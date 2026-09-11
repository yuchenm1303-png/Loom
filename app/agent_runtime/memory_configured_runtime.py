from __future__ import annotations

import os
from typing import Any

from app.ai import AIMessage

from .context_state import WorldStateEnvelope
from .memory_pipeline import MemoryPipeline
from .memory_runtime import MemoryRuntime
from .memory_semantic import SemanticMemoryRuntime


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().casefold()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


class ConfiguredSemanticMemoryRuntime(SemanticMemoryRuntime):
    """Live-configurable Memory v2 policy used by Loom's product runtime.

    Workers remain lightweight and durable settings gate whether they may do
    model work. Re-enabling a feature scans its durable backlog so toggling a
    setting never loses completed turns or pending semantic jobs. App-server
    management remains available even when model-side memory use is disabled.
    """

    def __init__(
        self,
        *args,
        memory_enabled: bool | None = None,
        **kwargs,
    ) -> None:
        self.memory_enabled = (
            _env_bool("LOOM_MEMORY_ENABLED", True)
            if memory_enabled is None
            else bool(memory_enabled)
        )
        super().__init__(*args, **kwargs)
        self.memory_store.model_access_enabled = bool(self.memory_enabled)

    def configure_memory(
        self,
        *,
        memory_enabled: bool | None = None,
        auto_extract: bool | None = None,
        semantic_auto: bool | None = None,
        idle_seconds: float | int | None = None,
    ) -> dict[str, object]:
        before_extract = bool(self.memory_enabled and self.memory_auto_extract)
        before_semantic = bool(self.memory_enabled and self.memory_semantic_auto)

        if memory_enabled is not None:
            self.memory_enabled = bool(memory_enabled)
        if auto_extract is not None:
            self.memory_auto_extract = bool(auto_extract)
        if semantic_auto is not None:
            self.memory_semantic_auto = bool(semantic_auto)
        if idle_seconds is not None:
            self.memory_idle_seconds = max(0.0, min(600.0, float(idle_seconds)))
            pipeline = self._memory_pipeline
            if pipeline is not None:
                pipeline.idle_seconds = self.memory_idle_seconds

        self.memory_store.model_access_enabled = bool(self.memory_enabled)
        extract_active = bool(self.memory_enabled and self.memory_auto_extract)
        semantic_active = bool(self.memory_enabled and self.memory_semantic_auto)

        if extract_active:
            self._ensure_product_memory_pipeline()
            if not before_extract:
                self._memory_backlog_scheduled = False
                self._ensure_memory_backlog_scheduled()

        if semantic_active:
            self._ensure_product_semantic_pipeline()
            if not before_semantic:
                self._schedule_semantic_backlog()

        return self.memory_configuration()

    def memory_configuration(self) -> dict[str, object]:
        return {
            "enabled": bool(self.memory_enabled),
            "autoExtract": bool(self.memory_auto_extract),
            "semanticAuto": bool(self.memory_semantic_auto),
            "idleSeconds": self.memory_idle_seconds,
        }

    def _ensure_product_memory_pipeline(self) -> None:
        if self._memory_pipeline is not None:
            self._memory_pipeline.idle_seconds = self.memory_idle_seconds
            return
        pipeline = MemoryPipeline(
            self._process_memory_session,
            idle_seconds=self.memory_idle_seconds,
        )
        self._memory_pipeline = pipeline
        self.subscribe(pipeline.on_event)

    def _ensure_product_semantic_pipeline(self) -> None:
        if self._semantic_pipeline is not None:
            return
        self._semantic_pipeline = MemoryPipeline(
            self._process_semantic_job,
            idle_seconds=0.0,
            name="loom-memory-semantic",
        )

    def _process_memory_session(self, session_id: str) -> None:
        if not self.memory_enabled or not self.memory_auto_extract:
            return
        super()._process_memory_session(session_id)

    def _process_semantic_job(self, extraction_id: str) -> None:
        if not self.memory_enabled or not self.memory_semantic_auto:
            return
        super()._process_semantic_job(extraction_id)

    def extract_memory_from_thread(self, *args: Any, **kwargs: Any):
        if not self.memory_enabled:
            raise RuntimeError("long-term memory is disabled")
        return super().extract_memory_from_thread(*args, **kwargs)

    def extract_memory_from_events(self, *args: Any, **kwargs: Any):
        if not self.memory_enabled or not self.memory_auto_extract:
            return None
        return super().extract_memory_from_events(*args, **kwargs)

    def _request_context_messages(
        self,
        session,
        step,
        envelope: WorldStateEnvelope,
    ) -> tuple[AIMessage, ...]:
        if self.memory_enabled:
            return super()._request_context_messages(session, step, envelope)
        # Skip MemoryRuntime's summary injection while preserving the rest of
        # the runtime stack below it (context, sandbox, multi-agent, etc.).
        return super(MemoryRuntime, self)._request_context_messages(
            session,
            step,
            envelope,
        )

    def memory_status(self, session_id: str) -> dict[str, object]:
        data = dict(super().memory_status(session_id))
        data["enabled"] = bool(self.memory_enabled)
        data["configured"] = self.memory_configuration()
        return data


__all__ = ["ConfiguredSemanticMemoryRuntime"]
