from __future__ import annotations

import codecs
import errno
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol

from .sandbox import SandboxManager, SandboxSnapshot


_MAX_PROCESSES = 32
_MAX_TRANSCRIPT_CHARS = 100_000
_MAX_DELTA_CHARS = 20_000
_MAX_DURABLE_EVENT_CHARS = 100_000
_DEFAULT_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 3600
_MAX_STDIN_CHARS = 256_000
_MIN_TERMINAL_ROWS = 1
_MAX_TERMINAL_ROWS = 1000
_MIN_TERMINAL_COLS = 1
_MAX_TERMINAL_COLS = 2000
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")


class ProcessState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    TERMINATED = "terminated"
    TIMED_OUT = "timed_out"


def _secret_env_name(name: str) -> bool:
    upper = str(name).upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def safe_process_environment(overrides: Mapping[str, object] | None = None) -> dict[str, str]:
    """Build a child environment without inheriting or injecting secret-like variables."""

    output: dict[str, str] = {}
    for name, value in os.environ.items():
        if _secret_env_name(name):
            continue
        output[str(name)] = str(value)
    for raw_name, raw_value in (overrides or {}).items():
        name = str(raw_name)
        if not name or "=" in name or "\x00" in name:
            raise ValueError("environment variable names must be non-empty and cannot contain '=' or NUL")
        if _secret_env_name(name):
            raise ValueError(f"secret-like environment override is not allowed: {name}")
        value = str(raw_value)
        if "\x00" in value:
            raise ValueError(f"environment variable value contains NUL: {name}")
        if len(name) > 1024 or len(value) > 64_000:
            raise ValueError("environment override is too large")
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
    if any("\x00" in value for value in argv):
        raise ValueError("argv entries must not contain NUL")
    if any(len(value) > 16_000 for value in argv):
        raise ValueError("an argv entry is too long")
    return argv


def validate_timeout(value: object, *, default: int = _DEFAULT_TIMEOUT_SECONDS) -> int:
    if isinstance(value, bool):
        raise ValueError("timeout_seconds must be an integer")
    timeout = int(default if value is None else value)
    if not 1 <= timeout <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be within 1..{_MAX_TIMEOUT_SECONDS}")
    return timeout


def validate_terminal_size(rows: object = 24, cols: object = 80) -> tuple[int, int]:
    if isinstance(rows, bool) or isinstance(cols, bool):
        raise ValueError("rows and cols must be integers")
    parsed_rows = int(24 if rows is None else rows)
    parsed_cols = int(80 if cols is None else cols)
    if not _MIN_TERMINAL_ROWS <= parsed_rows <= _MAX_TERMINAL_ROWS:
        raise ValueError(f"rows must be within {_MIN_TERMINAL_ROWS}..{_MAX_TERMINAL_ROWS}")
    if not _MIN_TERMINAL_COLS <= parsed_cols <= _MAX_TERMINAL_COLS:
        raise ValueError(f"cols must be within {_MIN_TERMINAL_COLS}..{_MAX_TERMINAL_COLS}")
    return parsed_rows, parsed_cols


def _bounded(value: str, limit: int = _MAX_DELTA_CHARS) -> tuple[str, bool, int]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False, 0
    removed = text[:-limit]
    return text[-limit:], True, len(removed.encode("utf-8", errors="replace"))


class _BoundedBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.text = ""
        self.cursor = 0
        self.truncated = False
        self.dropped_bytes = 0

    def append(self, chunk: str) -> None:
        text = self.text + str(chunk)
        removed = max(0, len(text) - self.limit)
        if removed:
            prefix = text[:removed]
            self.truncated = True
            self.dropped_bytes += len(prefix.encode("utf-8", errors="replace"))
            text = text[removed:]
            self.cursor = max(0, self.cursor - removed)
        self.text = text

    def drain(self) -> tuple[str, bool, int]:
        delta = self.text[self.cursor :]
        self.cursor = len(self.text)
        return _bounded(delta)


class ProcessBackend(Protocol):
    name: str
    pty: bool

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def read_stdout(self) -> str: ...

    def read_stderr(self) -> str: ...

    def write(self, text: str) -> None: ...

    def close_stdin(self) -> None: ...

    def send_control(self, control: str) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def interrupt(self) -> None: ...

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None: ...

    def close(self) -> None: ...


