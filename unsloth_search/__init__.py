# SPDX-License-Identifier: AGPL-3.0-only
"""Web search ported from Unsloth Studio's server-side tool loop. No API key.

upstream_verbatim.py is generated from tools.py and must not be hand-edited; fetch.py and search.py
are the adapted layer over it.
"""

from .search import (
    WEB_SEARCH_TOOL,
    approved_engines,
    canonicalize_web_search_arguments,
    web_search,
)
from .upstream_verbatim import EMPTY_SEARCH_RESULTS, _resolve_engine_tiers, _search_failure_message

__all__ = [
    "WEB_SEARCH_TOOL",
    "EMPTY_SEARCH_RESULTS",
    "approved_engines",
    "canonicalize_web_search_arguments",
    "web_search",
]

resolve_engine_tiers = _resolve_engine_tiers
search_failure_message = _search_failure_message
