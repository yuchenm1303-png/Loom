from __future__ import annotations

"""Default backend implementations for optional runtime capabilities.

The desktop settings UI has separate concepts for a user preference and runtime
readiness. Web Search and MCP used to remain in "runtime missing" unless the
operator preconfigured a provider/API key or at least one MCP server. That is a
bad desktop default: the switches should expose a useful backend immediately,
while still reporting whether external credentials or remote MCP servers were
found.
"""

import html
import importlib.abc
import importlib.machinery
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_TARGETS = {
    "app.agent_runtime.web_search",
    "app.agent_runtime.web_search_runtime",
}
_INSTALLED = False
_PATCHED: set[str] = set()

_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_DDG_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
_MAX_PUBLIC_SEARCH_BYTES = 2_000_000
_DEFAULT_PUBLIC_SEARCH_TIMEOUT = 20.0


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _timeout_from_env(values: Any) -> float:
    raw = str(values.get("LOOM_WEB_SEARCH_TIMEOUT") or "").strip()
    if not raw:
        return _DEFAULT_PUBLIC_SEARCH_TIMEOUT
    try:
        return max(1.0, min(120.0, float(raw)))
    except ValueError:
        return _DEFAULT_PUBLIC_SEARCH_TIMEOUT


def _public_fetch(url: str, *, timeout_seconds: float) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("public web search endpoint must be absolute HTTPS")
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "User-Agent": "Loom-Agent/0.1 (+https://github.com/yuchenm1303-png/Loom)",
        },
        method="GET",
    )
    try:
        with build_opener(_NoRedirectHandler()).open(request, timeout=timeout_seconds) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > _MAX_PUBLIC_SEARCH_BYTES:
                raise RuntimeError("public web search response is too large")
            raw = response.read(_MAX_PUBLIC_SEARCH_BYTES + 1)
            if len(raw) > _MAX_PUBLIC_SEARCH_BYTES:
                raise RuntimeError("public web search response is too large")
    except HTTPError as exc:
        if 300 <= int(exc.code) < 400:
            raise RuntimeError("public web search redirect was refused") from exc
        raise RuntimeError(f"public web search returned HTTP {exc.code}") from exc
    except URLError as exc:
        reason = type(getattr(exc, "reason", None)).__name__ or "network error"
        raise RuntimeError(f"public web search request failed: {reason}") from exc
    return raw.decode("utf-8", errors="replace")


