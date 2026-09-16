from __future__ import annotations

# Backward-compatible import surface. Browser routing is no longer an "auto
# fallback" policy: the production runtime now exposes explicit browser backends
# through BrowserBackendRegistryMixin. Keep the old class name as an alias so
# embedders/tests importing it do not break while the architecture migrates.
from .browser_backend_registry import BrowserBackendRegistryMixin


BrowserAutoPolicyMixin = BrowserBackendRegistryMixin


__all__ = ["BrowserAutoPolicyMixin", "BrowserBackendRegistryMixin"]
