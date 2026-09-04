from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


_MAX_PROCESSES = 32
_MAX_TRANSCRIPT_CHARS = 100_000
_MAX_DELTA_CHARS = 20_000
_DEFAULT_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 3600
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")


def safe_process_environment() -> dict[str, str]:
    output: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            continue
        output[name] = value
    return output


def validate_argv(raw_argv: object) -> tuple[str, ...]:
    if not isinstance(raw_argv, list) or not raw_argv:
        raise ValueError("argv must be a non-empty array")
    argv = tuple(str(value) for value in raw_argv)
    if len(argv) > 256:
        raise ValueError("argv contains too many entries")
    if any(not value for value in argv):
        raise ValueError("argv entries must not be empty")
    if any(len(value) > 16_000 for value in argv):
        raise ValueError("an argv entry is too long")
    return argv


def validate_timeout(value: object, *, default: int = _DEFAULT_TIMEOUT_SECONDS) -> int:
    timeout = int(value or default)
    if not 1 <= timeout <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be within 1..{_MAX_TIMEOUT_SECONDS}")
    return timeout


def _bounded(value: str, limit: int = _MAX_DELTA_CHARS) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    return text[-limit:], True


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    process_id: str
    argv: tuple[str, ...]
    cwd: str
    permission_mode: str
    running: bool
    returncode: int | None
    stdout: str
    stderr: str
    stdout_delta: str = ""
    stderr_delta: str = ""
    timed_out: bool = False
    output_truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "process_id": self.process_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "permission_mode": self.permission_mode,
            "running": self.running,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_delta": self.stdout_delta,
            "stderr_delta": self.stderr_delta,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
        }


class ManagedProcess:
    def __init__(
        self,
        *,
        process_id: str,
        session_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        permission_mode: str,
        process: subprocess.Popen[str],
        timeout_seconds: int,
    ) -> None:
        self.process_id = process_id
        self.session_id = session_id
        self.argv = argv
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.process = process
        self.timeout_seconds = timeout_seconds
        self.started_monotonic = time.monotonic()
        self._lock = threading.RLock()
        self._stdout = ""
        self._stderr = ""
        self._stdout_cursor = 0
        self._stderr_cursor = 0
        self._stdout_truncated = False
        self._stderr_truncated = False
        self._timed_out = False
        self._stdout_thread = self._reader_thread(process.stdout, "stdout")
        self._stderr_thread = self._reader_thread(process.stderr, "stderr")

    def _reader_thread(self, pipe, stream: str) -> threading.Thread | None:
        if pipe is None:
            return None

        def read_loop() -> None:
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        return
                    self._append(stream, chunk)
            finally:
                try:
                    pipe.close()
                except OSError:
                    pass

        thread = threading.Thread(
            target=read_loop,
            name=f"loom-{self.process_id}-{stream}",
            daemon=True,
        )
        thread.start()
        return thread

    def _append(self, stream: str, chunk: str) -> None:
        with self._lock:
            if stream == "stdout":
                text = self._stdout + chunk
                removed = max(0, len(text) - _MAX_TRANSCRIPT_CHARS)
                if removed:
                    self._stdout_truncated = True
                    text = text[removed:]
                    self._stdout_cursor = max(0, self._stdout_cursor - removed)
                self._stdout = text
            else:
                text = self._stderr + chunk
                removed = max(0, len(text) - _MAX_TRANSCRIPT_CHARS)
                if removed:
                    self._stderr_truncated = True
                    text = text[removed:]
                    self._stderr_cursor = max(0, self._stderr_cursor - removed)
                self._stderr = text

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def _enforce_timeout(self) -> None:
        if not self.running:
            return
        if time.monotonic() - self.started_monotonic < self.timeout_seconds:
            return
        with self._lock:
            self._timed_out = True
        self.terminate_tree()

    def drain_output(self) -> tuple[str, str, bool]:
        self._enforce_timeout()
        with self._lock:
            stdout = self._stdout[self._stdout_cursor :]
            stderr = self._stderr[self._stderr_cursor :]
            self._stdout_cursor = len(self._stdout)
            self._stderr_cursor = len(self._stderr)
        stdout, stdout_cut = _bounded(stdout)
        stderr, stderr_cut = _bounded(stderr)
        return stdout, stderr, stdout_cut or stderr_cut

    def snapshot(self, *, drain_delta: bool = False) -> ProcessSnapshot:
        self._enforce_timeout()
        stdout_delta = ""
        stderr_delta = ""
        delta_truncated = False
        if drain_delta:
            stdout_delta, stderr_delta, delta_truncated = self.drain_output()
        with self._lock:
            stdout = self._stdout
            stderr = self._stderr
            transcript_truncated = self._stdout_truncated or self._stderr_truncated
            timed_out = self._timed_out
        return ProcessSnapshot(
            process_id=self.process_id,
            argv=self.argv,
            cwd=str(self.cwd),
            permission_mode=self.permission_mode,
            running=self.running,
            returncode=self.process.poll(),
            stdout=stdout,
            stderr=stderr,
            stdout_delta=stdout_delta,
            stderr_delta=stderr_delta,
            timed_out=timed_out,
            output_truncated=transcript_truncated or delta_truncated,
        )

    def write_stdin(self, text: str) -> None:
        payload = str(text)
        if len(payload) > 256_000:
            raise ValueError("stdin exceeds 256,000 characters")
        if not self.running or self.process.stdin is None:
            raise RuntimeError("process stdin is not available")
        try:
            self.process.stdin.write(payload)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RuntimeError("process stdin is closed") from exc

    def interrupt(self) -> None:
        if not self.running:
            return
        try:
            if os.name == "nt":
                ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                os.kill(self.process.pid, ctrl_break)
            else:
                os.killpg(self.process.pid, signal.SIGINT)
        except (OSError, ProcessLookupError):
            pass

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        if not self.running:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while self.running and time.monotonic() < deadline:
            time.sleep(0.02)
        if self.running:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def wait(
        self,
        *,
        cancel_check: Callable[[], bool] | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> ProcessSnapshot:
        while self.running:
            if cancel_check is not None and cancel_check():
                self.terminate_tree()
                break
            self._enforce_timeout()
            stdout_delta, stderr_delta, _ = self.drain_output()
            if on_output is not None and (stdout_delta or stderr_delta):
                on_output(stdout_delta, stderr_delta)
            if self.running:
                time.sleep(0.02)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=0.25)
        stdout_delta, stderr_delta, _ = self.drain_output()
        if on_output is not None and (stdout_delta or stderr_delta):
            on_output(stdout_delta, stderr_delta)
        return self.snapshot()


