from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Protocol

from app.app_server_client import AppServerClientError, JsonRpcClientError

from .approvals import approval_fingerprint_matches
from .idempotency import IdempotencyStore
from .policy import RemoteControlPolicy


class AppServerBackend(Protocol):
    def runtime_status(self) -> dict[str, Any]: ...
    def project_list(self) -> dict[str, Any]: ...
    def thread_list(self, *, limit: int = 100) -> dict[str, Any]: ...
    def thread_read(self, thread_id: str) -> dict[str, Any]: ...
    def thread_start(
        self,
        *,
        workspace: str | None = None,
        project_id: str = "",
        permission_mode: str | None = None,
    ) -> dict[str, Any]: ...
    def turn_start(self, thread_id: str, text: str, attachments=()) -> dict[str, Any]: ...
    def turn_steer(
        self,
        thread_id: str,
        turn_id: str,
        text: str,
        *,
        client_input_id: str = "",
    ) -> dict[str, Any]: ...
    def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]: ...
    def approval_respond(
        self,
        thread_id: str,
        *,
        turn_id: str,
        request_id: str,
        call_id: str,
        decision: str,
    ) -> dict[str, Any]: ...


class RemoteControlError(RuntimeError):
    def __init__(self, code: str, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.data = data

    def as_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "data": copy.deepcopy(self.data),
            },
        }


