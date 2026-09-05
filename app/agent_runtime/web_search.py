from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


_MAX_QUERY_CHARS = 400
_MAX_QUERY_WORDS = 50
_MAX_RESULTS = 20
_MAX_RESPONSE_BYTES = 2_000_000
_DEFAULT_TIMEOUT_SECONDS = 20.0


class WebSearchError(RuntimeError):
    pass


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
    request = Request(
        url=url,
        data=body,
        headers=dict(headers),
        method=str(method or "GET").upper(),
    )
    try:
        with urlopen(request, timeout=float(timeout_seconds)) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > _MAX_RESPONSE_BYTES:
                raise WebSearchError("web search provider response is too large")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise WebSearchError("web search provider response is too large")
    except HTTPError as exc:
        raise WebSearchError(f"web search provider returned HTTP {exc.code}") from exc
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
        web = payload.get("web")
        rows = web.get("results") if isinstance(web, dict) else []
        if rows is None:
            rows = []
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
        rows = payload.get("results") or []
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
            return None

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
    "JSONTransport",
    "TavilyWebSearchProvider",
    "WebSearchError",
    "WebSearchProvider",
    "WebSearchResponse",
    "WebSearchResult",
    "web_search_provider_from_env",
]
