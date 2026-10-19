"""Real official-SDK stdio fixture used by contract tests, never public network I/O."""

import asyncio

from mcp.server.fastmcp import FastMCP

server = FastMCP("fixture")


@server.tool()
def add(a: int, b: int) -> dict[str, int]:
    """Add two integers."""
    return {"value": a + b}


@server.tool()
async def slow() -> str:
    await asyncio.sleep(10)
    return "finished"


if __name__ == "__main__":
    server.run()
