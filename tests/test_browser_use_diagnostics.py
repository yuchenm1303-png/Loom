from __future__ import annotations

import json

import pytest

import app.agent_runtime.browser_use_backend as browser_use_backend_module
from app.agent_runtime.browser_diagnostics import BrowserDiagnosticLog, summarize_browser_state_payload
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.browser_use_backend import BrowserUseBackend


class FakeBrowserUseDiagnosticsBackend(BrowserUseBackend):
    async def _start_async(self) -> BrowserPageState:
        return self._with_backend_page_info(
            BrowserPageState(
                url="https://example.com/?access_token=start-secret",
                title="Example",
                dom='[0] <input aria-label="Search">',
                tabs=({"tab_id": "tab-1", "url": "https://example.com/", "title": "Example"},),
            ),
            capture_mode="fake_start",
        )

    async def _type_async(self, index: int, text: str, *, clear: bool) -> BrowserPageState:
        return self._with_backend_page_info(
            BrowserPageState(
                url="https://example.com/form?session=typed-secret#id_token=header.payload.signature",
                title="Typed",
                dom=f'[0] <input value="{text}">',
                tabs=({"tab_id": "tab-1", "url": "https://example.com/form", "title": "Typed"},),
                page_info={"tab_id": "tab-1"},
            ),
            capture_mode="fake_type",
        )

    async def _screenshot_async(self, *, full_page: bool) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"


def test_browser_use_backend_diagnostics_redact_typed_text_and_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_use_backend_module.importlib.util, "find_spec", lambda name: object())
    diagnostics = BrowserDiagnosticLog(root=tmp_path)
    backend = FakeBrowserUseDiagnosticsBackend(
        options=BrowserLaunchOptions(),
        cdp_url="http://127.0.0.1:9222",
        diagnostics=diagnostics,
    )
    try:
        backend.start()
        backend.type_text(0, "super secret password", clear=True)
        backend.screenshot(full_page=False)
    finally:
        backend.close()

    payload = diagnostics.path.read_text(encoding="utf-8")
    assert "super secret password" not in payload
    assert "start-secret" not in payload
    assert "typed-secret" not in payload
    assert "header.payload.signature" not in payload
    assert "text_length" in payload
    assert "[omitted after browser_type]" in payload
    assert "cdp_endpoint_exposed" in payload
    assert "profile_path_exposed" in payload
    assert "browser_use.action.completed" in payload
    assert "bytes" in payload


def test_browser_state_summary_redacts_url_query_and_fragment_tokens():
    summary = summarize_browser_state_payload(
        {
            "url": "https://example.com/callback?access_token=topsecret&next=%2Fhome#id_token=header.payload.signature",
            "title": "Example",
            "dom": "visible text",
            "page_info": {"tab_id": "tab-1"},
        }
    )
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "topsecret" not in encoded
    assert "header.payload.signature" not in encoded
    assert "[REDACTED]" in encoded or "%5BREDACTED%5D" in encoded