def _operation_identity(name: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"name": name, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{name}:{hashlib.sha256(encoded).hexdigest()}"


def _translate_rpc_error(exc: JsonRpcClientError) -> RemoteControlError:
    if exc.code == -32004:
        return RemoteControlError("not_found", exc.message, data=exc.data)
    if exc.code in {-32001, -32009}:
        return RemoteControlError("server_busy", exc.message, data=exc.data)
    if exc.code in {-32600, -32602}:
        return RemoteControlError("invalid_request", exc.message, data=exc.data)
    return RemoteControlError("app_server_error", exc.message, data={"rpcCode": exc.code})


class RemoteControlClient:
    """Safe channel facade over Loom's authoritative App Server API."""

    def __init__(
        self,
        backend: AppServerBackend,
        *,
        policy: RemoteControlPolicy | None = None,
        idempotency: IdempotencyStore | None = None,
    ) -> None:
        self.backend = backend
        self.policy = policy or RemoteControlPolicy()
        self.idempotency = idempotency or IdempotencyStore()

    def _call(self, action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except RemoteControlError:
            raise
        except JsonRpcClientError as exc:
            raise _translate_rpc_error(exc) from exc
        except AppServerClientError as exc:
            raise RemoteControlError("server_offline", str(exc)) from exc
        except ValueError as exc:
            raise RemoteControlError("invalid_request", str(exc)) from exc

    @staticmethod
    def _thread_record(snapshot: dict[str, Any]) -> dict[str, Any]:
        thread = snapshot.get("thread")
        if not isinstance(thread, dict):
            raise RemoteControlError("invalid_response", "App Server returned no thread record")
        return thread

    def _ensure_active_control_allowed(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        thread = self._thread_record(snapshot)
        mode = str(thread.get("permissionMode") or "").strip()
        if mode not in self.policy.allowed_active_permission_modes:
            raise RemoteControlError(
                "permission_denied",
                (
                    f"{self.policy.channel} remote control cannot add authority to "
                    f"a thread using permission mode {mode!r}"
                ),
                data={
                    "permissionMode": mode,
                    "allowed": list(self.policy.allowed_active_permission_modes),
                },
            )
        return thread

    def status(self) -> dict[str, Any]:
        return {"ok": True, "runtime": self._call(self.backend.runtime_status)}

    def projects_list(self) -> dict[str, Any]:
        result = self._call(self.backend.project_list)
        return {"ok": True, **result}

    def threads_list(self, *, limit: int = 50) -> dict[str, Any]:
        resolved = max(1, min(int(limit), int(self.policy.max_thread_list_limit)))
        result = self._call(self.backend.thread_list, limit=resolved)
        return {"ok": True, **result}

    def thread_read(self, thread_id: str) -> dict[str, Any]:
        snapshot = self._call(self.backend.thread_read, str(thread_id))
        return {"ok": True, **snapshot}

    def task_start(
        self,
        *,
        prompt: str,
        project_id: str = "",
        thread_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        text = str(prompt or "").strip()
        project = str(project_id or "").strip()
        thread = str(thread_id or "").strip()
        if not text:
            raise RemoteControlError("invalid_request", "prompt must not be empty")
        if bool(project) == bool(thread):
            raise RemoteControlError(
                "invalid_request",
                "pass exactly one of project_id or thread_id",
            )

        operation = _operation_identity(
            "task_start",
            {"prompt": text, "projectId": project, "threadId": thread},
        )

        def execute() -> dict[str, Any]:
            created = False
            if thread:
                snapshot = self._call(self.backend.thread_read, thread)
                self._ensure_active_control_allowed(snapshot)
                target_thread = thread
            else:
                started_thread = self._call(
                    self.backend.thread_start,
                    project_id=project,
                    permission_mode=self.policy.new_thread_permission_mode,
                )
                record = started_thread.get("thread")
                if not isinstance(record, dict) or not record.get("id"):
                    raise RemoteControlError(
                        "invalid_response",
                        "App Server did not return a new thread id",
                    )
                target_thread = str(record["id"])
                created = True

            started_turn = self._call(self.backend.turn_start, target_thread, text)
            turn = started_turn.get("turn")
            if not isinstance(turn, dict):
                turn = {}
            return {
                "ok": True,
                "createdThread": created,
                "threadId": target_thread,
                "turn": turn,
            }

        result, replayed = self._call(
            self.idempotency.run,
            idempotency_key,
            operation,
            execute,
        )
        result["idempotentReplay"] = replayed
        return result

    def task_steer(
        self,
        *,
        thread_id: str,
        turn_id: str,
        input_text: str,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        thread = str(thread_id or "").strip()
        turn = str(turn_id or "").strip()
        text = str(input_text or "").strip()
        if not thread or not turn or not text:
            raise RemoteControlError(
                "invalid_request",
                "thread_id, turn_id, and input_text are required",
            )
        snapshot = self._call(self.backend.thread_read, thread)
        self._ensure_active_control_allowed(snapshot)

        operation = _operation_identity(
            "task_steer",
            {"threadId": thread, "turnId": turn, "input": text},
        )

        def execute() -> dict[str, Any]:
            result = self._call(
                self.backend.turn_steer,
                thread,
                turn,
                text,
                client_input_id=str(idempotency_key or ""),
            )
            return {"ok": True, **result}

        result, replayed = self._call(
            self.idempotency.run,
            idempotency_key,
            operation,
            execute,
        )
        result["idempotentReplay"] = replayed
        return result

    def task_stop(self, *, thread_id: str, turn_id: str) -> dict[str, Any]:
        thread = str(thread_id or "").strip()
        turn = str(turn_id or "").strip()
        if not thread or not turn:
            raise RemoteControlError("invalid_request", "thread_id and turn_id are required")
        result = self._call(self.backend.turn_interrupt, thread, turn)
        return {"ok": True, **result}

    def approval_respond(
        self,
        *,
        thread_id: str,
        fingerprint: str,
        decision: str,
    ) -> dict[str, Any]:
        thread = str(thread_id or "").strip()
        resolved_decision = str(decision or "").strip()
        if resolved_decision not in {"accept", "decline"}:
            raise RemoteControlError(
                "invalid_request",
                "decision must be 'accept' or 'decline'",
            )

        operation = _operation_identity(
            "approval_respond",
            {
                "threadId": thread,
                "fingerprint": str(fingerprint or "").strip(),
                "decision": resolved_decision,
            },
        )
        replay_key = f"approval:{thread}:{str(fingerprint or '').strip()}"

        def execute() -> dict[str, Any]:
            snapshot = self._call(self.backend.thread_read, thread)
            self._ensure_active_control_allowed(snapshot)
            pending = snapshot.get("pendingApproval")
            if not isinstance(pending, dict):
                raise RemoteControlError("stale_approval", "thread has no pending approval")
            if not approval_fingerprint_matches(thread, pending, fingerprint):
                raise RemoteControlError(
                    "stale_approval",
                    "approval changed after this card was displayed; refresh before responding",
                )

            result = self._call(
                self.backend.approval_respond,
                thread,
                turn_id=str(pending.get("turnId") or ""),
                request_id=str(pending.get("requestId") or ""),
                call_id=str(pending.get("callId") or ""),
                decision=resolved_decision,
            )
            return {"ok": True, **result}

        result, replayed = self._call(
            self.idempotency.run,
            replay_key,
            operation,
            execute,
        )
        result["idempotentReplay"] = replayed
        return result


__all__ = ["AppServerBackend", "RemoteControlClient", "RemoteControlError"]
