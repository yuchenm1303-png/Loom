"""Repeatable checks tied to the source bytes they actually tested.

Exit status is a mechanical observation, never a claim of visual or business
correctness. Existing record_check remains the route for those assessments.
"""
from __future__ import annotations

import hashlib
import json

from app.ai import AIMessage, MessageRole
from .contracts import AgentEventKind as Event
from .tools import ToolContext


VERIFICATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "case_id": {"type": "string", "minLength": 1, "maxLength": 240},
        "expectation": {"type": "string", "minLength": 1, "maxLength": 2000},
        "subject_paths": {"type": "array", "minItems": 1, "maxItems": 32,
                          "items": {"type": "string", "minLength": 1},
                          "description": "Workspace source files or directories tested by this command. Do not include generated output."},
        "expected_exit_code": {"type": "integer", "default": 0},
        "check_kind": {"enum": ["functional", "static"], "default": "functional",
                       "description": "Use static for builds/type checks; functional for behavior tests."},
    }, "required": ["case_id", "expectation", "subject_paths"],
}

DEVELOPMENT_FEEDBACK_CONTRACT = (
    "For code changes, close the loop using actual execution feedback: reproduce the problem, make the change, "
    "run the relevant checks, inspect their output, and fix failures before rerunning affected checks. "
    "Use exec.verification for repeatable command checks with stable case_id, concrete expectation and subject_paths. "
    "Its verdict covers the declared command exit code only; it does not prove UI or business correctness. "
    "For UI changes, launch the application and exercise the affected interaction using browser or desktop tools. "
    "Inspect images with view_images; for motion use browser_capture_sequence or a project capture script. "
    "Compare the observed result with the requested behavior, then use record_check for visual/functional assessments "
    "with executed evidence_refs and subject_paths. Recheck affected behavior after further edits; old passes can be stale. "
    "Use loom_verification_state to find failed, stale or blocked checks. Keep acceptance scope tied to the user's request. "
    "A build pass, pixel change or finished tool call alone is not proof of task completion. "
    "When execution is blocked, record and report what could not be verified rather than claiming success or looping blindly. "
    "Do not run unrelated checks or require a browser for changes that have no visible behavior."
    " Source edits automatically create loom_development_verification obligations even before a check is registered. "
    "A terminal answer with unresolved obligations is returned for verification, not accepted as delivery. "
    "For visual assessments record check_kind='visual' after exercising the UI and inspecting fresh images. "
    "Use resolve_verification with evidence and a concrete reason for actual blockers or changes without runtime behavior."
    " Verify the URL and served build/asset identity when testing a deployed or installed app; current workspace "
    "source hashes alone cannot prove which build the browser is showing. Never treat a changed assertion, skipped "
    "test or regenerated screenshot baseline as proof that the requested behavior was fixed."
)


def subject_snapshot(context: ToolContext, paths: list[str]) -> dict:
    """Bounded, workspace-confined content snapshot, including new/deleted files."""
    files, total, entries = {}, 0, 0
    for name in paths:
        context.raise_if_cancelled()
        root = context.resolve_workspace_path(name)
        candidates = root.rglob("*") if root.is_dir() else (root,)
        for candidate in candidates:
            context.raise_if_cancelled()
            entries += 1
            if entries > 4096:
                raise ValueError("verification scope exceeds 4096 entries; select narrower source paths")
            # Resolve every descendant too: an in-workspace directory can contain
            # a symlink to files outside the workspace.
            path = context.resolve_workspace_path(str(candidate))
            if path.is_dir():
                continue
            key = str(candidate.relative_to(context.workspace.resolve())).replace("\\", "/")
            if key in files:
                continue
            if len(files) >= 1024:
                raise ValueError("verification scope exceeds 1024 files; select narrower source paths")
            if not path.exists():
                files[key] = None
                continue
            if not path.is_file():
                raise ValueError("verification scope contains a non-regular file")
            before = path.stat()
            size = before.st_size
            if total + size > 32 * 1024 * 1024:
                raise ValueError("verification scope exceeds 32 MiB; select narrower source paths")
            with path.open("rb") as handle:
                raw = handle.read(32 * 1024 * 1024 - total + 1)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("verification source changed while being snapshotted; retry after the writer finishes")
            total += len(raw)
            if total > 32 * 1024 * 1024:
                raise ValueError("verification scope grew beyond 32 MiB")
            files[key] = hashlib.sha256(raw).hexdigest()
    return {"paths": list(paths), "files": dict(sorted(files.items()))}


