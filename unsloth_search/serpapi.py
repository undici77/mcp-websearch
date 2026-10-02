# SPDX-License-Identifier: AGPL-3.0-only
# Fork-owned tier 3 — NOT from upstream. Paid escape hatch when the free tiers are throttled.

"""SerpApi fallback, in the tier result format so the model cannot tell which served it."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .clip import clip, snippet_max, title_max
from .web_access_policy import check_url_access

KEY_FILE = os.path.expanduser("~/.qwen/websearch-keys.json")
ENGINE = "google"
HL = "en"
GL = "us"


def serpapi_api_key() -> str:
    # Env first: scripts/check-egress.mjs only admits serpapi.com when this exists.
    key = (os.environ.get("SERPAPI_API_KEY") or "").strip()
    if key:
        return key
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            return str(json.load(fh).get("serpapi", "")).strip()
    except (OSError, ValueError, AttributeError):
        return ""


def serpapi_search(
    query: str,
    max_results: int = 5,
    timeout: int | None = None,
    cancel_event = None,
    website_policy: dict | None = None,
) -> str | None:
    """Results, or None when unavailable/empty — the caller then keeps upstream's failure copy."""
    key = serpapi_api_key()
    if not key or (cancel_event is not None and cancel_event.is_set()):
        return None

    params = urllib.parse.urlencode(
        {
            "q": query,
            "engine": ENGINE,
            "hl": HL,
            "gl": GL,
            "num": max_results,
            "api_key": key,
        }
    )
    request = urllib.request.Request(
        f"https://serpapi.com/search?{params}",
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout = timeout or 30) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None

    parts = []
    for row in payload.get("organic_results") or []:
        if len(parts) >= max_results:
            break
        href = str(row.get("link") or "").strip()
        allowed, _reason, _hostname = check_url_access(href, website_policy)
        if not allowed:
            continue
        title = clip(row.get("title"), title_max())
        snippet = clip(row.get("snippet"), snippet_max())
        parts.append(f"Title: {title}\nURL: {href}\nSnippet: {snippet}")

    if not parts:
        return None
    text = "\n\n---\n\n".join(parts)
    text += (
        "\n\n---\n\nIMPORTANT: These are only short snippets. "
        "To get the full page content, call web_search with "
        'the url parameter (e.g. {"url": "<URL>"}).'
    )
    return text
