from __future__ import annotations

"""Central Window05 x Window06 recovery-routing integration.

Ordinary app-server reads are observational. A persisted active snapshot is not
proof of an unclean crash, so loading a thread must not terminalize it. Trusted
safe-handoff recovery is requested explicitly through ``thread/resume`` with the
unfinished ``recoverTurnId``; the runtime then rebuilds a fresh execution stack
for that same logical turn. Unclean-loss finalization remains an explicit
Window06 operation and pending-approval replay remains blocked on Window02.
"""

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import Any

from app.import_patch_chain import find_spec_without


_TARGET_MODULE = "app.app_server"
_INSTALLED = False


def patch(module: Any) -> None:
    service_cls = module.LoomAppServerService
    controller_cls = module.LoomRpcController
    if getattr(service_cls, "_loom_recovery_contract_installed", False):
        return

    original_thread_resume = service_cls.thread_resume
    original_initialize = controller_cls._initialize

    def load(self: Any, session_id: str):
        # Do not infer crash ownership from persisted RUNNING state. Window06's
        # recover_interrupted() is explicit unclean-loss finalization only.
        return self.runtime.get_session(str(session_id or "").strip())

    def thread_resume(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        recover_turn_id = str(params.get("recoverTurnId") or "").strip()
        if recover_turn_id:
            session = self._load(session_id)
            if session.current_turn_id != recover_turn_id:
                raise ValueError("recoverTurnId does not match the unfinished turn")
            if not self._is_active(session_id):
                recover = getattr(self.runtime, "recover_turn_if_idle", None)
                if not callable(recover):
                    raise RuntimeError("runtime does not support safe-handoff turn recovery")
                # _launch marks the session live before running the recovery body,
                # so a concurrent resume becomes a rejoin rather than a duplicate.
                self._launch(
                    session_id,
                    lambda: recover(session_id, recover_turn_id),
                )
        return original_thread_resume(self, params)

    def initialize(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_initialize(self, params)
        capabilities = dict(result.get("capabilities") or {})
        threads = dict(capabilities.get("threads") or {})
        threads["safeHandoffRecover"] = True
        capabilities["threads"] = threads
        result["capabilities"] = capabilities
        return result

    service_cls._load = load
    service_cls.thread_resume = thread_resume
    service_cls._loom_recovery_contract_installed = True
    controller_cls._initialize = initialize


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _RecoveryContractLoader(importlib.abc.Loader):
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


class _RecoveryContractFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _RecoveryContractLoader):
            return spec
        spec.loader = _RecoveryContractLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _RecoveryContractFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
