from __future__ import annotations

"""Same-turn live steering for the desktop/app-server execution path.

A steering message is not a new turn and it is not a cancellation. It is a
durable inbox entry attached to the current turn. The runtime consumes that
inbox only at safe boundaries: after an in-flight tool has a durable result,
between model steps, or immediately before a terminal answer commits.

Waiting approvals are a special boundary. New guidance supersedes the pending
approval and every not-yet-executed call from that sampled model response, then
continues the *same* turn with the guidance in history. No admitted tool is
replayed and no already-observed side effect is rolled back implicitly.
"""

import importlib.abc
import importlib.machinery
import json
import sys
import threading
import uuid
from types import ModuleType
from typing import Any

from app.import_patch_chain import find_spec_without


_TARGET_MODULE = "app.app_server"
_INSTALLED = False
_STEERING_PROMPT_MARKER = "LOOM_LIVE_STEERING_CONTRACT"
_STEERING_PROMPT = (
    f"[{_STEERING_PROMPT_MARKER}] The user may provide new guidance while this turn is still running. "
    "Apply the latest guidance to work that has not yet been executed. Treat completed tool results and "
    "other already-observed effects as facts: do not repeat them, pretend they did not happen, or undo "
    "them unless the user explicitly asks you to."
)


def _input_id(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 128:
        raise ValueError("clientInputId must be at most 128 characters")
    return text or uuid.uuid4().hex


def _submit_once(
    store: Any,
    *,
    session_id: str,
    turn_id: str,
    text: str,
    input_id: str,
) -> tuple[str, bool]:
    """Durably enqueue one steering input, deduplicated by client id."""

    from app.agent_runtime.journal import atomic_json, session_lock

    directory = store.session_dir(session_id)
    with session_lock(directory):
        path = directory / "steering.json"
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        for item in items:
            if str(item.get("id") or "") != input_id:
                continue
            same_turn = str(item.get("turn_id") or "") == turn_id
            same_text = str(item.get("text") or "") == text
            if not same_turn or not same_text:
                raise ValueError("clientInputId was already used for different steering input")
            return input_id, True
        if len(items) >= 100 or len(text) > 100_000:
            raise ValueError("steering inbox limit reached")
        items.append({"id": input_id, "turn_id": turn_id, "text": text})
        atomic_json(path, items)
    return input_id, False


def _receipt(
    *,
    input_id: str,
    duplicate: bool,
    delivery: str,
    resume_required: bool = False,
    applied: bool = False,
) -> dict[str, Any]:
    return {
        "accepted": True,
        "input_id": input_id,
        "duplicate": bool(duplicate),
        "delivery": delivery,
        "resume_required": bool(resume_required),
        "applied": bool(applied),
    }


def _consumed_duplicate(
    runtime: Any,
    session: Any,
    *,
    turn_id: str,
    text: str,
    input_id: str,
) -> dict[str, Any] | None:
    """Return an idempotent success for a steering message already in history.

    This matters when the app-server response is lost after the guidance was
    applied and the renderer retries after the turn has already completed.
    Reusing the same id for different content is rejected instead of silently
    dropping the user's newer instruction.
    """

    if input_id not in session.steering_ids:
        return None
    for event in reversed(runtime.store.events(session.session_id)):
        if event.turn_id != turn_id or event.kind.value != "user_message":
            continue
        data = event.data
        if str(data.get("source") or "") != "steering":
            continue
        if str(data.get("input_id") or "") != input_id:
            continue
        if str(data.get("text") or "") != text:
            raise ValueError("clientInputId was already used for different steering input")
        break
    return _receipt(
        input_id=input_id,
        duplicate=True,
        delivery="applied",
        applied=True,
    )


def _patch_runtime_class(runtime_cls: type[Any]) -> None:
    # Patch each concrete class once. A subclass can override methods from an
    # already-patched parent, so an inherited marker is deliberately ignored.
    if runtime_cls.__dict__.get("_loom_live_steering_installed", False):
        return

    from app.agent_runtime.contracts import AgentEventKind, AgentStatus
    from app.agent_runtime.tools import ToolResult

    original_system_prompt = runtime_cls._model_system_prompt

    def model_system_prompt(self: Any, session: Any, step: Any) -> str:
        value = str(original_system_prompt(self, session, step))
        if _STEERING_PROMPT_MARKER in value:
            return value
        return f"{value}\n\n{_STEERING_PROMPT}"

    def steer(
        self: Any,
        session_id: str,
        text: str,
        *,
        turn_id: str,
        input_id: str | None = None,
    ) -> dict[str, Any]:
        value = str(text or "").strip()
        if not value:
            raise ValueError("steering input must not be empty")
        resolved_session_id = str(session_id or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_session_id or not resolved_turn_id:
            raise ValueError("steering requires session_id and turn_id")
        resolved_input_id = _input_id(input_id)

        # The active path intentionally shares the terminal-commit guard used by
        # TurnRunner. Either the guidance enters the inbox before completion or
        # the completed turn wins. A retry of an already-consumed id remains an
        # idempotent success even if completion won after the first submission.
        with self._active_tokens_guard:
            token = self._active_tokens.get(resolved_session_id)
            session = self.store.load(resolved_session_id)
            if session.current_turn_id != resolved_turn_id:
                raise ValueError("steering target is not the active turn")
            consumed = _consumed_duplicate(
                self,
                session,
                turn_id=resolved_turn_id,
                text=value,
                input_id=resolved_input_id,
            )
            if consumed is not None:
                return consumed
            if (
                token is not None
                and not token.cancelled
                and session.status is AgentStatus.RUNNING
            ):
                identifier, duplicate = _submit_once(
                    self.store,
                    session_id=resolved_session_id,
                    turn_id=resolved_turn_id,
                    text=value,
                    input_id=resolved_input_id,
                )
                return _receipt(
                    input_id=identifier,
                    duplicate=duplicate,
                    delivery="next_safe_boundary",
                )

        # WAITING_APPROVAL has no active token: the worker intentionally returned
        # control to the app server. Acquire the normal session lease so an
        # approval response and a steer cannot both win this boundary.
        lock = self._session_lock(resolved_session_id)
        with lock:
            # Re-check the active case after waiting for the lease. A concurrent
            # approval may already have resumed the turn while we were waiting.
            with self._active_tokens_guard:
                token = self._active_tokens.get(resolved_session_id)
                session = self.store.load(resolved_session_id)
                if session.current_turn_id != resolved_turn_id:
                    raise ValueError("steering target is not the active turn")
                consumed = _consumed_duplicate(
                    self,
                    session,
                    turn_id=resolved_turn_id,
                    text=value,
                    input_id=resolved_input_id,
                )
                if consumed is not None:
                    return consumed
                if (
                    token is not None
                    and not token.cancelled
                    and session.status is AgentStatus.RUNNING
                ):
                    identifier, duplicate = _submit_once(
                        self.store,
                        session_id=resolved_session_id,
                        turn_id=resolved_turn_id,
                        text=value,
                        input_id=resolved_input_id,
                    )
                    return _receipt(
                        input_id=identifier,
                        duplicate=duplicate,
                        delivery="next_safe_boundary",
                    )

            session = self.store.load(resolved_session_id)
            if session.current_turn_id != resolved_turn_id:
                raise ValueError("steering target is not the active turn")
            consumed = _consumed_duplicate(
                self,
                session,
                turn_id=resolved_turn_id,
                text=value,
                input_id=resolved_input_id,
            )
            if consumed is not None:
                return consumed
            if session.status is not AgentStatus.WAITING_APPROVAL or session.pending_approval is None:
                raise ValueError("steering target is not the active turn")

            approval_call_id = session.pending_approval.call_id
            pending_calls = list(session.pending_tool_calls)
            if not pending_calls or not any(call.call_id == approval_call_id for call in pending_calls):
                raise RuntimeError("pending approval state is inconsistent")

            identifier, duplicate = _submit_once(
                self.store,
                session_id=resolved_session_id,
                turn_id=resolved_turn_id,
                text=value,
                input_id=resolved_input_id,
            )

            # The user's new direction supersedes this sampled action set. All
            # calls are still unexecuted at this boundary, so close their model
            # history explicitly instead of carrying stale calls into replanning.
            step_id = str(session.pending_step_id or "")
            session.status = AgentStatus.RUNNING
            session.pending_approval = None
            session.pending_tool_calls.clear()
            session.pending_step_id = ""
            for call in pending_calls:
                session.pending_bindings.pop(call.call_id, None)
            self._release_turn_steps(session)

            for call in pending_calls:
                if call.call_id == approval_call_id:
                    self._record(
                        session,
                        AgentEventKind.TOOL_DENIED,
                        data={
                            "call_id": call.call_id,
                            "tool": call.name,
                            "source": "user",
                            "reason": "superseded by new user guidance",
                            "steering": True,
                            "steering_input_id": identifier,
                            "step_id": step_id,
                        },
                    )
                self._append_tool_result(
                    session,
                    call,
                    ToolResult(
                        ok=False,
                        content="Not executed: superseded by new user guidance; reconsider this action.",
                    ),
                    failed=True,
                )

            self._consume_steering(session)
            return _receipt(
                input_id=identifier,
                duplicate=duplicate,
                delivery="approval_superseded",
                resume_required=True,
                applied=True,
            )

    def resume_steered_turn(self: Any, session_id: str, turn_id: str):
        """Continue an approval-superseded turn through the normal durable layers."""

        resolved_session_id = str(session_id or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        before_tokens = self.store.load(resolved_session_id).usage.total_tokens
        lock = self._session_lock(resolved_session_id)
        with lock:
            session = self.store.load(resolved_session_id)
            if session.current_turn_id != resolved_turn_id:
                raise ValueError("turn_id does not match the steered turn")
            if session.status is not AgentStatus.RUNNING:
                return self._result(session)
            if session.pending_approval is not None or session.pending_tool_calls:
                raise RuntimeError("steered turn still owns unresolved tool actions")
            with self._active_tokens_guard:
                existing = self._active_tokens.get(resolved_session_id)
                if existing is not None and not existing.cancelled:
                    return self._result(session)
            token = self._activate(resolved_session_id)
            try:
                result = self._drive(session, token)
            finally:
                self._deactivate(resolved_session_id, token)

        # DurableAgentRuntime normally performs these layers in resume_approval
        # after CoreAgentRuntime has released its token. Steering replaces that
        # approval resume entrypoint, so preserve identical goal accounting and
        # queued-turn draining without making the core runtime depend on Durable.
        track_usage = getattr(self, "_track_goal_usage", None)
        if callable(track_usage):
            result = track_usage(result, before_tokens=before_tokens)
        if bool(getattr(self, "auto_drain_queue", False)) and result.status is AgentStatus.COMPLETED:
            drain_queue = getattr(self, "_drain_queue", None)
            if callable(drain_queue):
                drained = drain_queue(resolved_session_id, result)
                if drained is not None:
                    result = drained
        return result

    runtime_cls._model_system_prompt = model_system_prompt
    runtime_cls.steer = steer
    runtime_cls.resume_steered_turn = resume_steered_turn
    runtime_cls._loom_live_steering_installed = True


def patch(module: Any) -> None:
    service_cls = module.LoomAppServerService
    controller_cls = module.LoomRpcController
    if getattr(service_cls, "_loom_live_steering_contract_installed", False):
        return

    original_service_init = service_cls.__init__
    original_initialize = controller_cls._initialize

    def service_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_service_init(self, *args, **kwargs)
        _patch_runtime_class(type(self.runtime))

    def turn_steer(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        turn_id = self._required_text(params, "turnId")
        text = self._required_text(params, "input")
        client_input_id = _input_id(params.get("clientInputId"))
        receipt = self.runtime.steer(
            session_id,
            text,
            turn_id=turn_id,
            input_id=client_input_id,
        )
        if not isinstance(receipt, dict):
            receipt = _receipt(
                input_id=client_input_id,
                duplicate=False,
                delivery="next_safe_boundary",
            )

        if bool(receipt.get("resume_required")):
            resume = getattr(self.runtime, "resume_steered_turn", None)
            if not callable(resume):
                raise RuntimeError("runtime cannot resume an approval superseded by steering")

            # Reserve the app-server slot before notifying the renderer, but gate
            # execution so `thread/updated: running` cannot arrive after a very
            # fast resumed turn has already completed.
            gate = threading.Event()

            def continue_turn() -> Any:
                gate.wait()
                return resume(session_id, turn_id)

            self._launch(session_id, continue_turn)
            try:
                session = self.store.load(session_id)
                self._notify(
                    "thread/updated",
                    {"thread": self._record(session, active=True)},
                )
            finally:
                gate.set()

        return {
            "threadId": session_id,
            "turnId": turn_id,
            "accepted": bool(receipt.get("accepted", True)),
            "inputId": str(receipt.get("input_id") or client_input_id),
            "duplicate": bool(receipt.get("duplicate")),
            "delivery": str(receipt.get("delivery") or "next_safe_boundary"),
            "applied": bool(receipt.get("applied")),
        }

    def initialize(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_initialize(self, params)
        capabilities = dict(result.get("capabilities") or {})
        turns = dict(capabilities.get("turns") or {})
        turns["steer"] = True
        turns["steering"] = {
            "sameTurn": True,
            "delivery": "safeBoundary",
            "idempotencyKey": "clientInputId",
            "approvalSupersedesPending": True,
            "attachments": False,
        }
        capabilities["turns"] = turns
        result["capabilities"] = capabilities
        return result

    service_cls.__init__ = service_init
    service_cls.turn_steer = turn_steer
    service_cls._loom_live_steering_contract_installed = True
    controller_cls._initialize = initialize


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _LiveSteeringLoader(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader) -> None:
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.loader, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        exec_module = getattr(self.loader, "exec_module", None)
        if not callable(exec_module):
            raise ImportError(f"loader for {_TARGET_MODULE} cannot execute modules")
        exec_module(module)
        patch(module)


class _LiveSteeringFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _LiveSteeringLoader):
            return spec
        spec.loader = _LiveSteeringLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _LiveSteeringFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
