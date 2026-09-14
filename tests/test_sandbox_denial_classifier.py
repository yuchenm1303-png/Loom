from __future__ import annotations

from app.agent_runtime.sandbox_denial import (
    classify_exec_sandbox_denial,
    is_likely_sandbox_denied_result,
)
from app.agent_runtime.tools import ToolResult


def _result(*, enforced=True, returncode=1, stdout="", stderr="") -> ToolResult:
    return ToolResult(
        ok=returncode == 0,
        content=(
            f"process=proc-1 status=exited backend=pipe\n"
            f"sandbox=bubblewrap:workspace\n"
            f"exit={returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        ),
        data={
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "sandbox": {"enforced": enforced},
        },
    )


def test_sandbox_keyword_under_enforced_sandbox_is_promoted_to_typed_denial():
    raw = _result(stderr="Operation not permitted")

    assert is_likely_sandbox_denied_result(raw) is True
    typed = classify_exec_sandbox_denial(raw)
    assert typed.data["failure_kind"] == "sandbox"
    assert typed.data["sandbox_failure"] == "denied"
    assert typed.data["sandbox_detection"] == "codex_heuristic"


def test_plain_nonzero_exit_is_not_sandbox_denial_even_when_harness_content_names_sandbox():
    raw = _result(stderr="application-level validation failed")
    assert "sandbox=bubblewrap" in raw.content
    assert is_likely_sandbox_denied_result(raw) is False
    assert classify_exec_sandbox_denial(raw) is raw


def test_permission_text_without_enforced_sandbox_is_not_sandbox_denial():
    raw = _result(enforced=False, stderr="Permission denied")
    assert is_likely_sandbox_denied_result(raw) is False


def test_success_is_never_sandbox_denial_even_if_output_mentions_sandbox():
    raw = _result(returncode=0, stdout="sandbox setup complete")
    assert is_likely_sandbox_denied_result(raw) is False


def test_preclassified_platform_denial_is_preserved():
    raw = ToolResult(
        ok=False,
        content="platform denied",
        data={
            "failure_kind": "sandbox",
            "sandbox_failure": "denied",
            "sandbox_detection": "platform",
        },
    )
    assert classify_exec_sandbox_denial(raw) is raw
