# SPDX-License-Identifier: AGPL-3.0-only
"""The guard rails must keep refusing what upstream refuses."""

import pytest

from unsloth_search import web_search
from unsloth_search.web_access_policy import (
    check_url_access,
    hostname_allowed,
    normalize_domain,
    scope_search_query,
)


@pytest.mark.parametrize(
    "url,reason",
    [
        ("file:///etc/passwd", "only http/https"),
        ("javascript:alert(1)", "only http/https"),
        ("http://127.0.0.1/", "non-public"),
        ("http://169.254.169.254/latest/meta-data/", "non-public"),
        ("http://10.0.0.5/", "non-public"),
        ("http://192.168.1.1/", "non-public"),
        ("http://[::1]/", "non-public"),
        ("http://100.64.0.1/", "non-public"),
        ("https://user:pass@example.com/", "credentials"),
    ],
)
def test_web_search_refuses_dangerous_urls(url, reason):
    out = web_search("", url = url, timeout = 5)
    assert "Blocked" in out or "refusing" in out, f"{url} was not refused: {out}"
    assert reason in out, f"{url}: expected {reason!r} in {out!r}"


def test_empty_query_is_a_distinct_exit_from_a_blocked_url():
    # An empty url falls through to the query check, so "" must read as "no query", never "blocked".
    assert web_search("", url = "") == "No query provided."
    assert web_search("   ", url = "   ") == "No query provided."


def test_bare_host_is_upgraded_to_https_and_fetches():
    out = web_search("", url = "example.com", timeout = 30)
    assert "Blocked" not in out
    assert len(out) > 20


def test_policy_blocks_and_allows_subdomains():
    assert hostname_allowed("a.example.com", {"blockedDomains": ["example.com"]}) is False
    assert hostname_allowed("a.example.com", {"allowedDomains": ["example.com"]}) is True
    assert hostname_allowed("evil.com", {"allowedDomains": ["example.com"]}) is False


def test_cancel_event_short_circuits_before_any_engine():
    import threading

    ev = threading.Event()
    ev.set()
    assert web_search("anything", cancel_event = ev) == "Search cancelled."


@pytest.mark.parametrize("bad", ["", "a b.com", "a/b", "a@b.com", "EXAMPLE:8080", "0x7f.0.0.1", "....", "xn--", "a.-b.com"])
def test_normalize_domain_rejects_junk(bad):
    with pytest.raises(ValueError):
        normalize_domain(bad)


def test_canonical_ip_is_normalized_and_padded_ip_is_refused():
    assert normalize_domain("127.0.0.1") == "127.0.0.1"
    assert normalize_domain("[::1]") == "::1"
    # Zero-padded and hex octets are rebinding bypass attempts, not domains.
    for junk in ("127.000.000.001", "0x7f.0.0.1", "2130706433"):
        with pytest.raises(ValueError):
            normalize_domain(junk)


def test_scope_search_query_adds_site_filter_and_rotates():
    policy = {"allowedDomains": [f"d{i}.example.com" for i in range(20)]}
    scoped = scope_search_query("hello", policy)
    assert "site:" in scoped
    assert scoped.count("site:") <= 8, "the site: cap must hold"
    # A fixed head would make domains past the cap permanently undiscoverable.
    windows = {scope_search_query(q, policy).split("(")[-1] for q in ("a", "b", "c", "d", "e", "f")}
    assert len(windows) > 1, "rotation is not happening"


def test_no_policy_leaves_query_untouched():
    assert scope_search_query("plain query", None) == "plain query"


def test_aliases_are_resolved():
    from unsloth_search.upstream_verbatim import _resolve_web_search_args

    assert _resolve_web_search_args({"q": "hi"}) == ("hi", "")
    assert _resolve_web_search_args({"href": "https://x.com"}) == ("", "https://x.com")
    assert _resolve_web_search_args({"q": "a", "url": "https://x.com"}) == ("a", "https://x.com")


def test_canonicalize_drops_query_when_url_present():
    from unsloth_search import canonicalize_web_search_arguments

    assert canonicalize_web_search_arguments({"query": "a", "url": "https://x.com"}) == {"url": "https://x.com"}
    assert canonicalize_web_search_arguments({"q": "a"}) == {"query": "a"}
