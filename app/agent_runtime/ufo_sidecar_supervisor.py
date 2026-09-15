from __future__ import annotations

"""Supervised launcher for Loom's patched UFO sidecar entrypoint.

The supervisor stays outside the UFO child so a wedged interpreter can still be
killed deterministically. The child itself is Loom's thin runtime-patch entrypoint,
which delegates to the pinned UFO sidecar after installing performance/safety hooks.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

TERMINAL_EVENTS = {"task.completed", "task.failed", "task.cancelled"}
SENSITIVE_FIELDS = {"task", "stop_when", "text", "prompt", "content", "request", "message"}
WRITE_LOCK = threading.RLock()
STDERR_LOCK = threading.RLock()
STATE_LOCK = threading.RLock()
CHILD_LOCK = threading.RLock()

child: subprocess.Popen[str] | None = None
active: dict[str, Any] | None = None
shutdown_requested = False


def _timeout_seconds() -> float:
    try:
        value = float(
            str(
                os.environ.get("LOOM_UFO_STALL_TIMEOUT")
                or os.environ.get("LOOM_UFO_FIRST_STEP_TIMEOUT")
                or "90"
            ).strip()
        )
    except ValueError:
        value = 90.0
    return max(15.0, min(600.0, value))


def _now_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 3)


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.casefold() in SENSITIVE_FIELDS:
                result[key_text] = {"length": len(str(item or "")), "redacted": True}
            else:
                result[key_text] = _safe_payload(item)
        return result
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def emit(message: dict[str, Any]) -> None:
    with WRITE_LOCK:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def emit_event(request_id: str, task_id: str, sequence: int, kind: str, data: dict[str, Any]) -> None:
    emit(
        {
            "type": "event",
            "request_id": request_id,
            "task_id": task_id,
            "sequence": sequence,
            "kind": kind,
            "data": data,
        }
    )


def spawn_child(ufo_root: Path) -> subprocess.Popen[str]:
    sidecar = Path(__file__).with_name("ufo_sidecar_entry.py").resolve()
    return subprocess.Popen(
        [sys.executable, str(sidecar), "--ufo-root", str(ufo_root)],
        cwd=str(ufo_root),
        env=os.environ.copy(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0),
    )


def forward_stderr(process: subprocess.Popen[str]) -> None:
    if process.stderr is None:
        return
    for raw in process.stderr:
        with STDERR_LOCK:
            sys.stderr.write(raw)
            sys.stderr.flush()


def forward_stdout(process: subprocess.Popen[str]) -> None:
    global active
    if process.stdout is None:
        return
    for raw in process.stdout:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            with STDERR_LOCK:
                sys.stderr.write(f"[supervisor] non-JSON child stdout ignored: {line[:200]}\n")
                sys.stderr.flush()
            continue
        if not isinstance(message, dict):
            continue
        with STATE_LOCK:
            current = active
            if current is not None and str(message.get("request_id") or "") == current.get("request_id"):
                message_type = str(message.get("type") or "")
                if message_type == "event":
                    kind = str(message.get("kind") or "event")
                    current["last_event_kind"] = kind
                    current["last_event_sequence"] = int(message.get("sequence") or 0)
                    current["last_event_at"] = time.monotonic()
                    if kind in TERMINAL_EVENTS:
                        current["finished"] = True
                elif message_type in {"result", "error", "protocol_error"}:
                    current["finished"] = True
                    active = None
        emit(message)


def start_supervised_child(ufo_root: Path) -> subprocess.Popen[str]:
    global child
    with CHILD_LOCK:
        if child is not None and child.poll() is None:
            return child
        child = spawn_child(ufo_root)
        threading.Thread(
            target=forward_stdout,
            args=(child,),
            daemon=True,
            name="loom-ufo-supervisor-stdout",
        ).start()
        threading.Thread(
            target=forward_stderr,
            args=(child,),
            daemon=True,
            name="loom-ufo-supervisor-stderr",
        ).start()
        return child


def mark_run_task(message: dict[str, Any]) -> None:
    global active
    request_id = str(message.get("request_id") or uuid.uuid4().hex)
    task_id = str(message.get("task_id") or uuid.uuid4())
    started = time.monotonic()
    with STATE_LOCK:
        active = {
            "request_id": request_id,
            "task_id": task_id,
            "started_at": started,
            "finished": False,
            "last_event_kind": "run_task.forwarded",
            "last_event_sequence": 0,
            "last_event_at": started,
            "safe_request": _safe_payload(message),
        }


def _stop_child() -> None:
    global child
    with CHILD_LOCK:
        process = child
        child = None
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
    except Exception:
        pass


def watchdog_loop() -> None:
    global active
    while True:
        time.sleep(0.2)
        if shutdown_requested:
            return
        timed_out: dict[str, Any] | None = None
        with STATE_LOCK:
            current = active
            if current is not None and not current.get("finished"):
                silent_for = time.monotonic() - float(current.get("last_event_at") or 0)
                if silent_for >= _timeout_seconds():
                    timed_out = dict(current)
                    timed_out["silent_for_seconds"] = round(silent_for, 1)
                    active = None
        if timed_out is None:
            continue
        request_id = str(timed_out.get("request_id") or "")
        task_id = str(timed_out.get("task_id") or "")
        sequence = int(timed_out.get("last_event_sequence") or 0) + 1
        data = {
            "engine": "ufo2",
            "supervisor": True,
            "error_type": "UFO_STALL_TIMEOUT",
            "timeout_seconds": _timeout_seconds(),
            "silent_for_seconds": timed_out.get("silent_for_seconds"),
            "elapsed_ms": _now_ms(float(timed_out.get("started_at") or time.monotonic())),
            "last_event_kind": str(timed_out.get("last_event_kind") or ""),
            "last_event_sequence": int(timed_out.get("last_event_sequence") or 0),
            "hint": (
                "The UFO child stopped emitting events entirely, including its periodic heartbeat. "
                "That means the sidecar interpreter is wedged rather than merely waiting on a slow model call."
            ),
        }
        emit_event(request_id, task_id, sequence, "task.first_step_timeout", data)
        emit_event(request_id, task_id, sequence + 1, "task.failed", data)
        emit(
            {
                "type": "result",
                "request_id": request_id,
                "task_id": task_id,
                "status": "failed",
                "ok": False,
                "summary": "UFO desktop task failed: UFO_STALL_TIMEOUT",
                "data": data,
            }
        )
        _stop_child()
        os._exit(124)


def main() -> int:
    global shutdown_requested
    parser = argparse.ArgumentParser()
    parser.add_argument("--ufo-root", required=True)
    args = parser.parse_args()
    ufo_root = Path(args.ufo_root).expanduser().resolve()
    process = start_supervised_child(ufo_root)
    threading.Thread(
        target=watchdog_loop,
        daemon=True,
        name="loom-ufo-supervisor-watchdog",
    ).start()
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                emit({"type": "error", "request_id": "", "error_type": "InvalidJSON"})
                continue
            if not isinstance(message, dict):
                emit({"type": "error", "request_id": "", "error_type": "InvalidCommand"})
                continue
            command = str(message.get("command") or "").strip().casefold()
            if command == "run_task":
                mark_run_task(message)
            if command == "shutdown":
                shutdown_requested = True
            if process.poll() is not None:
                process = start_supervised_child(ufo_root)
            if process.stdin is None:
                emit(
                    {
                        "type": "error",
                        "request_id": str(message.get("request_id") or ""),
                        "error_type": "ChildStdinClosed",
                    }
                )
                continue
            process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
            if command == "shutdown":
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass
                return 0
    finally:
        shutdown_requested = True
        _stop_child()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