class PipeProcessBackend:
    name = "pipe"
    pty = False

    def __init__(self, *, argv: tuple[str, ...], cwd: Path, env: Mapping[str, str]) -> None:
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        self._process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            shell=False,
            env=dict(env),
            **kwargs,
        )
        self._stdout_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._stderr_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    @staticmethod
    def _read(pipe, decoder) -> str:
        if pipe is None:
            return ""
        chunk = pipe.read(4096)
        if not chunk:
            return decoder.decode(b"", final=True)
        return decoder.decode(chunk, final=False)

    def read_stdout(self) -> str:
        return self._read(self._process.stdout, self._stdout_decoder)

    def read_stderr(self) -> str:
        return self._read(self._process.stderr, self._stderr_decoder)

    def write(self, text: str) -> None:
        stream = self._process.stdin
        if stream is None or stream.closed or self.poll() is not None:
            raise RuntimeError("process stdin is not available")
        try:
            stream.write(text.encode("utf-8"))
            stream.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RuntimeError("process stdin is closed") from exc

    def close_stdin(self) -> None:
        stream = self._process.stdin
        if stream is None or stream.closed:
            return
        try:
            stream.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def send_control(self, control: str) -> None:
        value = str(control).lower()
        if len(value) != 1 or not "a" <= value <= "z":
            raise ValueError("control must be one ASCII letter")
        self.write(chr(ord(value) - ord("a") + 1))

    def resize(self, rows: int, cols: int) -> None:
        raise RuntimeError("terminal resize requires a PTY process")

    def interrupt(self) -> None:
        if self.poll() is not None:
            return
        try:
            if os.name == "nt":
                ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                os.kill(self.pid, ctrl_break)
            else:
                os.killpg(self.pid, signal.SIGINT)
        except (OSError, ProcessLookupError):
            pass

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        if self.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            return
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while self.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if self.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        self.close_stdin()


