"""Server-owned provider discovery. Never consume credentials or catalogs from clients."""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .catalog import GROUPS, selection_for_model


# Provider additions live here and in catalog.GROUPS; model additions do not
# require a desktop release. All secrets stay in the server environment.
PROVIDERS = {
    "minimax": ("https://api.minimaxi.com/v1", ("MINIMAX_API_KEY", "LOOM_PRIMARY_API_KEY"), ("LOOM_MINIMAX_BASE_URL", "MINIMAX_BASE_URL")),
    "deepseek": ("https://api.deepseek.com", ("DEEPSEEK_API_KEY", "LOOM_DEEPSEEK_API_KEY"), ("LOOM_DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL")),
    "ant-ling": ("https://api.ant-ling.com/v1", ("LOOM_ANT_LING_API_KEY", "ANT_LING_API_KEY"), ("LOOM_ANT_LING_BASE_URL",)),
    "opencode-go": ("https://opencode.ai/zen/go/v1", ("OPENCODE_GO_API_KEY", "LOOM_OPENCODE_GO_API_KEY"), ("LOOM_OPENCODE_GO_BASE_URL",)),
    "managed-relay": ("https://muxway.dev/v1", ("LOOM_RELAY_API_KEY", "SMIREL_RELAY_API_KEY", "LOOM_CQU_API_KEY"), ("LOOM_RELAY_BASE_URL", "SMIREL_RELAY_BASE_URL")),
}


def parse_models(group_id: str, payload: dict) -> list[dict[str, str]]:
    if isinstance(payload, dict) and any(payload.get(key) for key in ("has_more", "next_cursor", "next_page")):
        raise ValueError("partial provider listing cannot replace a complete catalog")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data or len(data) > 2000:
        raise ValueError("invalid or empty model listing")
    models = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("invalid provider model entry")
        model = item["id"].strip()
        selection = selection_for_model(group_id, model)
        if len(selection) > 240:
            raise ValueError("model selection exceeds limit")
        models[selection] = {"model_id": selection, "model": model, "name": str(item.get("name") or model)[:240]}
    return list(models.values())


class CatalogSynchronizer:
    def __init__(self, store, *, environ=None, fetch=None):
        self.store = store
        self.environ = os.environ if environ is None else environ
        self.fetch = fetch or self._fetch
        self.stopped = threading.Event()
        self.guard = threading.Lock()
        self.thread = None
        self.interval = max(30, float(self.environ.get("LOOM_MODEL_CATALOG_REFRESH_SECONDS", "300")))

    @staticmethod
    def _fetch(url: str, credential: str) -> dict:
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + credential, "Accept": "application/json", "User-Agent": "LoomModelCatalog/2",
        })
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("model listing too large")
        return json.loads(raw.decode("utf-8"))

    def _manifest(self):
        path = str(self.environ.get("LOOM_MODEL_CATALOG_MANIFEST", "")).strip()
        if not path:
            return {}
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(group not in PROVIDERS for group in data):
            raise ValueError("invalid catalog manifest")
        return data

    def refresh(self) -> bool:
        # Admin refreshes and the background worker coalesce rather than race.
        if not self.guard.acquire(blocking=False):
            return False
        try:
            try:
                manifest = self._manifest()
            except Exception:
                for group in GROUPS:
                    self.store.catalog_failure(group.id, "Catalog manifest is invalid; last successful catalog retained.")
                return False
            with ThreadPoolExecutor(max_workers=len(PROVIDERS), thread_name_prefix="model-discovery") as pool:
                list(pool.map(lambda group: self._refresh_provider(group, manifest), PROVIDERS))
            return True
        finally:
            self.guard.release()

    def _refresh_provider(self, group_id: str, manifest: dict) -> None:
        try:
            if group_id in manifest:
                models = parse_models(group_id, {"data": manifest[group_id]})
                self.store.replace_provider_catalog(group_id, models, "manifest")
                return
            base, key_names, url_names = PROVIDERS[group_id]
            credential = next((self.environ[name].strip() for name in key_names if self.environ.get(name, "").strip()), "")
            if not credential:
                self.store.catalog_failure(group_id, "Discovery credential is not configured; bundled or last successful catalog retained.")
                return
            base = next((self.environ[name].strip() for name in url_names if self.environ.get(name, "").strip()), base)
            models = parse_models(group_id, self.fetch(base.rstrip("/") + "/models", credential))
            self.store.replace_provider_catalog(group_id, models)
        except Exception:
            # Never persist raw upstream errors: URLs or response bodies may
            # carry credentials. Failure does not retire any models.
            self.store.catalog_failure(group_id, "Provider discovery failed; last successful catalog retained.")

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._loop, daemon=True, name="model-catalog-sync")
        self.thread.start()

    def _loop(self) -> None:
        while not self.stopped.is_set():
            self.refresh()
            self.stopped.wait(self.interval)

    def close(self) -> None:
        self.stopped.set()
        if self.thread:
            self.thread.join(10)
