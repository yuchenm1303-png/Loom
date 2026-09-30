from __future__ import annotations

import html
import json
import os
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_MAX_QUERY_CHARS = 400
_MAX_QUERY_WORDS = 50
_MAX_RESULTS = 20
_MAX_RESPONSE_BYTES = 2_000_000
_DEFAULT_TIMEOUT_SECONDS = 20.0
_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_DDG_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"


class WebSearchError(RuntimeError):
    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    source: str
    score: float | None = None

    def __post_init__(self) -> None:
        title = str(self.title or "").strip()
        url = str(self.url or "").strip()
        snippet = str(self.snippet or "").strip()
        source = str(self.source or "").strip()
        if not title or not url:
            raise ValueError("web search result requires title and url")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("web search result URL must be absolute http/https")
        object.__setattr__(self, "title", title[:1000])
        object.__setattr__(self, "url", url[:4000])
        object.__setattr__(self, "snippet", snippet[:6000])
        object.__setattr__(self, "source", source[:500] or parsed.hostname or parsed.netloc)
        if self.score is not None:
            object.__setattr__(self, "score", float(self.score))

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class WebSearchResponse:
    provider: str
    query: str
    results: tuple[WebSearchResult, ...]
    request_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "query": self.query,
            "request_id": self.request_id,
            "results": [result.to_dict() for result in self.results],
        }


class WebSearchProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        ...


JSONTransport = Callable[[str, str, Mapping[str, str], bytes | None, float], Mapping[str, Any]]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never forward provider credentials across an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _validate_query(query: str) -> str:
    text = " ".join(str(query or "").split())
    if not text:
        raise ValueError("web search query must not be empty")
    if len(text) > _MAX_QUERY_CHARS:
        raise ValueError(f"web search query exceeds {_MAX_QUERY_CHARS} characters")
    if len(text.split()) > _MAX_QUERY_WORDS:
        raise ValueError(f"web search query exceeds {_MAX_QUERY_WORDS} words")
    return text


def _validate_count(count: int) -> int:
    value = int(count)
    if not 1 <= value <= _MAX_RESULTS:
        raise ValueError(f"web search count must be within 1..{_MAX_RESULTS}")
    return value


