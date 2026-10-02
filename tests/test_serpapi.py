# SPDX-License-Identifier: AGPL-3.0-only
"""Tier 3 is fork-owned, so its contract is asserted here: silent unarmed, tier format armed,
and never spent on an honest empty sweep."""

import json
import sys
import types
import urllib.error

import pytest

from unsloth_search import search, serpapi
from unsloth_search.serpapi import serpapi_api_key, serpapi_search


@pytest.fixture
def unarmed(monkeypatch, tmp_path):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setattr(serpapi, "KEY_FILE", str(tmp_path / "missing.json"))


def test_unarmed_is_silent(unarmed):
    assert serpapi_api_key() == ""
    assert serpapi_search("anything") is None


def test_env_key_beats_file(monkeypatch, unarmed, tmp_path):
    key_file = tmp_path / "keys.json"
    key_file.write_text(json.dumps({"serpapi": "file-key"}))
    monkeypatch.setattr(serpapi, "KEY_FILE", str(key_file))
    monkeypatch.setenv("SERPAPI_API_KEY", "env-key")
    assert serpapi_api_key() == "env-key"
    monkeypatch.delenv("SERPAPI_API_KEY")
    assert serpapi_api_key() == "file-key"


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_results_use_the_tier_format_and_the_policy_filter(monkeypatch, unarmed):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")

    def fake_urlopen(request, timeout=None):
        return _Response(
            {
                "organic_results": [
                    {"title": "A  B", "link": "https://example.org/x", "snippet": "s1"},
                    {"title": "Nope", "link": "https://blocked.example/y", "snippet": "s2"},
                ]
            }
        )

    monkeypatch.setattr(serpapi.urllib.request, "urlopen", fake_urlopen)
    out = serpapi_search(
        "q", max_results = 5, website_policy = {"blockedDomains": ["blocked.example"]}
    )
    assert out.startswith("Title: A B\nURL: https://example.org/x\nSnippet: s1")
    assert "blocked.example" not in out
    assert "call web_search with" in out


def test_transport_failure_is_none_not_a_crash(monkeypatch, unarmed):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")

    def boom(*args, **kwargs):
        raise urllib.error.URLError("throttled")

    monkeypatch.setattr(serpapi.urllib.request, "urlopen", boom)
    assert serpapi_search("q") is None


def _fake_ddgs(monkeypatch, text_impl):
    class FakeDDGS:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def text(self, query, max_results=None, backend=None):
            return text_impl(query, backend)

    ddgs = types.ModuleType("ddgs")
    ddgs.DDGS = FakeDDGS
    engines = types.ModuleType("ddgs.engines")
    engines.ENGINES = {"text": {"google": object(), "brave": object()}}
    monkeypatch.setitem(sys.modules, "ddgs", ddgs)
    monkeypatch.setitem(sys.modules, "ddgs.engines", engines)


def test_throttled_sweep_falls_back_to_serpapi(monkeypatch, unarmed):
    def raise_throttled(query, backend):
        raise RuntimeError("429 Too Many Requests")

    _fake_ddgs(monkeypatch, raise_throttled)
    calls = []
    monkeypatch.setattr(
        search, "serpapi_search", lambda *a, **k: calls.append(k) or "FALLBACK TEXT"
    )
    assert search.web_search("q", timeout = 30) == "FALLBACK TEXT"
    assert calls and "timeout" in calls[0]


def test_honest_empty_sweep_never_spends_serpapi(monkeypatch, unarmed):
    _fake_ddgs(monkeypatch, lambda query, backend: [])
    called = []
    monkeypatch.setattr(search, "serpapi_search", lambda *a, **k: called.append(1))
    out = search.web_search("q", timeout = 30)
    assert out == search.EMPTY_SEARCH_RESULTS[0]
    assert called == []


def test_fallback_absent_re_raises_for_upstream_copy(monkeypatch, unarmed):
    def raise_throttled(query, backend):
        raise RuntimeError("429 Too Many Requests")

    _fake_ddgs(monkeypatch, raise_throttled)
    monkeypatch.setattr(search, "serpapi_search", lambda *a, **k: None)
    out = search.web_search("q", timeout = 30)
    assert out.startswith("Search failed:")
