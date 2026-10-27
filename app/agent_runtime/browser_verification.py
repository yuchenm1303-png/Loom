"""Replay bounded browser actions and strict assertions through existing policy/tools."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace

from app.ai import ToolCall
from .contracts import AgentEventKind as E, ToolEffect
from .permissions import PermissionDecision
from .tools import AgentTool, ToolRouter, ToolResult, ToolExposure, validate_tool_arguments
from .verification import VERIFICATION_SCHEMA, subject_snapshot


def browser_run_check_tool(runtime):
    def run(context, arguments):
        context.raise_if_cancelled()
        store = runtime.browser_sessions
        browser_id = arguments["browser_id"]
        store.ensure_revision(context.session_id, browser_id, arguments["state_revision"])
        spec = arguments["verification"]
        before = subject_snapshot(context, spec["subject_paths"])
        allowed = {"browser_navigate", "browser_click", "browser_click_at", "browser_type", "browser_press",
                   "browser_hover", "browser_scroll", "browser_select", "browser_wait", "browser_state"}
        # Validate the entire recipe before the first action. In particular,
        # a child cannot substitute a browser id outside this approved recipe.
        for item in arguments["steps"]:
            if item["type"] == "action":
                if item["tool"] not in allowed:
                    raise ValueError("unsupported browser verification action")
                values = item["arguments"]
                if any(key in values for key in ("browser_id", "state_revision")):
                    raise ValueError("browser_id and state_revision are owned by the recipe")
                tool = runtime.tools.get(item["tool"])
                if tool is None:
                    raise ValueError("browser recipe requires an unavailable tool")
                bound = {"browser_id": browser_id, **values}
                if "state_revision" in tool.input_schema.get("properties", {}):
                    bound["state_revision"] = arguments["state_revision"]
                validate_tool_arguments(tool.input_schema, bound)
        steps, assertions, interacted = [], 0, False
        mode, rendered = arguments.get("mode", "interaction"), False
        operation_failure = {}
        screenshots = []
        parent = "browser-check-" + uuid.uuid4().hex[:12]
        step_context = context.service("execution_step")

        def execute(name, values):
            nonlocal operation_failure
            context.raise_if_cancelled()
            tool = runtime.tools.get(name)
            if tool is None:
                raise RuntimeError(f"required browser capability unavailable: {name}")
            values = {"browser_id": browser_id, **values}
            if "state_revision" in tool.input_schema.get("properties", {}):
                values["state_revision"] = store.snapshot(context.session_id, browser_id).state_revision
            call = ToolCall(f"{parent}-{len(steps)}", name, values)
            # Parent approval covers this exact declared recipe. Child tools still
            # cross schema, permission, account, URL and ownership enforcement.
            frozen = replace(step_context, tool_router=ToolRouter((tool,)))
            prepared = runtime.orchestrator.prepare(frozen, call, legacy_policy=runtime.policy)
            if prepared.decision is PermissionDecision.DENY:
                raise RuntimeError(f"browser recipe step denied: {prepared.reason}")
            context.emit(E.TOOL_STARTED, {"call_id": call.call_id, "tool": name, "effect": tool.effect.value,
                                          "parent_call_id": parent})
            result = runtime.orchestrator.execute(prepared, context, approval_granted=True)
            if result.data.get("failure_kind") == "browser_operation_unresolved":
                operation_failure = result.data
            # Do not persist full DOM, images, typed input or arbitrary page text.
            context.emit(E.TOOL_COMPLETED if result.ok else E.TOOL_FAILED,
                         {"call_id": call.call_id, "tool": name, "ok": result.ok,
                          "parent_call_id": parent, "content": "Browser verification recipe step.",
                          "data": {key: result.data[key] for key in ("browser_id", "state_revision", "path", "execution_status")
                                   if key in result.data}})
            steps.append({"tool": name, "call_id": call.call_id, "ok": result.ok})
            return result

        verdict, error = "passed", ""
        try:
            with store.operation(context.session_id, browser_id):
                for item in arguments["steps"]:
                    context.raise_if_cancelled()
                    if item["type"] == "assert":
                        result = execute("browser_eval", {"expression": item["expression"], "await_promise": True})
                        assertions += 1
                        if not result.ok or result.data.get("value") is not True or result.data.get("truncated"):
                            verdict, error = "failed", "Assertion did not return boolean true: " + item["label"]
                            steps[-1]["assertion_passed"] = False
                            break
                        steps[-1]["assertion_passed"] = True
                        continue
                    name = item["tool"]
                    result = execute(name, item["arguments"])
                    rendered |= result.ok and name in {"browser_navigate", "browser_state"}
                    interacted |= name not in {"browser_navigate", "browser_wait", "browser_state"}
                    if not result.ok:
                        verdict, error = "failed", "Browser action did not succeed: " + name
                        break
                # Failure screenshots are feedback too. Do not retry an action.
                result = (execute("browser_screenshot", {}) if not operation_failure
                          else ToolResult(False, "Browser operation unresolved", operation_failure))
                if result.ok:
                    screenshots.append({"path": result.data["path"], "label": "Browser acceptance result"})
                else:
                    verdict, error = "blocked", "Could not capture acceptance evidence"
        except Exception as exc:
            context.raise_if_cancelled()
            verdict, error = "blocked", f"{type(exc).__name__}: {exc}"[:2000]
        try:
            after = subject_snapshot(context, spec["subject_paths"])
        except (ValueError, OSError):
            after = None
        if verdict == "passed" and before != after:
            verdict, error = "stale", "Source changed during browser verification"
        exercised = rendered if mode == "render" else interacted
        if verdict == "passed" and (not assertions or not exercised):
            verdict, error = "failed", "Acceptance needs an exercised interaction and at least one strict assertion"
        visual = {}
        if screenshots:
            try:
                visual = runtime.visual_feedback.stage(context, screenshots)
            except (ValueError, OSError, RuntimeError) as exc:
                verdict, error = "blocked", f"Visual attachment unavailable: {exc}"[:2000]
        assessment = {"case_id": spec["case_id"], "expectation": spec["expectation"],
            "observed": error or f"{assertions} boolean assertions passed after exercising the UI",
            "verdict": verdict, "check_kind": "visual", "assessment_source": "runtime_browser_assertions",
            "acceptance_evidence": bool(assertions and exercised and screenshots), "subject_snapshot": before,
            "subject_paths": spec["subject_paths"]}
        import hashlib
        assessment["recipe_fingerprint"] = hashlib.sha256(json.dumps(
            {"steps": arguments["steps"], "expectation": spec["expectation"], "mode": mode}, sort_keys=True).encode()).hexdigest()
        return ToolResult(verdict == "passed", "Browser check: " + verdict + ". " + assessment["observed"]
                          + ". Inspect the attached result image; the assertion suite covers the declared recipe only.",
                          {"browser_id": browser_id, "verification": assessment, "steps": steps, **visual, **operation_failure})

    action_names = ["browser_navigate", "browser_click", "browser_click_at", "browser_type", "browser_press",
                    "browser_hover", "browser_scroll", "browser_select", "browser_wait", "browser_state"]
    browser_spec = {**VERIFICATION_SCHEMA, "properties": {k: v for k, v in VERIFICATION_SCHEMA["properties"].items()
                                                        if k not in {"check_kind", "expected_exit_code"}}}
    return AgentTool("browser_run_check", "Replay a bounded native-browser acceptance recipe and boolean page "
        "assertions. Uses the existing browser session and policy; it does not launch a server. Start the app first. "
        "Actions use known element indexes from this page; each step receives the actual current revision. "
        "Use assertions to confirm target identity/state before indexed actions; do not assume indexes survive "
        "navigation. Stop on first failure, capture evidence and fix before rerunning the same case_id. "
        "Use mode=render for static layout: refresh/navigate, assert geometry, then inspect the screenshot; no artificial click is needed. "
        "Default mode=interaction still requires an exercised interaction, strict assertion and screenshot. "
        "Discover with tool_search. Use ordinary adaptive browser tools when indexes/flows are not replayable.",
        {"type": "object", "additionalProperties": False, "properties": {
            "browser_id": {"type": "string", "minLength": 1},
            "state_revision": {"type": "integer", "minimum": 1},
            "verification": browser_spec,
            "mode": {"enum": ["render", "interaction"], "default": "interaction"},
            "steps": {"type": "array", "minItems": 2, "maxItems": 16, "items": {"oneOf": [
                {"type": "object", "additionalProperties": False, "properties": {
                    "type": {"const": "action"}, "tool": {"enum": action_names},
                    "arguments": {"type": "object", "maxProperties": 12}}, "required": ["type", "tool", "arguments"]},
                {"type": "object", "additionalProperties": False, "properties": {
                    "type": {"const": "assert"}, "label": {"type": "string", "minLength": 1, "maxLength": 500},
                    "expression": {"type": "string", "minLength": 1, "maxLength": 4000}}, "required": ["type", "label", "expression"]}]}}
        }, "required": ["browser_id", "state_revision", "verification", "steps"]},
        run, effect=ToolEffect.SENSITIVE, exposure=ToolExposure.DEFERRED)
