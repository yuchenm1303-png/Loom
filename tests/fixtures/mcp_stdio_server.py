from __future__ import annotations

from mcp.server import MCPServer

mcp = MCPServer("Loom stdio fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Echo text through a real stdio subprocess."""
    return f"stdio:{text}"


if __name__ == "__main__":
    mcp.run()
