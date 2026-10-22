from scripts.latency_report import report


def event(kind, second, **data):
    return dict(kind=kind, turn_id="t", event_id=f"{kind}-{second}",
                created_at=f"2026-10-22T00:00:{second:06.3f}+00:00", data=data)


def test_attempts_usage_and_overlapping_tools_are_not_conflated():
    events = [event("turn_started", 0),
        event("model_requested", 1, step_id="s", request_preparation_ms=100, model="fixed",
              estimated_input_tokens_after=500),
        event("model_response_rejected", 3, step_id="s", reason="transport_error"),
        event("model_requested", 4, step_id="s", request_preparation_ms=0,
              request_preparation_reused=True, retry_wait_ms=1000, model="fixed"),
        event("model_response", 9, step_id="s", model_execution_ms=5000,
              stream_timing=dict(execution_ms=4900, first_chunk_ms=10, first_content_ms=900, queue_wait_ms=100),
              usage=dict(input_tokens=600, cached_input_tokens=550, output_tokens=20, total_tokens=620)),
        event("tool_requested", 9, call_id="a"),
        event("tool_started", 10, call_id="a", step_id="s"),
        event("tool_started", 10, call_id="b", step_id="s"),
        event("tool_completed", 12, call_id="b", ok=True),
        event("tool_completed", 13, call_id="a", ok=True), event("turn_completed", 14)]
    result = report(events, "t")
    a, b = result["models"]
    assert a["execution_ms"] == 2000
    assert a["generation_ms"] is None
    assert a["estimated_input_tokens"] == 500
    assert b["preparation_ms"] == 0 and b["preceding_retry_wait_ms"] == 1000
    assert b["generation_ms"] == 4000 and b["executor_queue_wait_ms"] == 100
    assert b["provider_usage"]["input_tokens"] == 600
    assert b["provider_usage"]["cached_input_tokens"] == 550
    assert b["actual_cost"] is None
    assert [t["execution_ms"] for t in result["tools"]] == [3000, 2000]
    assert result["tools"][0]["permission_and_queue_ms"] == 1000
    assert result["wall_ms"] == 14000


def test_legacy_and_missing_evidence_stays_unknown():
    result = report([event("model_requested", 1, step_id="s", request_preparation_ms=300),
                     event("model_response_rejected", 2, step_id="s"),
                     event("model_requested", 3, step_id="s", request_preparation_ms=300)], "t")
    assert result["wall_ms"] is None
    assert result["models"][1]["preparation_ms"] is None
    assert result["models"][1]["legacy_preparation_ambiguous"]
    assert result["models"][1]["status"] == "unclosed"
    assert all(v is None for v in result["models"][0]["provider_usage"].values())
