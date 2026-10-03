# SPDX-License-Identifier: AGPL-3.0-only
# Adapted layer over the generated verbatim copy — see upstream_verbatim.py

"""Search the web through the approved engine tiers; falls back to SerpApi only when a key is set."""

from __future__ import annotations

import os
import time

from .clip import clip, snippet_max, title_max
from .fetch import fetch_page_text
from .serpapi import serpapi_search
from .upstream_verbatim import (  # noqa: F401  re-exported for callers and tests
    EMPTY_SEARCH_RESULTS,
    WEB_SEARCH_TOOL,
    _DDGS_EMPTY_SWEEP,
    _EXEC_TIMEOUT,
    _POLICY_OVERFETCH,
    _SEARCH_ENGINE_TIERS,
    _WEB_SEARCH_QUERY_ALIASES,
    _WEB_SEARCH_URL_ALIASES,
    _first_nonempty_arg,
    _resolve_engine_tiers,
    _resolve_web_search_args,
    _search_failure_message,
    canonicalize_web_search_arguments,
)
from .web_access_policy import check_url_access, scope_search_query


def _max_results(default: int = 5) -> int:
    try:
        value = int(os.environ.get("UNSLOTH_SEARCH_MAX_RESULTS", "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def web_search(
    query: str,
    max_results: int | None = None,
    timeout: int | None = None,
    url: str | None = None,
    cancel_event = None,
    website_policy: dict | None = None,
) -> str:
    """ADAPTED from upstream `_web_search`: same tier loop, same budget, same filter, same copy.

    ADAPTED out: `include_images` / `image_queries`, which feed Studio's opaque-id thumbnail registry.
    An MCP client has no thumbnail proxy to register ids against, so the whole branch is absent rather
    than half-present.
    """
    max_results = _max_results(max_results or 5)
    timeout = _EXEC_TIMEOUT if timeout is None else timeout

    # Direct URL fetch mode.
    if url and url.strip():
        fetch_timeout = 60 if timeout is None else min(timeout, 60)
        return fetch_page_text(
            url.strip(),
            timeout = fetch_timeout,
            cancel_event = cancel_event,
            website_policy = website_policy,
        )

    if not query or not query.strip():
        return "No query provided."
    # A disconnect sets cancel_event; DDGS.text() is blocking and cannot be interrupted mid-flight, so gate on either
    # side: skip an already-cancelled request, and discard results that land after the client has gone.
    if cancel_event is not None and cancel_event.is_set():
        return "Search cancelled."
    try:
        from ddgs import DDGS
        from ddgs.engines import ENGINES

        engine_tiers = _resolve_engine_tiers(ENGINES.get("text", {}))
        if not engine_tiers:
            return "Search failed: no approved search engine is available."

        effective_query = scope_search_query(query, website_policy)
        # The policy filters below, so ask for a deeper pool when one actually restricts: a page whose top hits are
        # all disallowed otherwise yields nothing even when valid results rank just under them. Test the domain lists,
        # not the dict: a run always stores a normalized policy, which is truthy even when unrestricted.
        restricted = any(
            (website_policy or {}).get(key) for key in ("allowedDomains", "blockedDomains")
        )
        wanted = max_results * _POLICY_OVERFETCH if restricted else max_results
        # ddgs applies `timeout` per client, as both the engine HTTP timeout and its fan-out wait, so
        # a client per tier would restart the budget and a 7s web_search could block ~14s.
        deadline = time.monotonic() + timeout if timeout else None
        client = DDGS(timeout = timeout)
        # ddgs signals an empty sweep by RAISING, so a tier's exception means try the next tier; the
        # last is re-raised for _search_failure_message to classify as a single-tier failure would be.
        results, last_error = [], None
        for backend in engine_tiers:
            if cancel_event is not None and cancel_event.is_set():
                return "Search cancelled."
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                client = DDGS(timeout = remaining)
            try:
                results = client.text(effective_query, max_results = wanted, backend = backend)
            except Exception as exc:  # noqa: BLE001 - re-raised below when no tier produced anything
                last_error = exc
                continue
            if results:
                break
        if not results and last_error is not None:
            # Tier 3 (fork, not upstream): SerpApi exists for free tiers that produced nothing.
            # ddgs signals an empty sweep by raising, so throttled and genuinely-empty both land
            # here indistinguishably and both may pay. Spends only the ORIGINAL budget's remainder.
            budget = None if deadline is None else deadline - time.monotonic()
            if budget is None or budget > 0:
                fallback = serpapi_search(
                    effective_query,
                    max_results = max_results,
                    timeout = budget,
                    cancel_event = cancel_event,
                    website_policy = website_policy,
                )
                if fallback:
                    return fallback
            raise last_error
        if cancel_event is not None and cancel_event.is_set():
            return "Search cancelled."
        if not results:
            return EMPTY_SEARCH_RESULTS[0]
        parts = []
        for r in results:
            if len(parts) >= max_results:
                break
            href = str(r.get("href") or "").strip()
            allowed, _reason, _hostname = check_url_access(href, website_policy)
            if not allowed:
                continue
            title = clip(r.get("title"), title_max())
            snippet = clip(r.get("body"), snippet_max())
            parts.append(f"Title: {title}\nURL: {href}\nSnippet: {snippet}")
        if not parts:
            return EMPTY_SEARCH_RESULTS[1]
        text = "\n\n---\n\n".join(parts)
        text += (
            "\n\n---\n\nIMPORTANT: These are only short snippets. "
            "To get the full page content, call web_search with "
            'the url parameter (e.g. {"url": "<URL>"}).'
        )
        return text
    except Exception as e:
        return _search_failure_message(e, timeout)


def approved_engines() -> list[str]:
    """What this install can actually reach, in tier order. Local diagnostic, not upstream."""
    try:
        from ddgs.engines import ENGINES

        return [
            name
            for tier in _resolve_engine_tiers(ENGINES.get("text", {}))
            for name in tier.split(",")
        ]
    except Exception:
        return []
