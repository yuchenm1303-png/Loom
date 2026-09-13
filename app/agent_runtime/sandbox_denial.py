from __future__ import annotations

from .tools import ToolResult


_SANDBOX_DENIED_KEYWORDS = (
    "operation not permitted",
    "permission denied",
    "read-only file system",
    "seccomp",
    "sandbox",
    "landlock",
    "failed to write file",
)


def is_likely_sandbox_denied_result(result: ToolResult) -> bool:
    """Port Codex's centralized sandbox-denial heuristic to Loom exec output.

    A non-zero exit or stderr alone is never enough. The result must carry an
    enforced sandbox snapshot and one of Codex's well-known denial markers in
    the command's stdout/stderr. Loom's human-readable ``content`` deliberately
    does not participate because it contains harness metadata such as
    ``sandbox=bubblewrap`` that would make every failed sandboxed command look
    like a denial.
    """

    if result.ok:
        return False
    data = result.data
    sandbox = data.get("sandbox")
    if not isinstance(sandbox, dict) or sandbox.get("enforced") is not True:
        return False
    exit_code = data.get("returncode")
    if exit_code in {None, 0}:
        return False

    sections = (
        str(data.get("stderr") or ""),
        str(data.get("stdout") or ""),
        str(data.get("aggregated_output") or ""),
    )
    return any(
        needle in section.casefold()
        for section in sections
        for needle in _SANDBOX_DENIED_KEYWORDS
    )


def classify_exec_sandbox_denial(result: ToolResult) -> ToolResult:
    """Promote one proven/likely sandbox failure into Loom's typed result shape."""

    if result.data.get("failure_kind") == "sandbox":
        return result
    if not is_likely_sandbox_denied_result(result):
        return result
    data = dict(result.data)
    data.update(
        {
            "failure_kind": "sandbox",
            "sandbox_failure": "denied",
            "sandbox_detection": "codex_heuristic",
        }
    )
    return ToolResult(ok=False, content=result.content, data=data)


__all__ = ["classify_exec_sandbox_denial", "is_likely_sandbox_denied_result"]
