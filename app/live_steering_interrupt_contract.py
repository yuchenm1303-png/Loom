from __future__ import annotations

"""Phase-two live steering: supersede an in-flight model sample immediately.

The durable inbox implemented by ``live_steering_contract`` remains the source
of truth. This layer only adds process-local invalidation: guidance received
while a model request is generating causes that request to be abandoned and the
same logical turn to be sampled again with the new guidance in history.
Already-running tools are deliberately not cancelled; they finish and become a
durable observation before replanning.
"""

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import Any

from app.import_patch_chain import find_spec_without
from app.live_steering_contract import _consumed_duplicate, _input_id, _receipt, _submit_once


_TARGET_MODULE = "app.app_server"
_INSTALLED = False


def _patch_runtime_class(runtime_cls: type[Any]) -> None:
    if runtime_cls.__dict__.get("_loom_live_steering_interrupt_installed", False):
        return
    original_steer = getattr(runtime_cls, "steer", None)
    if not callable(original_steer):
        return

    # Phase one resumes a superseded approval through the normal durable layers.
    # DurableAgentRuntime accounts goal usage after _drive() returns, but the core
    # runner has already persisted TURN_COMPLETED by then. Phase two adds a tiny
    # pre-completion hook so the final steering segment is accounted *before* the
    # terminal boundary becomes observable. The wrapper below suppresses the
    # legacy post-drive accounting only when that hook already did the work.
    original_resume_steered_turn = getattr(runtime_cls, "resume_steered_turn", None)
    original_track_goal_usage = getattr(runtime_cls, "_track_goal_usage", None)
    original_before_turn_completed = getattr(runtime_cls, "_before_turn_completed", None)

    def _usage_states(self: Any) -> dict[str, dict[str, Any]]:
        states = getattr(self, "_loom_steered_usage_states", None)
        if states is None:
            states = {}
            setattr(self, "_loom_steered_usage_states", states)
        return states

    def before_turn_completed(self: Any, session: Any) -> None:
        if callable(original_before_turn_completed):
            original_before_turn_completed(self, session)
        if not callable(original_track_goal_usage):
            return
        state = _usage_states(self).get(session.session_id)
        if not state or bool(state.get("accounted")):
            return
        if str(state.get("turn_id") or "") != str(session.current_turn_id or ""):
            return
        original_track_goal_usage(
            self,
            self._result(session),
            before_tokens=int(state["before_tokens"]),
        )
        state["accounted"] = True

    def track_goal_usage(
        self: Any,
        result: Any,
        *,
        before_tokens: int | None = None,
    ) -> Any:
        if not callable(original_track_goal_usage):
            return result
        state = _usage_states(self).get(str(getattr(result, "session_id", "")))
        if (
            state
            and bool(state.get("accounted"))
            and str(state.get("turn_id") or "") == str(getattr(result, "turn_id", "") or "")
            and before_tokens is not None
            and int(before_tokens) == int(state.get("before_tokens", -1))
        ):
            return result
        return original_track_goal_usage(self, result, before_tokens=before_tokens)

    def resume_steered_turn(self: Any, session_id: str, turn_id: str) -> Any:
        if not callable(original_resume_steered_turn):
            raise RuntimeError("runtime cannot resume a turn superseded by steering")
        resolved_session_id = str(session_id or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        states = _usage_states(self)
        # Only DurableAgentRuntime owns _track_goal_usage. Core runtimes still use
        # the same resume implementation but need no accounting state.
        if callable(original_track_goal_usage):
            states[resolved_session_id] = {
                "turn_id": resolved_turn_id,
                "before_tokens": self.store.load(resolved_session_id).usage.total_tokens,
                "accounted": False,
            }
        try:
            return original_resume_steered_turn(self, resolved_session_id, resolved_turn_id)
        finally:
            states.pop(resolved_session_id, None)

    def steer(
        self: Any,
        session_id: str,
        text: str,
        *,
        turn_id: str,
        input_id: str | None = None,
    ) -> Any:
        value = str(text or "").strip()
        if not value:
            raise ValueError("steering input must not be empty")
        resolved_session_id = str(session_id or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_session_id or not resolved_turn_id:
            raise ValueError("steering requires session_id and turn_id")
        resolved_input_id = _input_id(input_id)

        # The common RUNNING path is handled atomically under the same guard used
        # by TurnRunner's terminal commit. Persist the guidance first, then advance
        # the process-local model revision before releasing the guard. This makes
        # the ordering unambiguous: either the response committed first, or the
        # new guidance supersedes the still-uncommitted sample.
        with self._active_tokens_guard:
            token = self._active_tokens.get(resolved_session_id)
            session = self.store.load(resolved_session_id)
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
                and session.current_turn_id == resolved_turn_id
                and str(getattr(session.status, "value", session.status)) == "running"
            ):
                identifier, duplicate, submitted_at = _submit_once(
                    self.store,
                    session_id=resolved_session_id,
                    turn_id=resolved_turn_id,
                    text=value,
                    input_id=resolved_input_id,
                )
                sampling = False
                if not duplicate:
                    # Import lazily so importing ``app`` itself does not force the
                    # entire agent_runtime package to initialize just to install
                    # this protocol patch.
                    from app.agent_runtime.model_replan import request_replan

                    sampling = request_replan(token)
                return _receipt(
                    input_id=identifier,
                    duplicate=duplicate,
                    delivery="model_replan_requested" if sampling else "next_safe_boundary",
                    submitted_at=submitted_at,
                )

        # WAITING_APPROVAL and terminal/idempotent edge cases stay delegated to
        # phase one's implementation, which owns approval supersession and durable
        # retry receipts. No model token exists at that boundary to interrupt.
        return original_steer(
            self,
            resolved_session_id,
            value,
            turn_id=resolved_turn_id,
            input_id=resolved_input_id,
        )

    runtime_cls.steer = steer
    runtime_cls._before_turn_completed = before_turn_completed
    if callable(original_resume_steered_turn):
        runtime_cls.resume_steered_turn = resume_steered_turn
    if callable(original_track_goal_usage):
        runtime_cls._track_goal_usage = track_goal_usage
    runtime_cls._loom_live_steering_interrupt_installed = True


def patch(module: Any) -> None:
    service_cls = module.LoomAppServerService
    controller_cls = module.LoomRpcController
    if getattr(service_cls, "_loom_live_steering_interrupt_contract_installed", False):
        return

    original_service_init = service_cls.__init__
    original_initialize = controller_cls._initialize

    def service_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_service_init(self, *args, **kwargs)
        _patch_runtime_class(type(self.runtime))

    def initialize(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_initialize(self, params)
        capabilities = dict(result.get("capabilities") or {})
        turns = dict(capabilities.get("turns") or {})
        steering = dict(turns.get("steering") or {})
        steering["interruptsInFlightModel"] = True
        steering["runningToolPolicy"] = "finish_then_replan"
        turns["steering"] = steering
        capabilities["turns"] = turns
        result["capabilities"] = capabilities
        return result

    service_cls.__init__ = service_init
    service_cls._loom_live_steering_interrupt_contract_installed = True
    controller_cls._initialize = initialize


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _LiveSteeringInterruptLoader(importlib.abc.Loader):
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


class _LiveSteeringInterruptFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _LiveSteeringInterruptLoader):
            return spec
        spec.loader = _LiveSteeringInterruptLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _LiveSteeringInterruptFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
