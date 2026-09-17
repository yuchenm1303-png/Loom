from __future__ import annotations

"""Transient streaming cleanup for immediately superseded model samples.

A phase-two steering request can abandon a provider stream before it has a
canonical MODEL_RESPONSE. Durable history is already correct in that case, but
the desktop may have rendered transient assistant/tool-call deltas. This patch
tombstones those provisional items as soon as TurnRunner records the steering
rejection so stale text or phantom tool spinners do not survive the replan.
"""

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import Any

from app.import_patch_chain import find_spec_without


_TARGET_MODULE = "app.app_server_streaming"
_INSTALLED = False


def patch(module: Any) -> None:
    service_cls = module.StreamingLoomAppServerService
    if getattr(service_cls, "_loom_live_steering_stream_cleanup_installed", False):
        return

    original_on_runtime_event = service_cls._on_runtime_event
    rejected_kind = module.AgentEventKind.MODEL_RESPONSE_REJECTED

    def on_runtime_event(self: Any, event: Any) -> None:
        if (
            event.kind is rejected_kind
            and str(event.data.get("reason") or "") == "superseded_by_steering"
        ):
            step_id = str(event.data.get("step_id") or "").strip()
            assistant_item_id = module._assistant_step_item_id(step_id) if step_id else ""
            assistant_started = False
            streamed_tools: list[dict[str, Any]] = []
            with self._guard:
                if step_id:
                    assistant_key = (event.session_id, event.turn_id, step_id)
                    assistant_started = assistant_key in self._streamed_assistant_steps
                    self._streamed_assistant_steps.discard(assistant_key)
                    self._stream_last_tool_index.pop(assistant_key, None)
                    stale_tool_keys = [
                        key
                        for key in self._streamed_tool_calls
                        if key[0] == event.session_id
                        and key[1] == event.turn_id
                        and key[2] == step_id
                    ]
                    for key in stale_tool_keys:
                        streamed_tools.append(dict(self._streamed_tool_calls.pop(key)))

            # `type: superseded` is a transient tombstone, not a durable transcript
            # item. The renderer merges it over the provisional item, making that
            # abandoned sample disappear immediately; thread/read never contains it.
            if assistant_started and assistant_item_id:
                self._notify(
                    "item/completed",
                    {
                        "item": {
                            "id": assistant_item_id,
                            "threadId": event.session_id,
                            "turnId": event.turn_id,
                            "type": "superseded",
                            "status": "superseded",
                            "text": "",
                            "updatedAt": event.created_at,
                        }
                    },
                )
            for state in streamed_tools:
                call_id = str(state.get("call_id") or "").strip()
                if not call_id or not state.get("started"):
                    continue
                self._notify(
                    "item/completed",
                    {
                        "item": {
                            "id": module._tool_item_id(call_id),
                            "threadId": event.session_id,
                            "turnId": event.turn_id,
                            "type": "superseded",
                            "status": "superseded",
                            "callId": call_id,
                            "updatedAt": event.created_at,
                        }
                    },
                )

        original_on_runtime_event(self, event)

    service_cls._on_runtime_event = on_runtime_event
    service_cls._loom_live_steering_stream_cleanup_installed = True


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _LiveSteeringStreamLoader(importlib.abc.Loader):
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


class _LiveSteeringStreamFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _LiveSteeringStreamLoader):
            return spec
        spec.loader = _LiveSteeringStreamLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _LiveSteeringStreamFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
