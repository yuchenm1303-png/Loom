"""Supplement sparse OpenCode listings with exact endpoint/model modality data.

This never adds models or permissions. It only annotates models the configured
provider itself advertised. Custom upstreams never inherit public-host evidence.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from loom_image_capability import image_capability

CATALOG_URL = "https://models.dev/api.json"
_BASES = {
    "opencode-go": {"https://opencode.ai/zen/go/v1", "https://opencode.ai/inference/go/v1",
        "https://opencode.ai/inference/go/openai/v1"},
    "opencode-console": {"https://opencode.ai/zen/v1", "https://opencode.ai/inference/v1",
        "https://opencode.ai/inference/openai/v1"},
}
_PROVIDERS = {"opencode-go": "opencode-go", "opencode-console": "opencode"}


def _base(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    return value.rstrip("/")


class ImageCatalog:
    def __init__(self, fetch=None, clock=time.monotonic):
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.guard = threading.Lock()
        self.data = {}
        self.success_at = None
        self.attempt_at = None

    @staticmethod
    def _fetch():
        # Public metadata: never forward a provider or account credential here.
        request = urllib.request.Request(CATALOG_URL, headers={"Accept": "application/json", "User-Agent": "LoomModelCatalog/3"})
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("capability catalog too large")
        return json.loads(raw)

    def _models(self, group):
        with self.guard:
            now = self.clock()
            fresh = self.success_at is not None and now - self.success_at < 3600
            cooling = self.attempt_at is not None and now - self.attempt_at < 300
            if not fresh and not cooling:
                self.attempt_at = now
                try:
                    data = self.fetch()
                    if not isinstance(data, dict) or not any(isinstance(data.get(name, {}).get("models"), dict) for name in _PROVIDERS.values() if isinstance(data.get(name), dict)):
                        raise ValueError("invalid capability catalog")
                    self.data, self.success_at = data, now
                except Exception:
                    pass  # Failure affects enrichment only, never model discovery.
            if self.success_at is None or now - self.success_at >= 86400:
                return {}
            provider = self.data.get(_PROVIDERS[group], {})
            models = provider.get("models", {}) if isinstance(provider, dict) else {}
            return models if isinstance(models, dict) else {}

    def enrich(self, group, base, rows):
        if group not in _BASES or _base(base) not in _BASES[group]:
            return rows
        published = self._models(group)
        result = []
        for row in rows:
            current = row.get("image_input", {})
            # Explicit or conflicting upstream evidence wins; no name heuristics.
            if current.get("supported") is not None or current.get("source") == "conflicting-metadata":
                result.append(row)
                continue
            metadata = published.get(row["model"])
            if not isinstance(metadata, dict) or metadata.get("id") != row["model"]:
                result.append(row)
                continue
            provider = metadata.get("provider")
            if isinstance(provider, dict) and "api" in provider and _base(provider["api"]) not in _BASES[group]:
                result.append(row)
                continue
            evidence = image_capability(metadata)
            if evidence["supported"] is not None:
                result.append({**row, "image_input": {"supported": evidence["supported"], "source": "models.dev/" + _PROVIDERS[group] + ":modalities"}})
            else:
                result.append(row)
        return result