def _default_json_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebSearchError("web search provider endpoint must be absolute HTTPS")
    request = Request(
        url=url,
        data=body,
        headers=dict(headers),
        method=str(method or "GET").upper(),
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=float(timeout_seconds)) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > _MAX_RESPONSE_BYTES:
                raise WebSearchError("web search provider response is too large")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise WebSearchError("web search provider response is too large")
    except HTTPError as exc:
        if 300 <= int(exc.code) < 400:
            raise WebSearchError("web search provider redirect was refused") from exc
        code = "rate_limited" if exc.code == 429 else "authentication" if exc.code in {401, 403} else "provider_error"
        hint = " Check the search API key and permissions." if code == "authentication" else " Retry later or check the search API quota." if code == "rate_limited" else ""
        raise WebSearchError(f"web search provider returned HTTP {exc.code}.{hint}", code=code) from exc
    except URLError as exc:
        reason = type(getattr(exc, "reason", None)).__name__ or "network error"
        raise WebSearchError(f"web search provider request failed: {reason}") from exc
    except TimeoutError as exc:
        raise WebSearchError("web search provider request timed out") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebSearchError("web search provider returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise WebSearchError("web search provider JSON root must be an object")
    return payload



def _default_text_transport(url: str, timeout_seconds: float) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebSearchError("web search provider endpoint must be absolute HTTPS")
    request = Request(
        url=url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "User-Agent": "Loom-Agent/0.1",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=float(timeout_seconds)) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > _MAX_RESPONSE_BYTES:
                raise WebSearchError("web search provider response is too large")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise WebSearchError("web search provider response is too large")
    except HTTPError as exc:
        if 300 <= int(exc.code) < 400:
            raise WebSearchError("web search provider redirect was refused") from exc
        code = "blocked" if exc.code in {403, 429} else "provider_error"
        raise WebSearchError(f"DuckDuckGo search returned HTTP {exc.code}; search failed, not zero results.", code=code) from exc
    except URLError as exc:
        reason = type(getattr(exc, "reason", None)).__name__ or "network error"
        raise WebSearchError(f"web search provider request failed: {reason}") from exc
    except TimeoutError as exc:
        raise WebSearchError("web search provider request timed out") from exc
    return raw.decode("utf-8", errors="replace")


def _decode_duckduckgo_href(value: str) -> str:
    href = html.unescape(str(value or "").strip())
    if not href:
        return ""
    parsed = urlsplit(href)
    host = parsed.hostname or ""
    if (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and parsed.path.startswith("/l/"):
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
        self._capture = ""
        self._pieces: list[str] = []
        self._capture_tag = ""
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        classes = set(str(attrs_map.get("class") or "").split())
        if self._capture:
            if tag == self._capture_tag:
                self._capture_depth += 1
            return
        if tag == "a" and classes.intersection({"result__a", "result-link"}):
            if self._current and self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
            self._current = {
                "title": "",
                "url": _decode_duckduckgo_href(attrs_map.get("href", "")),
                "snippet": "",
            }
            self._capture = "title"
            self._capture_tag = tag
            self._capture_depth = 1
            self._pieces = []
            return
        if self._current is not None and tag in {"a", "div", "span", "td"} and (
            "result__snippet" in classes or "result-snippet" in classes
        ):
            self._capture = "snippet"
            self._capture_tag = tag
            self._capture_depth = 1
            self._pieces = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._pieces.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        if tag != self._capture_tag:
            return
        self._capture_depth -= 1
        if self._capture_depth:
            return
        value = " ".join("".join(self._pieces).split())
        if self._current is not None and value:
            existing = self._current.get(self._capture, "")
            self._current[self._capture] = " ".join(part for part in (existing, value) if part)
        if tag == "a" and self._capture == "title":
            self._capture = ""
            self._pieces = []
        elif self._capture == "snippet":
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
        if not title or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        key = target.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "url": target, "snippet": snippet})
        if len(rows) >= limit:
            break
    return rows


TextTransport = Callable[[str, float], str]


def _duckduckgo_page_results(markup: str, *, limit: int) -> list[dict[str, str]]:
    page = markup.casefold()
    if any(marker in page for marker in (
        "challenge-form", "anomaly.js", "anomaly-modal", "bots use duckduckgo",
    )):
        raise WebSearchError(
            "DuckDuckGo blocked automated search with a verification page. "
            "Configure a Brave or Tavily API key in Web Search settings. "
            "This is a search failure, not evidence that no web sources exist.",
            code="blocked",
        )
    rows = _parse_duckduckgo_results(markup, limit=limit)
    if rows:
        return rows
    if any(marker in page for marker in (
        'class="no-results', "no results found for", "no more results",
    )):
        return []
    raise WebSearchError(
        "DuckDuckGo returned an unrecognized search page; results could not be parsed. "
        "Configure a Brave or Tavily API key in Web Search settings. "
        "Do not interpret this failure as no web results.",
        code="invalid_response",
    )


