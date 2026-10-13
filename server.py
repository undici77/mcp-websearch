#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""MCP stdio server exposing Studio's web_search to Qwen Code.

`web_search` mirrors upstream: query|url duality and the same tiered engine allowlist. `wayback` is
fork-owned (NOT upstream): archived copies through the same fetcher, never called automatically.
The import shim covers both mcp SDK v1 (FastMCP) and v2 (MCPServer) so an SDK bump is not a breakage.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unsloth_search.search import web_search  # noqa: E402
from unsloth_search.wayback import wayback_fetch, wayback_snapshots  # noqa: E402

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
    "Pass {query} to search, or {url} to read a full page found in the results. "
    "The separate wayback tool reads archived copies (Wayback Machine, then Arquivo.pt) "
    "of pages that are gone or changed, and lists their captures."
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


# Dead page, dead host, or a slow one: an archive may still hold it. Never on a Blocked: refusal
# (policy or non-public address), a binary placeholder, or a cancellation.
_ARCHIVE_HINT_RE = re.compile(r"^Failed to (fetch URL: (HTTP \d|timed out)|resolve host:)")
_WAYBACK_DESCRIPTION = (
    "Read an archived copy of a web page that is gone, changed, or unreachable "
    "(Wayback Machine first, then Arquivo.pt). Pass timestamp (YYYY, YYYYMMDD or YYYYMMDDhhmmss) "
    "for the capture closest to that date, or list_snapshots=true to see available captures."
)


def _policy(allowed_domains, blocked_domains) -> dict | None:
    policy: dict = {}
    if allowed_domains:
        policy["allowedDomains"] = allowed_domains
    if blocked_domains:
        policy["blockedDomains"] = blocked_domains
    return policy or None


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
    result = web_search(
        query,
        max_results = max_results,
        timeout = timeout,
        url = url or None,
        website_policy = _policy(allowed_domains, blocked_domains),
    )
    # A hint, not a fetch: archive egress happens only on a wayback call the client confirms.
    if url and url.strip() and _ARCHIVE_HINT_RE.match(result):
        result += f"\n\nAn archived copy may exist: call wayback with {json.dumps({'url': url.strip()})}."
    return result


@app.tool(
    name="wayback",
    title="Web archive",
    description=_WAYBACK_DESCRIPTION,
    structured_output = False,
    # Same egress class as url mode: a page the model named, read from a fixed archive host.
    annotations=(
        ToolAnnotations(readOnlyHint = True, openWorldHint = True, idempotentHint = True)
        if ToolAnnotations is not None
        else None
    ),
)
def wayback_tool(
    url: str,
    timestamp: str = "",
    list_snapshots: bool = False,
    from_date: str = "",
    to_date: str = "",
    limit: int = 10,
    archive: str = "",
    timeout: int = 30,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> str:
    """Read an archived copy of ``url``, or list its captures."""
    policy = _policy(allowed_domains, blocked_domains)
    timeout = min(max(int(timeout or 30), 1), 60)
    if list_snapshots:
        return wayback_snapshots(
            url,
            from_date = from_date,
            to_date = to_date,
            limit = limit,
            archive = archive,
            timeout = timeout,
            website_policy = policy,
        )
    return wayback_fetch(url, timestamp = timestamp, archive = archive, timeout = timeout, website_policy = policy)


def main() -> None:
    app.run(transport = os.environ.get("UNSLOTH_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
