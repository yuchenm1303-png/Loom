from __future__ import annotations

import json

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult
from .web_search import WebSearchProvider


def web_search_tools(provider: WebSearchProvider | None) -> tuple[AgentTool, ...]:
    def status(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        _ = context, arguments
        name = provider.provider_name if provider is not None else "disabled"
        return ToolResult(
            ok=True,
            content=(
                f"Web search provider: {name}."
                if provider is not None
                else "Web search is not configured. Set a supported search provider API key."
            ),
            data={"enabled": provider is not None, "provider": name},
        )

    tools: list[AgentTool] = [
        AgentTool(
            name="web_search_status",
            description="Report whether external web search is configured and which provider is active. Does not expose credentials.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=status,
            effect=ToolEffect.READ_ONLY,
        )
    ]

    if provider is not None:
        def search(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
            context.raise_if_cancelled()
            query = str(arguments.get("query") or "").strip()
            count = int(arguments.get("count") or 8)
            response = provider.search(query, count=count)
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
                    "No web results found."
                    if not rows
                    else json.dumps(rows, ensure_ascii=False, indent=2)
                ),
                data=payload,
            )

        tools.append(
            AgentTool(
                name="web_search",
                description=(
                    "Search the public web through Loom's configured search provider. This performs an external "
                    "network request and returns ranked titles, URLs, and snippets. Treat snippets as source leads, "
                    "not as verified facts; prefer multiple sources for consequential claims."
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
            )
        )
    return tuple(tools)


__all__ = ["web_search_tools"]
