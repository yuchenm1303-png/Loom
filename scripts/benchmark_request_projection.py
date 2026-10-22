"""Paired request render + token estimate microbenchmark, never calls a provider.

python scripts/benchmark_request_projection.py --baseline-ref COMMIT [--session PATH]
Optional session.json replays only its canonical history, not the live task.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tempfile
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai import AIMessage, MessageRole
from app.agent_runtime import context_composer as current
from app.agent_runtime.context_budget import estimate_tokens, _measurements
from app.agent_runtime.storage import _message_from_dict, _message_to_dict


def runtime_comparison(baseline, runs):
    """Fixed local read task through real runtime/tools, scripted model only."""
    from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.agent_runtime.builtin_tools import builtin_read_only_tools
    from app.ai import ModelResponse, ToolCall
    original = current.render_request
    samples = {"before": [], "after": []}
    with tempfile.TemporaryDirectory(prefix="loom-latency-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "evidence.txt").write_text("verification-code: LOOM-42", encoding="utf-8")
        class Platform:
            def __init__(self):
                self.requests = []
            def execute_chat(self, profile, request):
                assert profile == "agent.fast"
                self.requests.append(request)
                if len(self.requests) == 1:
                    return ModelResponse(tool_calls=(ToolCall("read-evidence", "read_workspace_text", {"path": "evidence.txt"}),))
                evidence = [m for m in request.messages if m.tool_call_id == "read-evidence"]
                assert len(evidence) == 1 and "verification-code: LOOM-42" in evidence[0].content
                return ModelResponse(text="LOOM-42")
        try:
            for index in range(runs):
                pairs = [("before", baseline.render_request), ("after", original)]
                for name, renderer in pairs if index % 2 == 0 else reversed(pairs):
                    current.render_request = renderer
                    platform = Platform()
                    runtime = AgentRuntime(platform=platform, tools=builtin_read_only_tools(), store=FileAgentSessionStore(root / f"state-{name}-{index}"),
                        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
                    try:
                        session = runtime.create_session("agent.fast", workspace_dir=workspace, permission_mode="read-only")
                        start = time.perf_counter()
                        result = runtime.start_turn(session.session_id, "Read evidence.txt and return its verification code.")
                        wall = (time.perf_counter() - start) * 1000
                        events = runtime.store.events(session.session_id)
                        completed = [e for e in events if e.kind.value == "tool_completed"]
                        assert result.status.value == "completed" and result.final_text == "LOOM-42", (result.status, result.error, result.final_text)
                        assert len(completed) == 1 and completed[0].data["ok"]
                        assert completed[0].data["call_id"] == "read-evidence"
                        assert len(platform.requests) == 2
                        samples[name].append(wall)
                    finally:
                        runtime.close()
        finally:
            current.render_request = original
    return {"kind": "deterministic_runtime_task_not_live_provider", "runs_per_variant": runs,
            "successes": {k: len(v) for k, v in samples.items()}, "exact_answer": "LOOM-42",
            "verified_tool_calls_per_run": 1, "model_requests_per_run": 2,
            "median_wall_ms": {k: round(statistics.median(v), 3) for k, v in samples.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--runs", type=int, default=21)
    parser.add_argument("--runtime-runs", type=int, default=0)
    args = parser.parse_args()
    if args.runs < 1 or args.runtime_runs < 0:
        parser.error("--runs must be positive; --runtime-runs must be nonnegative")
    baseline = ModuleType("app.agent_runtime._projection_baseline")
    baseline.__package__ = "app.agent_runtime"
    source = subprocess.check_output(["git", "show", f"{args.baseline_ref}:app/agent_runtime/context_composer.py"])
    exec(compile(source, "baseline_context_composer.py", "exec"), baseline.__dict__)
    if args.session:
        raw = json.loads(args.session.read_text(encoding="utf-8"))
        history = tuple(_message_from_dict(m) for m in raw["messages"])
    else:
        history = tuple(AIMessage(role=role, content=f"evidence-{i} 中文 " * 1000)
                        for i in range(120) for role in (MessageRole.USER, MessageRole.ASSISTANT))
    runtime = SimpleNamespace()
    session = SimpleNamespace(session_id="replay", request_context_frames=[])
    def run(module):
        rendered = module.render_request(runtime, session, (), history)
        return rendered, estimate_tokens(rendered)
    results = {"before": [], "after": []}
    outputs = {}
    # Alternate ordering, warm the same caches before each measured sample.
    for index in range(args.runs):
        pairs = [("before", baseline), ("after", current)]
        for name, module in pairs if index % 2 == 0 else reversed(pairs):
            _measurements.clear()
            run(module)
            start = time.perf_counter()
            outputs[name] = run(module)
            results[name].append((time.perf_counter() - start) * 1000)
            if not args.session:
                assert outputs[name][0] == list(history)
        assert outputs["before"] == outputs["after"]
    assert outputs["before"] == outputs["after"]
    payload = json.dumps([_message_to_dict(m) for m in outputs["after"][0]], ensure_ascii=False, sort_keys=True)
    result = {"kind": "history_projection_microbenchmark", "baseline_ref": args.baseline_ref,
        "runs": args.runs, "messages": len(history), "estimated_tokens": outputs["after"][1],
        "same_output_and_estimate": True, "output_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "median_ms": {k: round(statistics.median(v), 3) for k, v in results.items()},
        "live_task_latency_verified": False}
    if args.runtime_runs:
        result["runtime_task"] = runtime_comparison(baseline, args.runtime_runs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
