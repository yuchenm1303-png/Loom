"""Server-owned provider discovery. Never consume credentials or catalogs from clients."""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loom_image_capability import image_capability

from .catalog import GROUPS, OPENCODE_CONSOLE_FREE_MODELS, is_opencode_console_free, selection_for_model


# Provider routes are shared with the gateway; model additions do not
# require a desktop release. All secrets stay in the server environment.
from services.loom_model_gateway.routes import PROVIDERS


def parse_models(group_id: str, payload: dict) -> list[dict]:
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
        display = item.get("name") or (
            model.replace("-", " ").title().replace("Deepseek", "DeepSeek").replace("Mimo", "MiMo")
            if group_id == "opencode-console" else model
        )
        models[selection] = {"model_id": selection, "model": model, "name": str(display)[:240], "image_input": image_capability(item)}
    return list(models.values())


def parse_opencode_console_config(payload: dict) -> list[dict]:
    """Only import the Console-owned OpenAI Chat Completions provider.

    The workspace config can also contain Go, Responses, Anthropic, custom
    upstreams or user-connected providers. None may be routed through the
    Console Chat Completions gateway by assuming model IDs are interchangeable.
    """
    providers = payload.get("providers") if isinstance(payload, dict) else None
    provider = providers.get("opencode") if isinstance(providers, dict) else None
    if not isinstance(provider, dict):
        raise ValueError("Console configuration does not include opencode")
    package = str(provider.get("package") or "")
    settings = provider.get("settings")
    base_url = settings.get("baseURL") if isinstance(settings, dict) else None
    if "@ai-sdk/openai-compatible" not in package or base_url != "https://opencode.ai/inference/openai/v1":
        raise ValueError("Console config is not OpenAI Chat Completions")
    available = provider.get("models")
    if not isinstance(available, dict):
        raise ValueError("Console models are missing")
    # Free entrypoints also work without a key and stay visible even when the
    # service-account workspace config contains only a subset of the models.
    rows = {model: {"id": model} for model in OPENCODE_CONSOLE_FREE_MODELS}
    for model, info in available.items():
        if not isinstance(model, str) or not isinstance(info, dict):
            continue
        # Explicitly exclude per-model endpoint overrides that require
        # another transport (e.g. /responses).
        if info.get("api") not in (None, "chat/completions", "chat-completions"):
            continue
        rows[model] = {"id": model, "name": str(info.get("name") or model)}
    return parse_models("opencode-console", {"data": list(rows.values())})


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
        if not isinstance(data, dict):
            raise ValueError("invalid catalog manifest")
        # A manifest from a newer deployment may contain another provider.
        # Validate known groups independently in _refresh_provider.
        return {group: rows for group, rows in data.items() if group in PROVIDERS}

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
            if group_id == "opencode-console" and not credential:
                # Free models are seeded at DB initialization. Refresh must not
                # rewrite a stable catalog when another provider is unavailable.
                # If a former paid key was removed, retire its billed rows now.
                rows = [row for row in self.store.catalog()["models"] if row["group_id"] == group_id]
                if any(row["available"] and not is_opencode_console_free(row["model"]) for row in rows):
                    free_models = parse_models(group_id, {
                        "data": [{"id": model} for model in OPENCODE_CONSOLE_FREE_MODELS]
                    })
                    self.store.replace_provider_catalog(group_id, free_models, "manifest")
                return
            if not credential:
                self.store.catalog_failure(group_id, "Discovery credential is not configured; bundled or last successful catalog retained.")
                return
            base = next((self.environ[name].strip() for name in url_names if self.environ.get(name, "").strip()), base)
            if group_id == "opencode-console":
                models = parse_opencode_console_config(
                    self.fetch("https://opencode.ai/console/api/v2/config", credential)
                )
            else:
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