class ProcessStore:
    """In-memory lifecycle manager for commands that may span model turns."""

    def __init__(self, *, max_processes: int = _MAX_PROCESSES) -> None:
        self.max_processes = max(1, int(max_processes))
        self._lock = threading.RLock()
        self._processes: dict[str, ManagedProcess] = {}
        self._order: list[str] = []

    def _prune_finished(self) -> None:
        with self._lock:
            while len(self._processes) >= self.max_processes:
                removable = next(
                    (
                        process_id
                        for process_id in self._order
                        if process_id in self._processes
                        and not self._processes[process_id].running
                    ),
                    None,
                )
                if removable is None:
                    raise RuntimeError("process store is full; terminate an active process first")
                self._processes.pop(removable, None)
                self._order = [item for item in self._order if item != removable]

    def start(
        self,
        *,
        session_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        permission_mode: str,
        timeout_seconds: int,
        stdin_text: str = "",
    ) -> ManagedProcess:
        self._prune_finished()
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                bufsize=1,
                shell=False,
                env=safe_process_environment(),
                **kwargs,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Executable not found: {argv[0]}") from exc
        process_id = f"proc-{uuid.uuid4().hex[:12]}"
        managed = ManagedProcess(
            process_id=process_id,
            session_id=session_id,
            argv=argv,
            cwd=cwd,
            permission_mode=str(permission_mode),
            process=process,
            timeout_seconds=timeout_seconds,
        )
        with self._lock:
            self._processes[process_id] = managed
            self._order.append(process_id)
        if stdin_text:
            managed.write_stdin(stdin_text)
        return managed

    def get(self, process_id: str, *, session_id: str) -> ManagedProcess:
        key = str(process_id or "").strip()
        with self._lock:
            managed = self._processes.get(key)
        if managed is None:
            raise KeyError(f"unknown process: {key}")
        if managed.session_id != str(session_id):
            raise PermissionError("process belongs to a different Loom session")
        return managed

    def run(
        self,
        *,
        session_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        permission_mode: str,
        timeout_seconds: int,
        stdin_text: str = "",
        cancel_check: Callable[[], bool] | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> ProcessSnapshot:
        managed = self.start(
            session_id=session_id,
            argv=argv,
            cwd=cwd,
            permission_mode=permission_mode,
            timeout_seconds=timeout_seconds,
            stdin_text=stdin_text,
        )
        return managed.wait(cancel_check=cancel_check, on_output=on_output)

    def list_for_session(self, session_id: str) -> tuple[ProcessSnapshot, ...]:
        with self._lock:
            processes = [
                self._processes[process_id]
                for process_id in self._order
                if process_id in self._processes
                and self._processes[process_id].session_id == str(session_id)
            ]
        return tuple(process.snapshot() for process in processes)

    def terminate_session(self, session_id: str) -> int:
        with self._lock:
            processes = [
                process
                for process in self._processes.values()
                if process.session_id == str(session_id) and process.running
            ]
        for process in processes:
            process.terminate_tree()
        return len(processes)

    def terminate_all(self) -> None:
        with self._lock:
            processes = tuple(self._processes.values())
        for process in processes:
            process.terminate_tree()


__all__ = [
    "ManagedProcess",
    "ProcessSnapshot",
    "ProcessStore",
    "safe_process_environment",
    "validate_argv",
    "validate_timeout",
]