class DuckDuckGoWebSearchProvider:
    """Keyless public-web fallback used by Loom desktop/source builds."""

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: TextTransport | None = None,
    ) -> None:
        self.timeout_seconds = max(1.0, min(120.0, float(timeout_seconds)))
        self._transport = transport or _default_text_transport
        # Agent tool batches can invoke several searches concurrently. Avoid
        # hammering a public HTML endpoint after its first verification page.
        self._search_lock = threading.Lock()
        self._blocked_until = 0.0
        self.last_error = ""

    @property
    def provider_name(self) -> str:
        return "duckduckgo"

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        text = _validate_query(query)
        limit = _validate_count(count)
        with self._search_lock:
            if time.monotonic() < self._blocked_until:
                raise WebSearchError(self.last_error, code="blocked")
            try:
                response = self._search(text, limit)
            except WebSearchError as exc:
                self.last_error = str(exc)
                if exc.code == "blocked":
                    self._blocked_until = time.monotonic() + 60.0
                raise
            self.last_error = ""
            self._blocked_until = 0.0
            return response

    def _search(self, text: str, limit: int) -> WebSearchResponse:
        params = urlencode({"q": text})
        rows: list[dict[str, str]] = []
        last_error: Exception | None = None
        for endpoint in (_DDG_HTML_ENDPOINT, _DDG_LITE_ENDPOINT):
            try:
                markup = self._transport(endpoint + "?" + params, self.timeout_seconds)
                rows = _duckduckgo_page_results(markup, limit=limit)
                # An explicitly empty results page is a valid response too.
                last_error = None
                break
            except WebSearchError as exc:
                if exc.code == "blocked":
                    raise
                last_error = exc
        if not rows and last_error is not None:
            if isinstance(last_error, WebSearchError):
                raise last_error
            raise WebSearchError(str(last_error)) from last_error

        results = tuple(
            WebSearchResult(
                title=row["title"],
                url=row["url"],
                snippet=row.get("snippet", ""),
                source=urlsplit(row["url"]).hostname or urlsplit(row["url"]).netloc,
            )
            for row in rows
        )
        return WebSearchResponse(
            provider=self.provider_name,
            query=text,
            results=results,
            request_id="",
        )


class BraveWebSearchProvider:
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: JSONTransport | None = None,
    ) -> None:
        secret = str(api_key or "").strip()
        if not secret:
            raise ValueError("Brave Search API key must not be empty")
        self._api_key = secret
        self.timeout_seconds = max(1.0, min(120.0, float(timeout_seconds)))
        self._transport = transport or _default_json_transport

    @property
    def provider_name(self) -> str:
        return "brave"

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        text = _validate_query(query)
        limit = _validate_count(count)
        url = self.endpoint + "?" + urlencode({"q": text, "count": limit})
        payload = self._transport(
            "GET",
            url,
            {
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
                "User-Agent": "Loom-Agent/0.1",
            },
            None,
            self.timeout_seconds,
        )
        if payload.get("error"):
            raise WebSearchError("Brave Search returned an API error", code="provider_error")
        web = payload.get("web")
        if web is not None and not isinstance(web, dict):
            raise WebSearchError("Brave Search response contains invalid web results", code="invalid_response")
        # Brave may omit the web section for a valid query with no matches.
        if web is None and not isinstance(payload.get("query"), dict):
            raise WebSearchError("Brave Search returned an unrecognized response", code="invalid_response")
        rows = web.get("results", []) if isinstance(web, dict) else []
        if not isinstance(rows, list):
            raise WebSearchError("Brave Search response contains invalid web results")
        results: list[WebSearchResult] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            target = str(row.get("url") or "").strip()
            if not title or not target:
                continue
            snippet_parts = [str(row.get("description") or "").strip()]
            extra = row.get("extra_snippets")
            if isinstance(extra, list):
                snippet_parts.extend(str(item).strip() for item in extra[:3] if str(item).strip())
            parsed = urlsplit(target)
            results.append(
                WebSearchResult(
                    title=title,
                    url=target,
                    snippet="\n".join(part for part in snippet_parts if part),
                    source=parsed.hostname or parsed.netloc,
                )
            )
        query_info = payload.get("query")
        request_id = ""
        if isinstance(query_info, dict):
            request_id = str(query_info.get("request_id") or "")
        return WebSearchResponse(
            provider=self.provider_name,
            query=text,
            results=tuple(results),
            request_id=request_id,
        )


