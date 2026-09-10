from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import Any

_TARGET_MODULE = "app.ai.openai_runtime"
_INSTALLED = False
_PATCHED = False


def _adapter_value(connection: Any) -> str:
    adapter = getattr(connection, "adapter", "")
    return str(getattr(adapter, "value", adapter) or "").strip().casefold()


def _canonical_model_name(connection: Any, model: Any) -> str:
    name = str(model or "").strip()
    if not name:
        return name

    # Several OpenAI-compatible gateways treat model identifiers as exact API
    # names. In particular, DeepSeek-compatible aliases are lowercase on the
    # wire even when the UI saves a display-style value such as DeepSeek-v4-flash.
    if _adapter_value(connection) == "openai-compatible" and name.casefold().startswith("deepseek-"):
        return name.casefold()
    return name


def patch(module: ModuleType) -> None:
    global _PATCHED
    backend_cls = getattr(module, "OpenAIChatBackend", None)
    if backend_cls is None or getattr(backend_cls, "_loom_model_name_compat", False):
        _PATCHED = True
        return

    original_request_kwargs = backend_cls._request_kwargs

    def request_kwargs(self: Any, request: Any) -> dict[str, Any]:
        kwargs = dict(original_request_kwargs(self, request))
        kwargs["model"] = _canonical_model_name(getattr(self, "connection", None), kwargs.get("model"))
        return kwargs

    backend_cls._request_kwargs = request_kwargs
    backend_cls._loom_model_name_compat = True
    _PATCHED = True


class _ModelNameCompatLoader(importlib.abc.Loader):
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


class _ModelNameCompatFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ModelNameCompatLoader):
            return spec
        spec.loader = _ModelNameCompatLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = sys.modules.get(_TARGET_MODULE)
    if existing is not None:
        patch(existing)
        _INSTALLED = True
        return
    sys.meta_path.insert(0, _ModelNameCompatFinder())
    _INSTALLED = True