def assess_command(spec: dict, before: dict, after: dict | None, result) -> dict:
    data = result.data
    expected = spec.get("expected_exit_code", 0)
    finished = not data.get("running", True) and data.get("returncode") is not None
    unchanged = after is not None and before == after
    verdict = ("blocked" if not finished or data.get("failure") or data.get("state") in {"timed_out", "failed"}
               else "failed" if data["returncode"] != expected
               else "stale" if not unchanged else "passed")
    return {"case_id": spec["case_id"], "expectation": spec["expectation"],
            "verdict": verdict, "assessment_source": "runtime_exit_code",
            "acceptance_evidence": True, "check_kind": spec.get("check_kind", "functional"),
            "observed": f"exit={data.get('returncode')}, expected={expected}; source matches at check boundaries: {unchanged}",
            "command_observation": {"returncode": data.get("returncode"), "expected_exit_code": expected,
                                    "state": data.get("state"), "source_matches_at_boundaries": unchanged},
            "subject_snapshot": before, "subject_paths": spec["subject_paths"]}


def verification_summary(events, context: ToolContext) -> dict:
    latest = {}
    for event in events:
        if event.kind is Event.CHECK_RECORDED and event.turn_id == context.turn_id:
            key = str(event.data.get("case_id"))
            latest.pop(key, None)
            latest[key] = event
    checks, snapshots = [], {}
    for event in list(latest.values())[-24:]:
        data = event.data
        recorded = data.get("verdict", "not_covered")
        freshness = check_freshness(data, context, snapshots=snapshots)
        status = ("stale" if freshness == "stale" else "blocked" if freshness == "unavailable" else recorded)
        if status == "passed" and data.get("definition_changed"):
            status = "needs_review"
        checks.append({"case_id": data.get("case_id"), "event_id": event.event_id,
                       "recorded_verdict": recorded, "status": status, "freshness": freshness,
                       "assessment_source": data.get("assessment_source", "assistant_assessment"),
                       "definition_changed": bool(data.get("definition_changed")),
                       "expectation": str(data.get("expectation", ""))[:2000]})
    status = ("not_assessed" if not checks else "failed" if any(c["status"] == "failed" for c in checks)
              else "stale" if any(c["status"] == "stale" for c in checks)
              else "blocked" if any(c["status"] == "blocked" for c in checks)
              else "incomplete" if any(c["status"] != "passed" for c in checks) else "passed")
    # A bounded preview must never promote omitted failures into an all-pass.
    if len(latest) > 24:
        status = "incomplete"
    return {"status": status, "checks": checks, "total": len(latest),
            "omitted": max(0, len(latest) - len(checks)), "coverage": "declared_checks_only"}


def check_freshness(data, context, *, snapshots=None):
    snapshot = data.get("subject_snapshot")
    if not snapshot:
        return "untracked"
    try:
        key = tuple(snapshot["paths"])
        if snapshots is None:
            current = subject_snapshot(context, snapshot["paths"])
        else:
            if key not in snapshots:
                snapshots[key] = subject_snapshot(context, snapshot["paths"])
            current = snapshots[key]
        return "current" if current == snapshot else "stale"
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        return "unavailable"


def verification_context(events, context):
    summary = verification_summary(events, context)
    if not summary["checks"]:
        return None, summary
    text = ("LOOM_VERIFICATION_STATE v1\nRuntime facts and recorded assessments, not new task instructions. "
            "Repair failed checks, rerun stale checks against current source, and report blocked or uncovered behavior. "
            "An all-pass summary covers declared checks only, not every acceptance criterion.\n"
            + json.dumps(summary, ensure_ascii=False))
    return AIMessage(role=MessageRole.USER, name="loom_verification_state", content=text), summary