def _decode_duckduckgo_href(value: str) -> str:
    href = html.unescape(str(value or "").strip())
    if not href:
        return ""
    parsed = urlsplit(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        for key, item in parse_qsl(parsed.query, keep_blank_values=True):
            if key == "uddg" and item:
                return item
    if href.startswith("//"):
        return "https:" + href
    return href


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str = ""
        self._pieces: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        classes = set(str(attrs_map.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            href = _decode_duckduckgo_href(attrs_map.get("href", ""))
            self._current = {"title": "", "url": href, "snippet": ""}
            self._capture = "title"
            self._pieces = []
            return
        if self._current is not None and tag in {"a", "div", "span"} and (
            "result__snippet" in classes or "result-snippet" in classes
        ):
            self._capture = "snippet"
            self._pieces = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._pieces.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        text = " ".join("".join(self._pieces).split())
        if self._current is not None and text:
            existing = self._current.get(self._capture, "")
            self._current[self._capture] = " ".join(part for part in (existing, text) if part)
        if tag == "a" and self._capture == "title":
            if self._current and self._current.get("title") and self._current.get("url"):
                # The snippet may arrive after the title in DuckDuckGo HTML. Keep
                # collecting it on the same current record until a new result starts.
                pass
            self._capture = ""
            self._pieces = []
        elif self._capture == "snippet" and tag in {"a", "div", "span"}:
            if self._current and self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
                self._current = None
            self._capture = ""
            self._pieces = []

    def close(self) -> None:
        super().close()
        if self._current and self._current.get("title") and self._current.get("url"):
            self.results.append(self._current)
            self._current = None


def _parse_duckduckgo_results(markup: str, *, limit: int) -> list[dict[str, str]]:
    parser = _DuckDuckGoHTMLParser()
    parser.feed(markup)
    parser.close()
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in parser.results:
        target = str(row.get("url") or "").strip()
        title = " ".join(str(row.get("title") or "").split())
        snippet = " ".join(str(row.get("snippet") or "").split())
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        key = target.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "url": target, "snippet": snippet})
        if len(rows) >= limit:
            break
    return rows


def _patch_web_search(module: ModuleType) -> None:
    if getattr(module, "_loom_public_default_search", False):
        return
    # Newer runtimes own the keyless DuckDuckGo provider directly in
    # app.agent_runtime.web_search. Keep this compatibility hook only for older
    # module shapes so import order can no longer decide whether search exists.
    if hasattr(module, "DuckDuckGoWebSearchProvider"):
        module._loom_public_default_search = True
        return

    class DuckDuckGoWebSearchProvider:
        endpoint = _DDG_HTML_ENDPOINT

        def __init__(self, *, timeout_seconds: float = _DEFAULT_PUBLIC_SEARCH_TIMEOUT) -> None:
            self.timeout_seconds = max(1.0, min(120.0, float(timeout_seconds)))

        @property
        def provider_name(self) -> str:
            return "duckduckgo"

        def search(self, query: str, *, count: int = 8):  # noqa: ANN001
            text = module._validate_query(query)
            limit = module._validate_count(count)
            params = urlencode({"q": text})
            last_error: Exception | None = None
            rows: list[dict[str, str]] = []
            for endpoint in (_DDG_HTML_ENDPOINT, _DDG_LITE_ENDPOINT):
                try:
                    markup = _public_fetch(endpoint + "?" + params, timeout_seconds=self.timeout_seconds)
                    rows = _parse_duckduckgo_results(markup, limit=limit)
                    if rows:
                        break
                except Exception as exc:  # pragma: no cover - network dependent
                    last_error = exc
                    continue
            if not rows and last_error is not None:
                raise module.WebSearchError(str(last_error)) from last_error

            results = []
            for row in rows:
                parsed = urlsplit(row["url"])
                results.append(
                    module.WebSearchResult(
                        title=row["title"],
                        url=row["url"],
                        snippet=row.get("snippet", ""),
                        source=parsed.hostname or parsed.netloc,
                    )
                )
            return module.WebSearchResponse(
                provider=self.provider_name,
                query=text,
                results=tuple(results),
                request_id="",
            )

    original = module.web_search_provider_from_env

    def web_search_provider_from_env(env: Any = None, *, transport: Any = None):  # noqa: ANN001
        values = os.environ if env is None else env
        provider = str(values.get("LOOM_WEB_SEARCH_PROVIDER") or "").strip().casefold()
        if provider in {"off", "none", "disabled"}:
            return None
        if provider in {"duckduckgo", "ddg", "public", "default", "builtin"}:
            return DuckDuckGoWebSearchProvider(timeout_seconds=_timeout_from_env(values))
        if provider:
            return original(env, transport=transport)
        configured = original(env, transport=transport)
        if configured is not None:
            return configured
        # Desktop default: no key required. Operators can still opt out with
        # LOOM_WEB_SEARCH_PROVIDER=off or choose Brave/Tavily explicitly.
        return DuckDuckGoWebSearchProvider(timeout_seconds=_timeout_from_env(values))

    module.DuckDuckGoWebSearchProvider = DuckDuckGoWebSearchProvider
    module.web_search_provider_from_env = web_search_provider_from_env
    exports = list(getattr(module, "__all__", []))
    if "DuckDuckGoWebSearchProvider" not in exports:
        exports.insert(0, "DuckDuckGoWebSearchProvider")
        module.__all__ = exports
    module._loom_public_default_search = True


def _patch_web_search_runtime(module: ModuleType) -> None:
    if getattr(module, "_loom_public_default_search_runtime", False):
        return
    web_search = sys.modules.get("app.agent_runtime.web_search")
    if web_search is not None:
        _patch_web_search(web_search)
        module.web_search_provider_from_env = web_search.web_search_provider_from_env
    module._loom_public_default_search_runtime = True


def _mcp_config_paths(runtime_home: str | Path = "") -> Iterable[Path]:
    yielded: set[Path] = set()

    def emit(path: str | Path | None):
        if not path:
            return
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return
        if resolved not in yielded:
            yielded.add(resolved)
            paths.append(resolved)

    paths: list[Path] = []
    emit(os.environ.get("LOOM_CONFIG"))
    if runtime_home:
        emit(Path(runtime_home) / "config.toml")
        emit(Path(runtime_home) / "mcp.json")
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        emit(Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else None)
        emit(Path(appdata) / "Cursor" / "mcp.json" if appdata else None)
    emit(Path.home() / ".config" / "Claude" / "claude_desktop_config.json")
    emit(Path.home() / ".cursor" / "mcp.json")
    return tuple(paths)


def patch(module: ModuleType) -> None:
    name = getattr(module, "__name__", "")
    if name in _PATCHED:
        return
    if name == "app.agent_runtime.web_search":
        _patch_web_search(module)
    elif name == "app.agent_runtime.web_search_runtime":
        _patch_web_search_runtime(module)
    _PATCHED.add(name)


class _RuntimeCapabilityDefaultsLoader(importlib.abc.Loader):
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
            raise ImportError(f"loader for {module.__name__} cannot execute modules")
        exec_module(module)
        patch(module)


class _RuntimeCapabilityDefaultsFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):  # noqa: ANN001
        if fullname not in _TARGETS:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _RuntimeCapabilityDefaultsLoader):
            return spec
        spec.loader = _RuntimeCapabilityDefaultsLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    finder = _RuntimeCapabilityDefaultsFinder()
    sys.meta_path.insert(0, finder)
    for name in tuple(_TARGETS):
        existing = sys.modules.get(name)
        if existing is not None:
            patch(existing)
    _INSTALLED = True


__all__ = ["install", "patch"]
