# SPDX-License-Identifier: AGPL-3.0-only
# Mirrors upstream studio/backend/tests/test_web_search_tiers.py
"""Text search runs the approved allowlist tier by tier and never reaches Yandex.

Drives the real DDGS engine selector; only each engine's own ``search`` is replaced, so the tier
strings really are resolved by ddgs and a fan-out cannot hide.
"""

import json
import time
import urllib.request

import pytest

from ddgs.engines import ENGINES
from ddgs.exceptions import DDGSException
from ddgs.results import TextResult

from unsloth_search import serpapi, web_search
from unsloth_search.upstream_verbatim import EMPTY_SEARCH_RESULTS, _resolve_engine_tiers

TIER1 = ("wikipedia", "brave", "duckduckgo", "mojeek", "startpage")
TIER2 = ("grokipedia", "google", "yahoo")


class _Recorder:
    """Records every text engine ddgs decides to run, and installs their behaviour."""

    def __init__(self, monkeypatch):
        self.m = monkeypatch
        self.calls = []

    def _search_for(self, name, behaviour):
        def search(engine, query, **kwargs):
            self.calls.append((name, query))
            if behaviour == "empty":
                return []
            if behaviour == "raise":
                raise DDGSException(f"{name} refused")
            return [TextResult(title = f"{name} result", href = f"https://{name}.example/source", body = f"Synthetic from {name}.")]

        return search

    def install(self, behaviour_for):
        for name, cls in ENGINES["text"].items():
            # Building a real engine reaches ddgs's HTTP client, which on the py<3.10 pin raises
            # `Invalid impersonate`, so these selection tests would fail there for an unrelated reason.
            self.m.setattr(cls, "__init__", lambda self, *a, **k: None, raising = False)
            self.m.setattr(cls, "search", self._search_for(name, behaviour_for(name)))

    @property
    def names(self):
        return [n for n, _q in self.calls]


@pytest.fixture
def engine_calls(monkeypatch):
    from ddgs.ddgs import DDGS

    # A DHT cache hit would skip the engines entirely.
    if callable(getattr(DDGS, "_get_network_client", None)):
        monkeypatch.setattr(DDGS, "_get_network_client", lambda self: None)
    return _Recorder(monkeypatch)