class UnixPtyProcessBackend:
    name = "unix-pty"
    pty = True

    def __init__(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str],
        rows: int,
        cols: int,
    ) -> None:
        if os.name == "nt":
            raise RuntimeError("Unix PTY backend is not available on Windows")
        import fcntl
        import pty
        import struct
        import termios

        packed_size = struct.pack("HHHH", rows, cols, 0, 0)
        pid, master_fd = pty.fork()
        if pid == 0:  # pragma: no cover - this branch becomes the exec'd child
            try:
                # pty.fork() creates a new session and controlling terminal.
                fcntl.ioctl(0, termios.TIOCSWINSZ, packed_size)
                os.chdir(cwd)
                os.execvpe(argv[0], list(argv), dict(env))
            except BaseException as exc:
                try:
                    message = f"loom PTY exec failed: {type(exc).__name__}: {exc}\n"
                    os.write(2, message.encode("utf-8", errors="replace"))
                finally:
                    os._exit(127)
        self._pid = int(pid)
        self._master_fd = master_fd
        self._closed = False
        self._returncode: int | None = None
        self._poll_lock = threading.Lock()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        with self._poll_lock:
            if self._returncode is not None:
                return self._returncode
            try:
                waited_pid, status = os.waitpid(self._pid, os.WNOHANG)
            except ChildProcessError:
                return self._returncode if self._returncode is not None else 0
            if waited_pid == 0:
                return None
            if os.WIFEXITED(status):
                self._returncode = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                self._returncode = -os.WTERMSIG(status)
            else:
                self._returncode = 1
            return self._returncode

    def read_stdout(self) -> str:
        if self._closed:
            return ""
        try:
            chunk = os.read(self._master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return self._decoder.decode(b"", final=True)
            raise
        if not chunk:
            return self._decoder.decode(b"", final=True)
        return self._decoder.decode(chunk, final=False)

    def read_stderr(self) -> str:
        return ""

    def write(self, text: str) -> None:
        if self._closed or self.poll() is not None:
            raise RuntimeError("process PTY input is not available")
        view = memoryview(text.encode("utf-8"))
        while view:
            try:
                written = os.write(self._master_fd, view)
            except OSError as exc:
                raise RuntimeError("process PTY input is closed") from exc
            view = view[written:]

    def close_stdin(self) -> None:
        # The PTY master is duplex, so send the terminal EOF character instead
        # of closing the descriptor and losing pending output.
        if self.poll() is None and not self._closed:
            try:
                os.write(self._master_fd, b"\x04")
            except OSError:
                pass

    def send_control(self, control: str) -> None:
        value = str(control).lower()
        if len(value) != 1 or not "a" <= value <= "z":
            raise ValueError("control must be one ASCII letter")
        self.write(chr(ord(value) - ord("a") + 1))

    def resize(self, rows: int, cols: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        if self.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    def interrupt(self) -> None:
        if self.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        if self.poll() is not None:
            return
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while self.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if self.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._master_fd)
        except OSError:
            pass


class WindowsConPtyProcessBackend:
    name = "windows-conpty"
    pty = True

    def __init__(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str],
        rows: int,
        cols: int,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows ConPTY backend is only available on Windows")
        try:
            import winpty
        except ImportError as exc:  # pragma: no cover - exercised on Windows CI
            raise RuntimeError(
                "Windows PTY requires pywinpty>=3.0.5; install Loom on Windows with current dependencies"
            ) from exc
        self._process = winpty.PtyProcess.spawn(
            list(argv),
            cwd=str(cwd),
            env=dict(env),
            dimensions=(rows, cols),
            backend=winpty.Backend.ConPTY,
        )

    @property
    def pid(self) -> int:
        return int(self._process.pid)

    def poll(self) -> int | None:
        if self._process.isalive():
            return None
        status = self._process.exitstatus
        if status is None:
            try:
                status = self._process.pty.get_exitstatus()
            except Exception:
                status = None
        return 0 if status is None else int(status)

    def read_stdout(self) -> str:
        try:
            return str(self._process.read(4096) or "")
        except EOFError:
            return ""

    def read_stderr(self) -> str:
        return ""

    def write(self, text: str) -> None:
        if self.poll() is not None:
            raise RuntimeError("process PTY input is not available")
        try:
            self._process.write(text)
        except (EOFError, OSError, ValueError) as exc:
            raise RuntimeError("process PTY input is closed") from exc

    def close_stdin(self) -> None:
        if self.poll() is None:
            try:
                self._process.sendeof()
            except (EOFError, OSError, ValueError):
                pass

    def send_control(self, control: str) -> None:
        value = str(control).lower()
        if len(value) != 1 or not "a" <= value <= "z":
            raise ValueError("control must be one ASCII letter")
        if self.poll() is not None:
            raise RuntimeError("process PTY input is not available")
        self._process.sendcontrol(value)

    def resize(self, rows: int, cols: int) -> None:
        self._process.setwinsize(rows, cols)

    def interrupt(self) -> None:
        if self.poll() is None:
            try:
                self._process.sendintr()
            except (EOFError, OSError, ValueError):
                pass

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        if self.poll() is not None:
            return
        # Preserve the existing Windows process-tree guarantee. /T /F kills
        # root and descendants together, unlike a graceful root-only close.
        subprocess.run(
            ["taskkill", "/PID", str(self.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if self.poll() is None:
            try:
                self._process.terminate(force=True)
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._process.close(force=False)
        except Exception:
            pass


def _spawn_backend(
    *,
    argv: tuple[str, ...],
    cwd: Path,
    env: Mapping[str, str],
    pty: bool,
    rows: int,
    cols: int,
) -> ProcessBackend:
    if not pty:
        return PipeProcessBackend(argv=argv, cwd=cwd, env=env)
    if os.name == "nt":
        return WindowsConPtyProcessBackend(argv=argv, cwd=cwd, env=env, rows=rows, cols=cols)
    return UnixPtyProcessBackend(argv=argv, cwd=cwd, env=env, rows=rows, cols=cols)


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    process_id: str
    argv: tuple[str, ...]
    cwd: str
    permission_mode: str
    state: ProcessState
    running: bool
    returncode: int | None
    stdout: str
    stderr: str
    sandbox: SandboxSnapshot
    backend: str
    pty: bool
    rows: int | None = None
    cols: int | None = None
    stdout_delta: str = ""
    stderr_delta: str = ""
    timed_out: bool = False
    output_truncated: bool = False
    dropped_bytes: int = 0
    delta_dropped_bytes: int = 0
    failure: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "process_id": self.process_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "permission_mode": self.permission_mode,
            "state": self.state.value,
            "running": self.running,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_delta": self.stdout_delta,
            "stderr_delta": self.stderr_delta,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
            "dropped_bytes": self.dropped_bytes,
            "delta_dropped_bytes": self.delta_dropped_bytes,
            "backend": self.backend,
            "pty": self.pty,
            "rows": self.rows,
            "cols": self.cols,
            "failure": self.failure,
            "sandbox": self.sandbox.to_dict(),
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
        sandbox: SandboxSnapshot,
        backend: ProcessBackend,
        timeout_seconds: int,
        rows: int | None,
        cols: int | None,
    ) -> None:
        self.process_id = process_id
        self.session_id = session_id
        self.argv = argv
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.sandbox = sandbox
        self.backend = backend
        self.timeout_seconds = timeout_seconds
        self.started_monotonic = time.monotonic()
        self._lock = threading.RLock()
        self._stdout = _BoundedBuffer(_MAX_TRANSCRIPT_CHARS)
        self._stderr = _BoundedBuffer(_MAX_TRANSCRIPT_CHARS)
        self._termination_reason: ProcessState | None = None
        self._failure = ""
        self._rows = rows
        self._cols = cols
        self._exit_event_claimed = False
        self._durable_event_chars = 0
        self._durable_output_truncated = False
        self._delta_dropped_bytes = 0
        self._stdout_thread = self._reader_thread("stdout")
        self._stderr_thread = None if backend.pty else self._reader_thread("stderr")
        self._timeout_thread = threading.Thread(
            target=self._timeout_watch,
            name=f"loom-{self.process_id}-timeout",
            daemon=True,
        )
        self._timeout_thread.start()

    @property
    def process(self):
        """Compatibility access for older tests/callers; prefer backend APIs."""
        return getattr(self.backend, "_process", None)

    def _reader_thread(self, stream: str) -> threading.Thread:
        def read_loop() -> None:
            try:
                reader = self.backend.read_stdout if stream == "stdout" else self.backend.read_stderr
                while True:
                    chunk = reader()
                    if chunk:
                        self._append(stream, chunk)
                        continue
                    if self.backend.poll() is not None:
                        return
                    time.sleep(0.005)
            except Exception as exc:
                if self.backend.poll() is None:
                    with self._lock:
                        if not self._failure:
                            self._failure = f"{type(exc).__name__}: {exc}"
            finally:
                if self.backend.poll() is not None:
                    try:
                        self.backend.close()
                    except Exception:
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
            target = self._stdout if stream == "stdout" else self._stderr
            target.append(chunk)

    @property
    def running(self) -> bool:
        return self.backend.poll() is None

    @property
    def state(self) -> ProcessState:
        if self.running:
            return ProcessState.RUNNING
        with self._lock:
            if self._failure:
                return ProcessState.FAILED
            if self._termination_reason is not None:
                return self._termination_reason
        return ProcessState.EXITED

    def _timeout_watch(self) -> None:
        deadline = self.started_monotonic + self.timeout_seconds
        while self.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    if self._termination_reason is not ProcessState.TERMINATED:
                        self._termination_reason = ProcessState.TIMED_OUT
                self.backend.terminate_tree()
                return
            time.sleep(min(0.1, max(0.01, remaining)))

    def drain_output(self) -> tuple[str, str, bool, int]:
        with self._lock:
            stdout, stdout_cut, stdout_dropped = self._stdout.drain()
            stderr, stderr_cut, stderr_dropped = self._stderr.drain()
            dropped = stdout_dropped + stderr_dropped
            self._delta_dropped_bytes += dropped
        return stdout, stderr, stdout_cut or stderr_cut, dropped

    def bounded_event_output(self, stdout: str, stderr: str) -> tuple[str, str, bool]:
        """Bound total durable PROCESS_OUTPUT payload for one process."""
        with self._lock:
            remaining = max(0, _MAX_DURABLE_EVENT_CHARS - self._durable_event_chars)
            combined = stdout + stderr
            if len(combined) <= remaining:
                self._durable_event_chars += len(combined)
                return stdout, stderr, False
            if self._durable_output_truncated:
                return "", "", True
            self._durable_output_truncated = True
            if remaining <= 0:
                return "\n[loom: durable process output truncated]\n", "", True
            stdout_part = stdout[:remaining]
            leftover = max(0, remaining - len(stdout_part))
            stderr_part = stderr[:leftover]
            self._durable_event_chars += len(stdout_part) + len(stderr_part)
            marker = (
                "\n[loom: durable process output truncated; "
                "use process snapshot for the bounded tail]\n"
            )
            if stderr_part:
                stderr_part += marker
            else:
                stdout_part += marker
            return stdout_part, stderr_part, True

    def snapshot(self, *, drain_delta: bool = False) -> ProcessSnapshot:
        stdout_delta = ""
        stderr_delta = ""
        delta_truncated = False
        delta_dropped = 0
        if drain_delta:
            stdout_delta, stderr_delta, delta_truncated, delta_dropped = self.drain_output()
        with self._lock:
            stdout = self._stdout.text
            stderr = self._stderr.text
            transcript_truncated = self._stdout.truncated or self._stderr.truncated
            dropped_bytes = self._stdout.dropped_bytes + self._stderr.dropped_bytes
            delta_dropped_bytes = self._delta_dropped_bytes
            reason = self._termination_reason
            failure = self._failure
            rows = self._rows
            cols = self._cols
        return ProcessSnapshot(
            process_id=self.process_id,
            argv=self.argv,
            cwd=str(self.cwd),
            permission_mode=self.permission_mode,
            state=self.state,
            running=self.running,
            returncode=self.backend.poll(),
            stdout=stdout,
            stderr=stderr,
            sandbox=self.sandbox,
            backend=self.backend.name,
            pty=self.backend.pty,
            rows=rows,
            cols=cols,
            stdout_delta=stdout_delta,
            stderr_delta=stderr_delta,
            timed_out=reason is ProcessState.TIMED_OUT,
            output_truncated=(
                transcript_truncated
                or delta_truncated
                or delta_dropped_bytes > 0
                or self._durable_output_truncated
            ),
            dropped_bytes=dropped_bytes,
            delta_dropped_bytes=delta_dropped_bytes,
            failure=failure,
        )

    def write_stdin(self, text: str) -> None:
        payload = str(text)
        if len(payload) > _MAX_STDIN_CHARS:
            raise ValueError(f"stdin exceeds {_MAX_STDIN_CHARS:,} characters")
        self.backend.write(payload)

    def send_control(self, control: str) -> None:
        self.backend.send_control(control)

    def close_stdin(self) -> None:
        self.backend.close_stdin()

    def resize(self, *, rows: int, cols: int) -> ProcessSnapshot:
        parsed_rows, parsed_cols = validate_terminal_size(rows, cols)
        if not self.running:
            raise RuntimeError("cannot resize an exited process")
        self.backend.resize(parsed_rows, parsed_cols)
        with self._lock:
            self._rows = parsed_rows
            self._cols = parsed_cols
        return self.snapshot(drain_delta=True)

    def interrupt(self) -> None:
        if not self.running:
            return
        with self._lock:
            if self._termination_reason is None:
                self._termination_reason = ProcessState.INTERRUPTED
        self.backend.interrupt()

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        if not self.running:
            return
        with self._lock:
            if self._termination_reason is not ProcessState.TIMED_OUT:
                self._termination_reason = ProcessState.TERMINATED
        self.backend.terminate_tree(grace_seconds=grace_seconds)

    def claim_exit_event(self) -> bool:
        if self.running:
            return False
        with self._lock:
            if self._exit_event_claimed:
                return False
            self._exit_event_claimed = True
            return True

    def wait(
        self,
        *,
        cancel_check: Callable[[], bool] | None = None,
        on_output: Callable[[str, str], None] | None = None,
        wait_timeout_seconds: float | None = None,
    ) -> ProcessSnapshot:
        wait_deadline = (
            None
            if wait_timeout_seconds is None
            else time.monotonic() + max(0.0, wait_timeout_seconds)
        )
        while self.running:
            if cancel_check is not None and cancel_check():
                self.terminate_tree()
                break
            stdout_delta, stderr_delta, _, _ = self.drain_output()
            if on_output is not None and (stdout_delta or stderr_delta):
                on_output(stdout_delta, stderr_delta)
            if wait_deadline is not None and time.monotonic() >= wait_deadline:
                return self.snapshot()
            if self.running:
                time.sleep(0.02)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=0.5)
        if not self.running:
            try:
                self.backend.close()
            except Exception:
                pass
        stdout_delta, stderr_delta, _, _ = self.drain_output()
        if on_output is not None and (stdout_delta or stderr_delta):
            on_output(stdout_delta, stderr_delta)
        return self.snapshot()


class ProcessStore:
    """Unified in-memory lifecycle manager for pipe and PTY process sessions."""

    def __init__(
        self,
        *,
        max_processes: int = _MAX_PROCESSES,
        sandbox_manager: SandboxManager | None = None,
    ) -> None:
        self.max_processes = max(1, int(max_processes))
        self.sandbox_manager = sandbox_manager or SandboxManager()
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
                managed = self._processes.pop(removable, None)
                if managed is not None:
                    managed.backend.close()
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
        workspace: Path | None = None,
        env: Mapping[str, object] | None = None,
        pty: bool = False,
        rows: int = 24,
        cols: int = 80,
    ) -> ManagedProcess:
        self._prune_finished()
        root = Path(workspace or cwd).expanduser().resolve()
        child_env = safe_process_environment(env)
        # PermissionEngine runs before the tool handler. This second boundary is
        # intentionally SandboxManager.prepare before _spawn_backend. The exact
        # sanitized child environment is frozen before both planning and spawn so
        # MXC can grant only the executable/runtime paths the target actually uses.
        prepared = self.sandbox_manager.prepare(
            argv=tuple(argv),
            cwd=Path(cwd),
            workspace=root,
            permission_mode=permission_mode,
            environment=child_env,
        )
        parsed_rows, parsed_cols = validate_terminal_size(rows, cols)
        process_id = f"proc-{uuid.uuid4().hex[:12]}"
        try:
            backend = _spawn_backend(
                argv=tuple(prepared.argv),
                cwd=Path(prepared.cwd),
                env=child_env,
                pty=bool(pty),
                rows=parsed_rows,
                cols=parsed_cols,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Executable not found: {argv[0]}") from exc
        managed = ManagedProcess(
            process_id=process_id,
            session_id=session_id,
            argv=tuple(argv),
            cwd=Path(cwd).expanduser().resolve(),
            permission_mode=str(permission_mode),
            sandbox=prepared.snapshot,
            backend=backend,
            timeout_seconds=timeout_seconds,
            rows=parsed_rows if pty else None,
            cols=parsed_cols if pty else None,
        )
        with self._lock:
            self._processes[process_id] = managed
            self._order.append(process_id)
        if stdin_text:
            try:
                managed.write_stdin(stdin_text)
            except Exception:
                managed.terminate_tree()
                raise
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
        workspace: Path | None = None,
        env: Mapping[str, object] | None = None,
        pty: bool = False,
        rows: int = 24,
        cols: int = 80,
    ) -> ProcessSnapshot:
        managed = self.start(
            session_id=session_id,
            argv=argv,
            cwd=cwd,
            permission_mode=permission_mode,
            timeout_seconds=timeout_seconds,
            stdin_text=stdin_text,
            workspace=workspace,
            env=env,
            pty=pty,
            rows=rows,
            cols=cols,
        )
        if not pty:
            managed.close_stdin()
        return managed.wait(cancel_check=cancel_check, on_output=on_output)

    def sandbox_snapshot(self, *, permission_mode: str, workspace: Path) -> SandboxSnapshot:
        return self.sandbox_manager.snapshot(
            permission_mode=permission_mode,
            workspace=workspace,
        )

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
    "PipeProcessBackend",
    "ProcessBackend",
    "ProcessSnapshot",
    "ProcessState",
    "ProcessStore",
    "UnixPtyProcessBackend",
    "WindowsConPtyProcessBackend",
    "safe_process_environment",
    "validate_argv",
    "validate_terminal_size",
    "validate_timeout",
]
