# SPDX-License-Identifier: AGPL-3.0-only
"""The clamp is fork-owned, so its limits are asserted here — including that it never severs
a word and that a 4k engine snippet cannot reach the model intact."""

from unsloth_search.clip import clip, snippet_max, title_max


def test_short_text_passes_through():
    assert clip("  hello   world  ", 400) == "hello world"


def test_long_text_is_clamped_and_marked():
    out = clip("word " * 2000, 400)
    assert len(out) <= 401  # the ellipsis
    assert out.endswith("…")


def test_clamp_never_severs_a_word():
    out = clip("alpha bravo charlie " * 500, 400)
    assert not out[:-1].endswith(("alph", "brav", "charl"))
    assert out[:-1] == out[:-1].rstrip()


def test_env_override(monkeypatch):
    monkeypatch.setenv("UNSLOTH_SNIPPET_MAX_CHARS", "20")
    assert snippet_max() == 20
    assert len(clip("one two three four five six", snippet_max())) <= 21
    monkeypatch.setenv("UNSLOTH_SNIPPET_MAX_CHARS", "not-a-number")
    assert snippet_max() == 400


def test_zero_or_negative_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UNSLOTH_SNIPPET_MAX_CHARS", "0")
    assert snippet_max() == 400


def test_github_sized_snippet_cannot_pass_intact():
    blob = "LVGL is the most popular free and open source embedded graphics library. " * 60
    assert len(blob) > 4000
    assert len(clip(blob, snippet_max())) <= snippet_max() + 1


def test_title_uses_its_own_smaller_limit():
    assert title_max() < snippet_max()