@pytest.fixture
def no_paid_key(monkeypatch):
    """Tier 3 spends money when a key exists, so a test claiming 'no results' must clear both key sources."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising = False)
    monkeypatch.setattr(serpapi, "KEY_FILE", "/nonexistent/keys.json")


def _require_two_tiers():
    resolved = _resolve_engine_tiers(ENGINES["text"])
    if len(resolved) < 2:
        pytest.skip(f"installed ddgs resolves only {len(resolved)} tier(s): {resolved}")


def test_tier_one_answers_alone_and_tier_two_is_never_asked(engine_calls):
    engine_calls.install(lambda n: "results")
    result = web_search("unsloth")
    assert set(engine_calls.names) <= set(TIER1)
    assert not set(engine_calls.names) & set(TIER2)
    assert "yandex" not in engine_calls.names
    assert "URL: https://" in result


def test_tier_two_runs_only_after_tier_one_finds_nothing(engine_calls):
    _require_two_tiers()
    engine_calls.install(lambda n: "empty" if n in TIER1 else "results")
    result = web_search("unsloth")
    assert set(engine_calls.names) & set(TIER2), "tier 2 was never reached"
    assert "yandex" not in engine_calls.names
    assert "URL: https://" in result


def test_a_tier_one_refusal_falls_through_to_tier_two(engine_calls):
    _require_two_tiers()
    engine_calls.install(lambda n: "raise" if n in TIER1 else "results")

    result = web_search("unsloth")

    assert set(engine_calls.names) & set(TIER2), "a tier-1 refusal did not reach tier 2"
    assert "URL: https://" in result


def test_every_tier_empty_reports_no_results_and_never_widens(engine_calls, no_paid_key):
    engine_calls.install(lambda n: "empty")
    assert web_search("unsloth") == EMPTY_SEARCH_RESULTS[0]
    assert "yandex" not in engine_calls.names


def test_tier_three_pays_and_answers_only_when_a_key_exists(monkeypatch, engine_calls, tmp_path):
    """ddgs cannot tell throttled from empty, so an empty free sweep reaches tier 3; the host stays serpapi.com."""
    key_file = tmp_path / "keys.json"
    key_file.write_text(json.dumps({"serpapi": "test-key"}))
    monkeypatch.delenv("SERPAPI_API_KEY", raising = False)
    monkeypatch.setattr(serpapi, "KEY_FILE", str(key_file))

    hosts = []
    payload = {"organic_results": [{"title": "Paid hit", "link": "https://example.org/paid", "snippet": "From the paid tier."}]}

    class _Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, *a, **k):
        # full_url minus the query: the api_key must never reach an assertion message.
        hosts.append(request.full_url.split("?")[0])
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    engine_calls.install(lambda n: "empty")

    result = web_search("unsloth")

    assert hosts == ["https://serpapi.com/search"], f"unexpected paid egress: {hosts}"
    assert "Paid hit" in result and "https://example.org/paid" in result
    assert "yandex" not in engine_calls.names


@pytest.mark.parametrize("missing", [("startpage",), ("startpage", "grokipedia", "google")])
def test_an_engine_absent_from_the_registry_does_not_reopen_auto(monkeypatch, engine_calls, missing):
    """9.8.0 raises KeyError on an unknown name and re-runs as auto; 9.14.4 keeps the valid ones."""
    engine_calls.install(lambda n: "results")
    for name in missing:
        monkeypatch.delitem(ENGINES["text"], name, raising = False)

    expected = set(_resolve_engine_tiers(ENGINES["text"])[0].split(","))
    web_search("unsloth")

    contacted = set(engine_calls.names)
    assert "yandex" not in contacted
    # Equality, not a subset: as a subset this passed intermittently on the pre-allowlist code,
    # whose shuffle sometimes kept the dispatched engines inside the tiers anyway.
    assert contacted == expected, f"expected exactly tier 1 {expected}, got {contacted}"


def test_no_approved_engine_available_fails_closed(monkeypatch, engine_calls):
    engine_calls.install(lambda n: "results")
    for name in TIER1 + TIER2:
        monkeypatch.delitem(ENGINES["text"], name, raising = False)

    assert web_search("unsloth") == "Search failed: no approved search engine is available."
    assert engine_calls.names == [], "a query left the process with no approved engine"


def test_disabled_engines_are_dropped_from_a_tier(monkeypatch, engine_calls):
    engine_calls.install(lambda n: "results")
    monkeypatch.setattr(ENGINES["text"]["duckduckgo"], "disabled", True)

    web_search("unsloth")

    contacted = set(engine_calls.names)
    assert "duckduckgo" not in contacted
    assert contacted <= set(TIER1) - {"duckduckgo"}, f"an engine outside tier 1 ran: {contacted}"
    assert "yandex" not in contacted


def test_the_caller_timeout_is_one_budget_for_both_tiers(monkeypatch, engine_calls):
    """Tier 2 inherits what is LEFT of the timeout: a fresh copy doubles what the caller asked for."""
    from ddgs.ddgs import DDGS

    budgets = []
    real_init = DDGS.__init__

    def recording_init(self, *a, **k):
        budgets.append(k.get("timeout"))
        return real_init(self, *a, **k)

    monkeypatch.setattr(DDGS, "__init__", recording_init)
    engine_calls.install(lambda n: "empty" if n in TIER1 else "results")
    for name in TIER1:
        cls = ENGINES["text"].get(name)
        if cls is not None:
            inner = cls.search
            monkeypatch.setattr(cls, "search", lambda self, q, _i = inner, **k: (time.sleep(0.3), _i(self, q, **k))[1])

    web_search("unsloth", timeout = 3)

    tier_budgets = [b for b in budgets if isinstance(b, (int, float))]
    assert len(tier_budgets) >= 3, f"expected a client per tier, saw {budgets}"
    assert tier_budgets == sorted(tier_budgets, reverse = True), f"budget did not shrink: {tier_budgets}"
    assert max(tier_budgets) <= tier_budgets[0], "a tier was handed more than the original budget"


def test_the_resolver_never_names_an_engine_outside_the_tiers():
    named = {n for t in _resolve_engine_tiers(ENGINES["text"]) for n in t.split(",")}
    assert named <= set(TIER1) | set(TIER2)
    assert "yandex" not in named
    assert "bing" not in named
