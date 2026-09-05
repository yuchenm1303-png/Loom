from __future__ import annotations

from typing import Any

from .browser_runtime import BrowserRuntime as _BrowserRuntime
from .browser_transient import BrowserTransientInputPlatform


class BrowserRuntime(_BrowserRuntime):
    """Hardened Loom BrowserRuntime with one-shot typed-text payloads.

    The base Browser runtime already places URL secret scrubbing in front of the
    durable Runtime. This layer inserts BrowserTransientInputPlatform *inside* that
    boundary, so every model-produced browser_type payload is replaced by an opaque
    RAM reference before the base boundary or Session/event persistence sees it.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        boundary = self.platform
        delegate = getattr(boundary, "_delegate", None)
        if delegate is None:
            raise RuntimeError("browser durable secret boundary is unavailable")
        boundary._delegate = BrowserTransientInputPlatform(delegate)

    def consume_browser_type_text(self, value: str) -> str:
        consumer = getattr(self.platform, "consume_browser_type_text", None)
        if not callable(consumer):
            raise RuntimeError("browser transient input boundary is unavailable")
        return str(consumer(value))

    def browser_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        status = dict(super().browser_status(owner_session_id))
        status["typed_text_persistence"] = "transient_only"
        status["url_policy"] = (
            "execution-layer pre/post navigation; browser-use backend also enforces redirect/popup navigation"
        )
        return status

    def close(self) -> None:
        clearer = getattr(self.platform, "clear_browser_transient_inputs", None)
        if callable(clearer):
            clearer()
        super().close()


__all__ = ["BrowserRuntime"]
