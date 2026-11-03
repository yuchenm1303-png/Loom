"""Native ConPTY x86 smoke: execute with a 32-bit Python on Windows.

In packaged mode LOOM_VERIFY_PACKAGED_X86_CONPTY=1, resolve the backend via
Loom's production _spawn_backend function rather than importing loose source.
The test does real input/output/resize and fails when only pipe fallback works.
"""
from __future__ import annotations

import importlib.util
import os
import struct
import sys
import threading
from pathlib import Path


def main() -> int:
    if os.name != "nt" or struct.calcsize("P") != 4:
        raise RuntimeError("Windows x86 ConPTY smoke requires 32-bit Windows Python")

    if os.environ.get("LOOM_VERIFY_PACKAGED_X86_CONPTY") == "1":
        from app.agent_runtime.process_runtime import _spawn_backend
        spawn = _spawn_backend
    else:
        source = Path(__file__).resolve().parents[2] / "app/agent_runtime/native_windows_conpty.py"
        spec = importlib.util.spec_from_file_location("loom_win32_native_conpty", source)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load native ConPTY implementation")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        spawn = lambda **kwargs: module.WindowsNativeConPtyProcessBackend(
            **{k: v for k, v in kwargs.items() if k != "pty"}
        )

    print("ConPTY-smoke: before spawn", flush=True)
    marker = "LOOM_NATIVE_X86_CONPTY_READY"
    session = spawn(
        argv=("cmd.exe", "/d", "/k"),
        cwd=Path.cwd(),
        env=dict(os.environ),
        pty=True,
        rows=24,
        cols=80,
    )
    print(f"ConPTY-smoke: spawned backend={session.name}, pid={session.pid}", flush=True)
    output: list[str] = []
    failures: list[str] = []
    found = threading.Event()

    def consume() -> None:
        try:
            while not found.is_set():
                chunk = session.read_stdout()
                if chunk:
                    output.append(chunk)
                    if marker in "".join(output):
                        found.set()
                        return
                elif session.poll() is not None:
                    return
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            found.set()

    reader = threading.Thread(target=consume, daemon=True, name="loom-x86-native-conpty-smoke")
    try:
        assert session.pty and session.name == "windows-native-conpty", (
            session.name, session.pty
        )
        print("ConPTY-smoke: before resize", flush=True)
        session.resize(30, 100)
        print("ConPTY-smoke: after resize", flush=True)
        reader.start()
        print("ConPTY-smoke: reader started, sending command", flush=True)
        session.write(f"echo {marker}\r\n")
        print("ConPTY-smoke: command sent; waiting output", flush=True)
        if not found.wait(15):
            raise AssertionError(
                f"ConPTY did not produce interactive output (pid={session.pid}, "
                f"poll={session.poll()}, output={''.join(output)[:500]!r})"
            )
        if failures:
            raise AssertionError("ConPTY reader failed: " + "; ".join(failures))
        assert marker in "".join(output)
        print("ConPTY-smoke: output verified; exiting", flush=True)
        session.write("exit\r\n")
        print("loom-native-x86-conpty-ok", session.name, "pid", session.pid)
        return 0
    finally:
        print("ConPTY-smoke: cleanup start", flush=True)
        if session.poll() is None:
            try:
                session.terminate_tree()
            except Exception:
                pass
        print("ConPTY-smoke: closing", flush=True)
        session.close()
        print("ConPTY-smoke: cleanup complete", flush=True)


if __name__ == "__main__":
    sys.exit(main())
