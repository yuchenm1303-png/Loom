"""Credential-backed search shared by authenticated Loom devices."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SearchServiceError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def search(query: str, count: int) -> dict:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise SearchServiceError(503, "SEARCH_UNCONFIGURED", "Loom shared search is not configured.")
    request = Request(
        "https://api.tavily.com/search",
        data=json.dumps({"query": query, "max_results": count, "search_depth": "basic",
                         "include_answer": False, "include_images": False,
                         "include_raw_content": False}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("oversized response")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("invalid response")
        results = []
        for row in payload["results"][:count]:
            if not isinstance(row, dict):
                raise ValueError("invalid result")
            url = str(row.get("url") or "")
            parsed = urlsplit(url)
            title = str(row.get("title") or "").strip()
            if not title or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid result")
            results.append({"title": title[:1000], "url": url[:4000],
                            "snippet": str(row.get("content") or "")[:6000],
                            "source": parsed.hostname, "score": row.get("score")})
        return {"provider": "loom", "query": query, "results": results,
                "request_id": str(payload.get("request_id") or "")[:200]}
    except HTTPError as exc:
        if exc.code in {429, 432, 433}:
            raise SearchServiceError(429, "SEARCH_RATE_LIMITED", "Shared search quota or rate limit reached. Try again later.") from None
        raise SearchServiceError(502, "SEARCH_UPSTREAM_ERROR", "The search provider rejected the request.") from None
    except (URLError, TimeoutError, ValueError, UnicodeDecodeError):
        raise SearchServiceError(502, "SEARCH_UNAVAILABLE", "The search provider is unavailable or returned an invalid response.") from None
