"""Zero-model checks that ordinary development work runs in the OS sandbox, and that it stays contained.

Each problem real project runs met in the Windows sandbox - a scratch script it could
not run, a script an earlier turn wrote, tempfile.mkdtemp, a new 0o700 directory - cost
hundreds of thousands of model tokens to discover. This suite runs the same operations
through the production launch path (SandboxManager.prepare with the session scratch) in
seconds, before any model call, and also checks the boundaries: writes outside the
workspace, reads of ungranted files and network connections must fail.

Every Python check bounds itself with faulthandler, so a hang ends the child and the
launcher exits normally; killing an MXC launcher mid-run leaves ACEs for the next probe.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .contracts import PermissionMode

_PYTHON_PRELUDE = "import faulthandler, os, sys, tempfile, pathlib, subprocess\nfaulthandler.dump_traceback_later(45, exit=True)\n"


@dataclass(frozen=True)
class Case:
    name: str
    expect: str  # "ok": must succeed; "denied": must fail
    code: str = ""  # Python run in the workspace; exit 0 = the operation succeeded
    argv: tuple[str, ...] = ()  # a non-Python command instead
    needs: str = ""  # a host tool that must exist on PATH, else the case is skipped
    # Git needs read access to every folder above the workspace, which only the
    # elevated host preparation grants. Without it the case is a warning that names
    # the step for the user, not a sandbox defect.
    host_prep: bool = False


def cases(paths) -> list[Case]:
    outside, secret, old_script = (str(paths["outside"]), str(paths["secret"]), str(paths["old_script"]))
    new_scratch = str(paths["new_scratch"])
    return [
        Case("write and read a workspace file", "ok", "pathlib.Path('a.txt').write_text('x'); assert pathlib.Path('a.txt').read_text() == 'x'"),
        Case("nested directories", "ok", "os.makedirs('d1/d2/d3', exist_ok=True); pathlib.Path('d1/d2/d3/f').write_text('x')"),
        Case("unicode file name", "ok", "pathlib.Path('数据-ü.txt').write_text('x', encoding='utf-8')"),
        Case("0o700 directory", "ok", "os.mkdir('private', 0o700); pathlib.Path('private/f').write_text('x')"),
        Case("tempfile.mkdtemp in the workspace", "ok", "d = tempfile.mkdtemp(dir='.'); pathlib.Path(d, 'f').write_text('x')"),
        Case("tempfile default directory", "ok", "d = tempfile.mkdtemp(); pathlib.Path(d, 'f').write_text('x')"),
        Case("TemporaryDirectory with cleanup", "ok",
             "with tempfile.TemporaryDirectory() as d: pathlib.Path(d, 'f').write_text('x')"),
        Case("child process", "ok",
             "subprocess.run([sys.executable, '-c', \"open('child.txt','w').write('x')\"], check=True, timeout=60); "
             "assert pathlib.Path('child.txt').read_text() == 'x'"),
        Case("unittest with a temporary directory", "ok",
             "pathlib.Path('test_tmp.py').write_text('import tempfile, unittest, pathlib\\n"
             "class T(unittest.TestCase):\\n    def test(self):\\n        with tempfile.TemporaryDirectory() as d:\\n"
             "            pathlib.Path(d, \"f\").write_text(\"x\")\\n'); "
             "subprocess.run([sys.executable, '-m', 'unittest', '-q', 'test_tmp'], check=True, timeout=60)"),
        Case("write and run a script in this turn's scratch", "ok",
             f"p = pathlib.Path({new_scratch!r}, 'probe.py'); p.write_text('print(1)'); "
             "subprocess.run([sys.executable, str(p)], check=True, timeout=60)"),
        Case("run a script an earlier turn wrote", "ok",
             f"subprocess.run([sys.executable, {old_script!r}], check=True, timeout=60)"),
        Case("git repository", "ok", argv=("git", "-c", "user.name=t", "-c", "user.email=t@localhost", "init", "-q", "repo"),
             needs="git", host_prep=True),
        Case("node", "ok", argv=("node", "-e", "require('fs').writeFileSync('node.txt', 'x')"), needs="node"),
        Case("cmd", "ok", argv=("cmd", "/d", "/c", "echo ok"), needs="cmd"),
        Case("PowerShell", "ok", argv=("powershell", "-NoProfile", "-NonInteractive", "-Command", "Write-Output ok"),
             needs="powershell"),
        Case("write outside the workspace", "denied", f"pathlib.Path({outside!r}, 'escaped.txt').write_text('x')"),
        Case("read an ungranted host file", "denied", f"pathlib.Path({secret!r}).read_text()"),
        Case("network connection", "denied",
             "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5).close()"),
    ]


def run_suite(manager, root: Path, *, python: str = sys.executable,
              environment: dict[str, str] | None = None) -> list[dict]:
    """Run every case in a fresh layout under root; returns one result per case."""
    root = Path(root).resolve()
    if root.exists():
        shutil.rmtree(root)
    workspace, artifacts = root / "workspace", root / "state" / "run-artifacts"
    paths = {"outside": root / "outside", "secret": root / "outside" / "secret.txt",
             "old_script": artifacts / "earlier-turn" / "old.py", "new_scratch": artifacts / "this-turn"}
    for folder in (workspace, paths["outside"], paths["old_script"].parent, paths["new_scratch"]):
        folder.mkdir(parents=True)
    paths["secret"].write_text("host only", encoding="utf-8")
    paths["old_script"].write_text("print('earlier turn')", encoding="utf-8")
    # Development PYTHONPATH must not widen the grants (it once declared a whole checkout).
    env = {key: value for key, value in (environment or os.environ).items() if key.upper() != "PYTHONPATH"}
    results = []
    for case in cases(paths):
        if case.needs and not shutil.which(case.needs):
            results.append({"name": case.name, "expect": case.expect, "outcome": "skipped", "passed": True,
                            "detail": f"{case.needs} not on PATH"})
            continue
        if case.code:
            script = workspace / f"compat_{len(results)}.py"
            script.write_text(_PYTHON_PRELUDE + case.code + "\n", encoding="utf-8")
            argv = (python, "-B", script.name)
        else:
            argv = case.argv
        started = time.monotonic()
        command = manager.prepare(argv=argv, cwd=workspace, workspace=workspace,
                                  permission_mode=PermissionMode.WORKSPACE, environment=env, scratch=artifacts)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as output:
            process = subprocess.Popen(command.argv, cwd=command.cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                code = None  # left running: killing the launcher would strand ACEs
            output.seek(0)
            text = output.read()[-600:]
        succeeded = code == 0
        passed = succeeded if case.expect == "ok" else (code is not None and not succeeded)
        outcome = "timeout" if code is None else ("succeeded" if succeeded else f"failed ({code})")
        if not passed and case.host_prep and _missing_host_prep(manager, workspace):
            passed, outcome = None, "needs host preparation"
            text = "Run scripts/windows-sandbox-prep.ps1 as administrator (the user's decision) to grant the folders above workspaces."
        results.append({"name": case.name, "expect": case.expect, "passed": passed, "outcome": outcome,
                        "seconds": round(time.monotonic() - started, 1), "detail": "" if passed else text.strip()})
    # Escapes are judged by effect, not only exit codes.
    if (paths["outside"] / "escaped.txt").exists():
        results.append({"name": "nothing written outside the workspace", "expect": "denied", "passed": False,
                        "outcome": "escaped.txt exists", "detail": str(paths["outside"])})
    return results


def _missing_host_prep(manager, workspace) -> bool:
    status = getattr(manager, "windows_prep_status", None)
    if status is None:
        return False
    try:
        return bool(status(workspace=workspace).missing_ancestors)
    except Exception:
        return False


def failures(results: list[dict]) -> list[dict]:
    """Defects only: a warning (passed is None) needs a host step, not a fix."""
    return [item for item in results if item["passed"] is False]


def warnings(results: list[dict]) -> list[dict]:
    return [item for item in results if item["passed"] is None]


def main(argv=None) -> int:
    import argparse
    import json
    from .sandbox import SandboxManager
    parser = argparse.ArgumentParser(description="Run the sandbox compatibility suite (no model calls).")
    parser.add_argument("root", type=Path, help="Scratch folder for the suite; recreated")
    args = parser.parse_args(argv)
    manager = SandboxManager()
    snapshot = manager.snapshot(workspace=args.root, permission_mode=PermissionMode.WORKSPACE)
    if not snapshot.enforced:
        print(json.dumps({"enforced": False, "reason": snapshot.reason}))
        return 2
    results = run_suite(manager, args.root)
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    return 1 if failures(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
