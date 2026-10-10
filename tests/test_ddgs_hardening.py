# SPDX-License-Identifier: AGPL-3.0-only
"""ddgs hardening stays idempotent, and a blocked sweep is not a failure.

Both layers patch ddgs's own registry/client classes in place: they must wrap exactly once and
rename no engine — a new registry key is an engine this allowlist does not approve.
"""

import json
import threading
import urllib.request

import pytest
import httpx
from ddgs.engines import ENGINES

from unsloth_search import serpapi, web_search
from unsloth_search.upstream_verbatim import (
    EMPTY_SEARCH_RESULTS,
    _install_ddgs_http1_retry,
    _install_yahoo_layout_parser,
    _is_connection_reset,
    _resolve_engine_tiers,
    _usable_search_results,
)

TIER1 = ("wikipedia", "brave", "duckduckgo", "mojeek", "startpage")
TIER2 = ("grokipedia", "google", "yahoo")


def test_the_yahoo_parser_is_installed_once_and_renames_nothing():
    engines = dict(ENGINES["text"])
    before = _resolve_engine_tiers(engines)

    _install_yahoo_layout_parser(engines)
    installed = engines["yahoo"]
    _install_yahoo_layout_parser(engines)

    assert engines["yahoo"] is installed, "a second install re-wrapped the parser"
    assert installed._parses_section_layout is True
    assert _resolve_engine_tiers(engines) == before, "the allowlist gained or lost an engine name"


def test_the_http1_retry_wraps_each_ddgs_client_exactly_once():
    from ddgs.http_client import HttpClient

    _install_ddgs_http1_retry()
    wrapped = HttpClient.__dict__["request"]

    _install_ddgs_http1_retry()

    assert HttpClient.__dict__["request"] is wrapped, "a second install double-wrapped request()"
    assert HttpClient.__dict__["_unsloth_http1_retry"] is True
    assert getattr(wrapped, "__wrapped__", None) is not None


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError("[Errno 104] Connection reset by peer"),
        RuntimeError("h2 connection driver error"),
        httpx.RemoteProtocolError("server disconnected"),
        BrokenPipeError("broken pipe"),
        OSError("[Errno 10054] forcibly closed"),
    ],
)
def test_connection_resets_match_the_reset_markers(exc):
    assert _is_connection_reset(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        # The class name alone must not match: it renders without the space the marker needs.
        ConnectionResetError("reset by peer"),
        # DNS failures and refused connections are not resets, so they must not replay.
        OSError("Name or service not known"),
        ConnectionRefusedError("[Errno 111] Connection refused"),
    ],
)
def test_a_non_reset_never_triggers_the_http1_replay(exc):
    assert _is_connection_reset(exc) is False


def test_usable_search_results_drops_blocked_and_malformed_hits():
    results = [
        {"href": "https://blocked.example/x"},
        {"href": "https://ok.example/x"},
        "not-a-dict",
        {"no_href": True},
    ]
    kept = _usable_search_results(results, {"blockedDomains": ["blocked.example"]})
    assert [r["href"] for r in kept] == ["https://ok.example/x"]


class _Resp:
    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _paid_tier_armed(monkeypatch, tmp_path):
    """A key is present, so any paid egress this turn is a real regression, not a skip."""
    key_file = tmp_path / "keys.json"
    key_file.write_text(json.dumps({"serpapi": "test-key"}))
    monkeypatch.delenv("SERPAPI_API_KEY", raising = False)
    monkeypatch.setattr(serpapi, "KEY_FILE", str(key_file))
    hosts = []
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, *a, **k: hosts.append(req.full_url.split("?")[0]) or _Resp(),
    )
    return hosts


def _stub_sweep(monkeypatch, candidates_for):
    import ddgs.ddgs

    monkeypatch.setattr(ddgs.ddgs.DDGS, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(
        ddgs.ddgs.DDGS, "text", lambda self, q, **k: candidates_for(k.get("backend", ""))
    )
    if callable(getattr(ddgs.ddgs.DDGS, "_get_network_client", None)):
        monkeypatch.setattr(ddgs.ddgs.DDGS, "_get_network_client", lambda self: None)


def test_policy_blocked_hits_report_blocked_and_never_pay(monkeypatch, tmp_path):
    """Candidates the policy dropped are an honest empty sweep, not the tier's exception.

    Re-raising here reports "nothing you may read" as "Search failed: ..." and, in this fork,
    spends SerpApi credits re-finding hits the same policy blocks again.
    """
    hosts = _paid_tier_armed(monkeypatch, tmp_path)
    _stub_sweep(
        monkeypatch,
        lambda backend: [{"href": f"https://{backend}.example/x", "title": "T", "body": "B"}],
    )

    out = web_search("unsloth", website_policy = {"blockedDomains": ["example"]}, timeout = 5)

    assert out == EMPTY_SEARCH_RESULTS[1], out
    assert hosts == [], f"paid egress for a policy-blocked sweep: {hosts}"


def test_an_engine_that_never_ran_does_not_open_the_paid_tier(monkeypatch, tmp_path):
    """No approved engine resolves: the install is broken, not throttled, so tier 3 must stay shut."""
    hosts = _paid_tier_armed(monkeypatch, tmp_path)
    _stub_sweep(monkeypatch, lambda backend: [])
    for name in TIER1 + TIER2:
        monkeypatch.delitem(ENGINES["text"], name, raising = False)

    out = web_search("unsloth", timeout = 5)

    assert out == "Search failed: no approved search engine is available.", out
    assert hosts == [], f"paid egress with no engine installed: {hosts}"


def test_a_cancelled_search_never_reaches_the_paid_tier(monkeypatch, tmp_path):
    hosts = _paid_tier_armed(monkeypatch, tmp_path)
    ev = threading.Event()
    ev.set()

    assert web_search("unsloth", cancel_event = ev) == "Search cancelled."
    assert hosts == []
