"""Validate durable evidence references without interpreting model conclusions."""
from datetime import datetime
import json

from .contracts import AgentEventKind as Event, ToolEffect
from .tools import AgentTool, ToolResult

EVIDENCE_REFS_SCHEMA = {"type": "array", "minItems": 1, "maxItems": 64, "items": {
    "oneOf": [
        {"type": "object", "properties": {"call_id": {"type": "string", "minLength": 1,
         "description": "ID of an executed tool call in this session, including remote commands. Use the call that observed or verified the result."}},
         "required": ["call_id"], "additionalProperties": False},
        {"type": "object", "properties": {"path": {"type": "string", "minLength": 1,
         "description": "Existing local file inside the active workspace. For remote or outside-workspace files, cite the verifying tool call_id instead."}},
         "required": ["path"], "additionalProperties": False}]}}


def resolve_evidence(store, context, refs):
    events = store.events(context.session_id)
    invalid, facts = [], []
    for ref in refs:
        if not isinstance(ref, dict) or len(ref) != 1:
            invalid.append({"reference": ref, "reason": "expected call_id or workspace path"})
            continue
        if "call_id" in ref:
            call_id = ref["call_id"]
            results = [e for e in events if e.data.get("call_id") == call_id
                       and e.kind in {Event.TOOL_COMPLETED, Event.TOOL_FAILED}]
            result = results[-1] if results else None
            started = any(e.kind is Event.TOOL_STARTED and e.data.get("call_id") == call_id
                          and result is not None and e.turn_id == result.turn_id for e in events)
            result_data = result.data.get("data") if result is not None else None
            not_executed = isinstance(result_data, dict) and result_data.get("execution_status") == "not_executed"
            if result is None or not started or not_executed:
                invalid.append({"reference": ref, "reason": "call has no executed result in this session"})
                continue
            facts.append({"call_id": call_id, "event_id": result.event_id, "turn_id": result.turn_id,
                          "recorded_at": result.created_at, "tool": result.data.get("tool"),
                          "result_ok": result.data.get("ok")})
        elif "path" in ref:
            try:
                path = context.resolve_workspace_path(ref["path"])
                if not path.is_file():
                    raise ValueError("workspace file does not exist")
                stat = path.stat()
                facts.append({"path": str(path.relative_to(context.workspace.resolve())),
                              "size_bytes": stat.st_size, "modified_at_ns": stat.st_mtime_ns})
            except (ValueError, OSError, TypeError) as exc:
                invalid.append({"reference": ref, "reason": str(exc)})
        else:
            invalid.append({"reference": ref, "reason": "expected call_id or workspace path"})
    return facts, invalid


def rejected_evidence(invalid):
    return ToolResult(False, "Evidence references are invalid; no state was changed.",
                      {"execution_status": "not_executed", "invalid_references": invalid,
                       "recovery": "For remote or outside-workspace results, cite the executed verification call as {\"call_id\": \"...\"}, not the target path. Recover missing IDs with read_durable_tool_result(recent=5), inspect the relevant result, and retry with valid references. Do not rerun unchanged work just to create a local evidence file."})


def check_ledger(events):
    return [{"event_id": e.event_id, "turn_id": e.turn_id, "recorded_at": e.created_at, **e.data}
            for e in events if e.kind is Event.CHECK_RECORDED]


def record_check_tool(store):
    def record(context, arguments):
        refs = arguments["evidence_refs"]
        if not refs:
            return rejected_evidence([{"reason": "check requires evidence_refs"}])
        facts, invalid = resolve_evidence(store, context, refs)
        if invalid:
            return rejected_evidence(invalid)
        if context.emit_event is None:
            raise RuntimeError("durable check event service is unavailable")
        timestamps = []
        for fact in facts:
            if "recorded_at" in fact:
                try:
                    timestamps.append(datetime.fromisoformat(fact["recorded_at"].replace("Z", "+00:00")))
                except ValueError:
                    pass
        data = {**arguments, "evidence_facts": facts,
                "elapsed_seconds": (max(timestamps) - min(timestamps)).total_seconds() if len(timestamps) >= 2 else None}
        context.emit(Event.CHECK_RECORDED, data)
        return ToolResult(True, "Check recorded; verdict is the assistant's assessment, references are runtime verified.", data)
    return AgentTool("record_check", "Record a test or verification assessment backed by executed calls or workspace files. Runtime calculates elapsed time between referenced result events; it does not verify your verdict.",
        {"type": "object", "additionalProperties": False, "properties": {
            "case_id": {"type": "string", "minLength": 1, "maxLength": 240},
            "expectation": {"type": "string", "minLength": 1, "maxLength": 4000},
            "observed": {"type": "string", "minLength": 1, "maxLength": 4000},
            "verdict": {"enum": ["passed", "failed", "blocked", "not_covered"]},
            "evidence_refs": EVIDENCE_REFS_SCHEMA},
         "required": ["case_id", "expectation", "observed", "verdict", "evidence_refs"]},
        record, effect=ToolEffect.READ_ONLY, supports_parallel_tool_calls=False)


def read_check_ledger_tool(store):
    def read(context, arguments):
        entries = check_ledger(store.events(context.session_id))
        if arguments.get("case_id"):
            entries = [entry for entry in entries if entry["case_id"] == arguments["case_id"]]
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 50)
        selected = entries[offset:offset + limit]
        data = {"entries": selected, "total": len(entries), "next_offset": offset + len(selected)}
        return ToolResult(True, json.dumps(data, ensure_ascii=False), data)
    return AgentTool("read_check_ledger", "Read durable verification records for this session, including runtime evidence facts and assistant assessments. To export a workspace deliverable, pass the returned entries to append_workspace_jsonl.",
        {"type": "object", "additionalProperties": False, "properties": {
            "case_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
        read, effect=ToolEffect.READ_ONLY)