class TavilyWebSearchProvider:
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: JSONTransport | None = None,
    ) -> None:
        secret = str(api_key or "").strip()
        if not secret:
            raise ValueError("Tavily API key must not be empty")
        self._api_key = secret
        self.timeout_seconds = max(1.0, min(120.0, float(timeout_seconds)))
        self._transport = transport or _default_json_transport

    @property
    def provider_name(self) -> str:
        return "tavily"

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        text = _validate_query(query)
        limit = _validate_count(count)
        body = json.dumps(
            {
                "query": text,
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
                "include_images": False,
                "include_raw_content": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        payload = self._transport(
            "POST",
            self.endpoint,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "Loom-Agent/0.1",
            },
            body,
            self.timeout_seconds,
        )
        if payload.get("error") or "results" not in payload:
            raise WebSearchError("Tavily Search returned an API error or unrecognized response", code="invalid_response")
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise WebSearchError("Tavily response contains invalid results")
        results: list[WebSearchResult] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            target = str(row.get("url") or "").strip()
            if not title or not target:
                continue
            parsed = urlsplit(target)
            score = row.get("score")
            results.append(
                WebSearchResult(
                    title=title,
                    url=target,
                    snippet=str(row.get("content") or ""),
                    source=parsed.hostname or parsed.netloc,
                    score=float(score) if isinstance(score, (int, float)) else None,
                )
            )
        return WebSearchResponse(
            provider=self.provider_name,
            query=text,
            results=tuple(results),
            request_id=str(payload.get("request_id") or ""),
        )


class LoomWebSearchProvider:
    """Authenticated desktop bridge. Tavily credentials never reach Python."""

    provider_name = "loom"

    def __init__(self, url: str, token: str, *, timeout_seconds: float = 20.0) -> None:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.path != "/search" or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Loom search requires the authenticated desktop loopback bridge")
        if not token:
            raise ValueError("Loom search requires a desktop bridge token")
        self._url, self._token = url, token
        self.timeout_seconds = timeout_seconds
        self.last_error = ""

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        text, limit = _validate_query(query), _validate_count(count)
        request = Request(self._url, data=json.dumps({"query": text, "count": limit}).encode(),
                          headers={"Authorization": "Bearer " + self._token,
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with build_opener(_NoRedirectHandler()).open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError("oversized response")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("invalid response")
            results = tuple(WebSearchResult(
                title=row["title"], url=row["url"], snippet=row.get("snippet", ""),
                source=row.get("source", ""), score=row.get("score"),
            ) for row in payload["results"][:limit])
        except HTTPError as exc:
            code = "authentication" if exc.code in {401, 403} else "rate_limited" if exc.code == 429 else "provider_error"
            hint = "Sign in to Loom to use shared search." if code == "authentication" else "Shared search is rate limited; retry later." if code == "rate_limited" else "Loom shared search is unavailable."
            self.last_error = hint
            raise WebSearchError(hint, code=code) from None
        except (URLError, TimeoutError):
            self.last_error = "Could not reach Loom shared search. Check the account service connection."
            raise WebSearchError(self.last_error, code="unavailable") from None
        except (ValueError, UnicodeDecodeError, TypeError, KeyError):
            self.last_error = "Loom shared search returned an invalid response."
            raise WebSearchError(self.last_error, code="invalid_response") from None
        self.last_error = ""
        return WebSearchResponse(provider=self.provider_name, query=text, results=results,
                                 request_id=str(payload.get("request_id") or ""))


# Provider names the desktop settings page may store. "auto" is resolved by the
# caller and means "use whatever the environment or stored credentials offer,
# otherwise Loom's keyless public fallback".
DISABLED_PROVIDER_NAMES = frozenset({"off", "none", "disabled"})
PUBLIC_PROVIDER_NAMES = frozenset({"duckduckgo", "ddg", "public", "builtin", "default"})
CREDENTIAL_PROVIDER_NAMES = frozenset({"brave", "tavily"})


def web_search_provider_from_values(
    provider: str,
    api_key: str = "",
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    transport: JSONTransport | None = None,
) -> WebSearchProvider | None:
    """Build a provider from an explicit provider name plus an optional key.

    This is the settings-page path. The chosen provider is stored in Loom's own
    settings file while the key lives in the OS credential store, so a stored
    settings snapshot never contains secret material.

    A name/key mismatch raises ValueError, which is how "Tavily selected but no
    key found anywhere" fails closed instead of answering from model knowledge.
    """

    name = str(provider or "").strip().casefold()
    secret = str(api_key or "").strip()
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT_SECONDS
    timeout = max(1.0, min(120.0, timeout))

    if name in DISABLED_PROVIDER_NAMES:
        return None
    if name in PUBLIC_PROVIDER_NAMES:
        # The keyless public provider is installed as a module attribute by
        # app.runtime_capability_defaults; looking it up through globals keeps
        # this module importable on its own.
        factory = globals().get("DuckDuckGoWebSearchProvider")
        if factory is None:  # pragma: no cover - depends on the defaults layer
            raise ValueError("Loom's public web search provider is unavailable")
        return factory(timeout_seconds=timeout)
    if name == "brave":
        if not secret:
            raise ValueError("Brave Search requires an API key")
        return BraveWebSearchProvider(secret, timeout_seconds=timeout, transport=transport)
    if name == "tavily":
        if not secret:
            raise ValueError("Tavily Search requires an API key")
        return TavilyWebSearchProvider(secret, timeout_seconds=timeout, transport=transport)
    raise ValueError(f"unsupported Loom web search provider: {name or '<empty>'}")


def web_search_provider_from_env(
    env: Mapping[str, str] | None = None,
    *,
    transport: JSONTransport | None = None,
) -> WebSearchProvider | None:
    values = os.environ if env is None else env
    provider = str(values.get("LOOM_WEB_SEARCH_PROVIDER") or "").strip().casefold()
    generic_key = str(values.get("LOOM_WEB_SEARCH_API_KEY") or "").strip()
    brave_key = str(values.get("BRAVE_SEARCH_API_KEY") or "").strip()
    tavily_key = str(values.get("TAVILY_API_KEY") or "").strip()
    timeout_text = str(values.get("LOOM_WEB_SEARCH_TIMEOUT") or "").strip()
    timeout = float(timeout_text) if timeout_text else _DEFAULT_TIMEOUT_SECONDS

    if provider in {"off", "none", "disabled"}:
        return None
    relay_url = str(values.get("LOOM_SEARCH_RELAY_URL") or "").strip()
    relay_token = str(values.get("LOOM_SEARCH_RELAY_TOKEN") or "").strip()
    if provider == "loom":
        return LoomWebSearchProvider(relay_url, relay_token, timeout_seconds=timeout)
    if provider in {"duckduckgo", "ddg", "public", "default", "builtin"}:
        return DuckDuckGoWebSearchProvider(timeout_seconds=timeout)
    if not provider:
        if brave_key:
            provider = "brave"
        elif tavily_key:
            provider = "tavily"
        elif generic_key:
            raise ValueError(
                "LOOM_WEB_SEARCH_API_KEY requires LOOM_WEB_SEARCH_PROVIDER=brave or tavily"
            )
        else:
            if relay_url:
                return LoomWebSearchProvider(relay_url, relay_token, timeout_seconds=timeout)
            return DuckDuckGoWebSearchProvider(timeout_seconds=timeout)

    if provider == "brave":
        key = generic_key or brave_key
        if not key:
            raise ValueError("Brave web search requires LOOM_WEB_SEARCH_API_KEY or BRAVE_SEARCH_API_KEY")
        return BraveWebSearchProvider(key, timeout_seconds=timeout, transport=transport)
    if provider == "tavily":
        key = generic_key or tavily_key
        if not key:
            raise ValueError("Tavily web search requires LOOM_WEB_SEARCH_API_KEY or TAVILY_API_KEY")
        return TavilyWebSearchProvider(key, timeout_seconds=timeout, transport=transport)
    raise ValueError(f"unsupported Loom web search provider: {provider}")


__all__ = [
    "BraveWebSearchProvider",
    "DuckDuckGoWebSearchProvider",
    "JSONTransport",
    "TextTransport",
    "TavilyWebSearchProvider",
    "WebSearchError",
    "WebSearchProvider",
    "WebSearchResponse",
    "WebSearchResult",
    "web_search_provider_from_env",
    "web_search_provider_from_values",
]
