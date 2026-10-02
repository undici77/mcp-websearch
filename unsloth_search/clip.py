# SPDX-License-Identifier: AGPL-3.0-only
"""Engine snippets arrive unbounded — one github.com result came back near 4k characters and
filled half the screen. Clamped here so every tier is held to the same limit."""

from __future__ import annotations

import os

SNIPPET_MAX = 400
TITLE_MAX = 200


def _limit(env: str, default: int) -> int:
    try:
        value = int(os.environ.get(env, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def snippet_max() -> int:
    return _limit("UNSLOTH_SNIPPET_MAX_CHARS", SNIPPET_MAX)


def title_max() -> int:
    return _limit("UNSLOTH_TITLE_MAX_CHARS", TITLE_MAX)


def clip(text: str, cap: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= cap:
        return text
    cut = text[:cap]
    if " " in cut[cap - 40 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.") + "…"
