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

from app.agent_runtime.model_replan import request_replan
from app.import_patch_chain import find_spec_without


_TARGET_MODULE = "app.app_server"
_INSTALLED = False


def _patch_runtime_class(runtime_cls: type[Any]) -> None:
    if runtime_cls.__dict__.get("_loom_live_steering_interrupt_installed", False):
        return
    original_steer = getattr(runtime_cls, "steer", None)
    if not callable(original_steer):
        return

    def steer(
        self: Any,
        session_id: str,
        text: str,
        *,
        turn_id: str,
        input_id: str | None = None,
    ) -> Any:
        receipt = original_steer(
            self,
            session_id,
            text,
            turn_id=turn_id,
            input_id=input_id,
        )
        if not isinstance(receipt, dict) or bool(receipt.get("duplicate")):
            return receipt
        if str(receipt.get("delivery") or "") != "next_safe_boundary":
            return receipt

        resolved_session_id = str(session_id or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        resolved_input_id = str(receipt.get("input_id") or input_id or "").strip()
        sampling = False
        with self._active_tokens_guard:
            token = self._active_tokens.get(resolved_session_id)
            session = self.store.load(resolved_session_id)
            # If the runner already consumed the durable inbox entry, its next
            # request is already built from the new history and does not need a
            # second invalidation. Otherwise advance the process-local revision.
            already_consumed = bool(
                resolved_input_id and resolved_input_id in session.steering_ids
            )
            if (
                not already_consumed
                and token is not None
                and not token.cancelled
                and session.current_turn_id == resolved_turn_id
                and str(getattr(session.status, "value", session.status)) == "running"
            ):
                sampling = request_replan(token)

        if not sampling:
            return receipt
        updated = dict(receipt)
        updated["delivery"] = "model_replan_requested"
        return updated

    runtime_cls.steer = steer
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
