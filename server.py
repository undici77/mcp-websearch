#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""MCP stdio server exposing Studio's web_search to Qwen Code.

One tool, `web_search`, with upstream's query|url duality and the same tiered engine allowlist.
The import shim covers both mcp SDK v1 (FastMCP) and v2 (MCPServer) so an SDK bump is not a breakage.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unsloth_search.search import web_search  # noqa: E402

try:  # mcp>=2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp<2
    from mcp.server.fastmcp import FastMCP as _Server

try:
    from mcp.types import ToolAnnotations
except ImportError:  # pragma: no cover
    ToolAnnotations = None

# Mirrors upstream's tier list so a client reading instructions sees the same promise the code keeps.
_SERVER_INSTRUCTIONS = (
    "Web search through an approved engine tier list. "
    "Tier 1: wikipedia, brave, duckduckgo, mojeek, startpage. "
    "Tier 2 (only if tier 1 returns nothing): grokipedia, google, yahoo. "
    "Tier 3 (only if tiers 1-2 both fail, and only when a SerpApi key is configured): serpapi.com. "
    "Engines outside those tiers are never contacted. "
    "Pass {query} to search, or {url} to read a full page found in the results."
)

_TOOL_DESCRIPTION = (
    "Search the web and fetch page content. Returns snippets for all results. "
    "Use the url parameter to fetch full page text from a specific URL."
)


app = _Server(
    name="websearch",
    title="Web Search",
    description=_TOOL_DESCRIPTION,
    instructions=_SERVER_INSTRUCTIONS,
)


@app.tool(
    name="web_search",
    title="Web search",
    description=_TOOL_DESCRIPTION,
    # Without this the SDK derives {"result": string} from the `-> str` return, emits it as
    # structuredContent, and the client renders the whole result a second time as raw JSON.
    structured_output = False,
    # Upstream keeps plain search always-safe but prompts when a url is supplied
    # (_web_search_fetches_url): egress to a host the model named. Leave the client's per-call
    # confirmation in force — do not register this server with --trust.
    annotations=(
        ToolAnnotations(readOnlyHint = True, openWorldHint = True, idempotentHint = True)
        if ToolAnnotations is not None
        else None
    ),
)
def web_search_tool(
    query: str = "",
    url: str = "",
    max_results: int = 5,
    timeout: int = 30,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> str:
    """Search the web, or read one page when ``url`` is given."""
    policy: dict = {}
    if allowed_domains:
        policy["allowedDomains"] = allowed_domains
    if blocked_domains:
        policy["blockedDomains"] = blocked_domains
    return web_search(
        query,
        max_results = max_results,
        timeout = timeout,
        url = url or None,
        website_policy = policy or None,
    )


def main() -> None:
    app.run(transport = os.environ.get("UNSLOTH_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
