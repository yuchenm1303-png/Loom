from __future__ import annotations

import json

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult
from .web_search import WebSearchError, WebSearchProvider


def web_search_tools(provider: WebSearchProvider | None) -> tuple[AgentTool, ...]:
    def status(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        _ = context, arguments
        name = provider.provider_name if provider is not None else "disabled"
        reason = str(getattr(provider, "last_error", "") or "")
        return ToolResult(
            ok=not bool(reason),
            content=(
                f"Web search provider: {name}." + (f" Last search failed: {reason}" if reason else "")
                if provider is not None
                else "Web search is not configured. Set a supported search provider API key."
            ),
            data={"enabled": provider is not None, "provider": name,
                  "state": "error" if reason else "ready" if provider else "disabled", "reason": reason},
        )

    tools: list[AgentTool] = [
        AgentTool(
            name="web_search_status",
            description="Report whether external web search is configured and which provider is active. Does not expose credentials.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=status,
            effect=ToolEffect.READ_ONLY,
            supports_parallel_tool_calls=True,
        )
    ]

    if provider is not None:
        def search(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
            context.raise_if_cancelled()
            query = str(arguments.get("query") or "").strip()
            count = int(arguments.get("count") or 8)
            try:
                response = provider.search(query, count=count)
            except WebSearchError as exc:
                return ToolResult(
                    ok=False,
                    content=f"Web search failed: {exc}",
                    data={"provider": provider.provider_name, "query": query,
                          "state": "error", "errorCode": exc.code},
                )
            rows = []
            for index, result in enumerate(response.results, start=1):
                rows.append(
                    {
                        "index": index,
                        "title": result.title,
                        "url": result.url,
                        "snippet": result.snippet,
                        "source": result.source,
                        "score": result.score,
                    }
                )
            payload = {
                "provider": response.provider,
                "query": response.query,
                "request_id": response.request_id,
                "results": rows,
            }
            return ToolResult(
                ok=True,
                content=(
                    "Search completed with zero results for this query. "
                    "This does not establish that the topic has no public sources."
                    if not rows
                    else json.dumps(rows, ensure_ascii=False, indent=2)
                ),
                data=payload,
            )

        tools.append(
            AgentTool(
                name="web_search",
                description=(
                    "Search the public web through Loom's configured search provider. Use this for current, latest, "
                    "today, recent, news, price, release-date, and current-documentation lookups, and whenever the user "
                    "explicitly asks to search or check the web. This performs an external network request and returns "
                    "ranked titles, URLs, and snippets. Treat snippets as source leads, not as verified facts; prefer "
                    "multiple sources for consequential claims. Prefer this over browser automation when the task is "
                    "information retrieval rather than page interaction."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 400},
                        "count": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search,
                effect=ToolEffect.SENSITIVE,
            supports_parallel_tool_calls=True,
            )
        )
    return tuple(tools)


__all__ = ["web_search_tools"]
