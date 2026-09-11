from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
from loom_model_bridge import _resolve


def _read() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("model admin input must be a JSON object")
    return payload


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _profile_payload(entry: Any) -> dict[str, Any]:
    return {
        "selection": entry.selection,
        "id": entry.model_id,
        "kind": "saved",
        "name": entry.display_name,
        "adapter": entry.adapter.value,
        "baseUrl": entry.base_url,
        "model": entry.model,
        "vision": bool(entry.vision),
    }


def _update(store: ModelConfigStore, payload: dict[str, Any]) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip()
    saved = store.model_for_selection(selection)
    if saved is None:
        raise ValueError("only saved model connections can be edited")
    entry = store.update_model(
        saved.model_id,
        display_name=str(payload.get("name") or saved.display_name),
        adapter=str(payload.get("adapter") or saved.adapter.value),
        base_url=str(payload.get("baseUrl") if "baseUrl" in payload else saved.base_url),
        model=str(payload.get("model") or saved.model),
        api_key=str(payload.get("apiKey") or "") or None,
        vision=bool(payload.get("vision", saved.vision)),
    )
    return _profile_payload(entry)


def _model_list_url(base_url: str, provider: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base and provider == "openai":
        base = "https://api.openai.com/v1"
    if not base:
        raise ValueError("model endpoint has no Base URL")
    return f"{base}/models"


def _test(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip()
    if not selection:
        raise ValueError("model selection is required")
    resolved = _resolve(store, reasoning_store, selection_store, selection)
    url = _model_list_url(str(resolved.get("baseUrl") or ""), str(resolved.get("provider") or ""))
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {str(resolved.get('apiKey') or '').strip()}",
            "Accept": "application/json",
            "User-Agent": "Loom/model-test",
        },
    )
    started = time.perf_counter()
    status = 0
    body = b""
    try:
        with urllib.request.urlopen(request, timeout=6.0) as response:
            status = int(response.status or 200)
            body = response.read(512 * 1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        body = exc.read(64 * 1024)
        if status >= 400:
            detail = body.decode("utf-8", errors="replace")[:280]
            raise RuntimeError(f"endpoint returned HTTP {status}: {detail or exc.reason}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"could not reach model endpoint: {exc}") from exc

    latency_ms = round((time.perf_counter() - started) * 1000)
    listed = False
    discovered = 0
    try:
        decoded = json.loads(body.decode("utf-8")) if body else {}
        data = decoded.get("data") if isinstance(decoded, dict) else None
        if isinstance(data, list):
            ids = [str(item.get("id") or "") for item in data if isinstance(item, dict)]
            discovered = len([item for item in ids if item])
            target = str(resolved.get("model") or "").casefold()
            listed = any(item.casefold() == target for item in ids if item)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    saved = store.model_for_selection(selection)
    vision = bool(saved.vision) if saved is not None else True
    reasoning = resolved.get("reasoning")
    return {
        "ok": True,
        "selection": selection,
        "status": status,
        "latencyMs": latency_ms,
        "endpoint": url,
        "model": str(resolved.get("model") or ""),
        "modelListed": listed,
        "discoveredModels": discovered,
        "capabilities": {
            "chat": True,
            "streaming": True,
            "vision": vision,
            "reasoning": isinstance(reasoning, dict),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"update", "test"}:
        sys.stderr.write("usage: loom_model_admin.py {update|test}\n")
        return 2
    try:
        store = ModelConfigStore()
        payload = _read()
        if args[0] == "update":
            result = _update(store, payload)
        else:
            result = _test(
                store,
                ReasoningConfigStore(store.home),
                ModelSelectionStore(store.home),
                payload,
            )
        _write({"ok": True, "result": result})
        return 0
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
