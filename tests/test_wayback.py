# SPDX-License-Identifier: AGPL-3.0-only
"""The wayback tool is fork-owned, so its contract is asserted here: the caller's policy judges the
original url, each archive request is pinned to its own domain, and Arquivo.pt runs only after the
Wayback Machine came up empty. Offline: both fetch entry points are replaced."""

import json

import pytest

import server
from unsloth_search import wayback
from unsloth_search.wayback import ARCHIVES, wayback_fetch, wayback_snapshots


class Net:
    """Records every archive request; answers from a per-prefix table."""

    def __init__(self, raw = None, page = None):
        self.calls = []
        self.raw = raw or {}
        self.page = page or {}

    def _answer(self, table, url, default):
        for prefix, answer in table.items():
            if url.startswith(prefix):
                return answer
        return default

    def fetch_url_raw(self, url, timeout = 30, deadline = None, website_policy = None, **_kw):
        self.calls.append(("raw", url, website_policy))
        return self._answer(self.raw, url, ("Failed to fetch URL: HTTP 503 Service Unavailable", "", ""))

    def fetch_page_text(self, url, max_chars = None, timeout = 30, website_policy = None, **_kw):
        self.calls.append(("page", url, website_policy))
        return self._answer(self.page, url, "Failed to fetch URL: HTTP 404 Not Found")


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(wayback, "fetch_url_raw", n.fetch_url_raw)
    monkeypatch.setattr(wayback, "fetch_page_text", n.fetch_page_text)
    return n


WB_REPLAY = ARCHIVES["wayback"][3]
AQ_CDX = ARCHIVES["arquivo"][2]
AQ_REPLAY = ARCHIVES["arquivo"][3]
WB_CDX = ARCHIVES["wayback"][2]


def _ndjson(*rows):
    return "\n".join(json.dumps(r) for r in rows)


@pytest.mark.parametrize(
    "url, policy",
    [
        ("https://python.org/", {"blockedDomains": ["python.org"]}),
        ("https://python.org/", {"allowedDomains": ["example.org"]}),
        ("http://127.0.0.1/", None),
        ("http://[::1]/", None),
        ("http://10.0.0.5/admin", None),
        ("http://localhost:8080/", None),
        ("file:///etc/passwd", None),
    ],
)
def test_refused_original_url_never_reaches_an_archive(net, url, policy):
    assert wayback_fetch(url, website_policy = policy).startswith("Blocked:")
    assert wayback_snapshots(url, website_policy = policy).startswith("Blocked:")
    assert net.calls == []


def test_allowed_domains_judge_the_original_host_not_the_archive(net):
    net.page[WB_REPLAY] = "page body"
    out = wayback_fetch("https://python.org/", website_policy = {"allowedDomains": ["python.org"]})
    assert out.startswith("Archived copy of https://python.org/ from the Wayback Machine")
    assert out.endswith("page body")


def test_every_request_is_pinned_to_its_own_archive(net):
    net.raw[AQ_CDX] = (None, _ndjson({"timestamp": "20150101000000", "url": "https://python.org/"}), "")
    net.page[AQ_REPLAY] = "aq body"
    wayback_fetch("https://python.org/")
    assert net.calls, "expected archive traffic"
    for _kind, url, policy in net.calls:
        name = "wayback" if "archive.org" in url else "arquivo"
        assert policy == {"allowedDomains": [ARCHIVES[name][1]]}, url


def test_wayback_replay_url_and_latest_default(net):
    net.page[WB_REPLAY] = "body"
    wayback_fetch("python.org/x")
    assert net.calls[0][1] == f"{WB_REPLAY}{wayback.LATEST}id_/https://python.org/x"
    net.calls.clear()
    out = wayback_fetch("https://python.org/x", timestamp = "2015-01-01")
    assert net.calls[0][1] == f"{WB_REPLAY}20150101id_/https://python.org/x"
    assert "closest to 2015-01-01" in out


def test_arquivo_only_after_wayback_misses(net):
    net.page[WB_REPLAY] = "wb body"
    wayback_fetch("https://python.org/")
    assert all("arquivo.pt" not in url for _k, url, _p in net.calls)


def test_arquivo_fetches_the_exact_capture_it_found(net):
    net.raw[AQ_CDX] = (None, _ndjson({"timestamp": "20080222211956", "url": "http://www.python.org/"}), "")
    net.page[AQ_REPLAY] = "aq body"
    out = wayback_fetch("https://python.org/", timestamp = "2008")
    cdx = [u for k, u, _p in net.calls if k == "raw"][0]
    assert "closest=20080000000000" in cdx and "filter=status%3A200" in cdx
    assert ("page", f"{AQ_REPLAY}20080222211956id_/http://www.python.org/", {"allowedDomains": ["arquivo.pt"]}) in net.calls
    assert out.startswith("Archived copy of https://python.org/ from Arquivo.pt, captured 2008-02-22 21:19 UTC")


def test_no_copy_anywhere(net):
    net.raw[AQ_CDX] = (None, "", "text/x-ndjson")
    assert wayback_fetch("https://gone.example/") == (
        "No archived copy of https://gone.example/ in Wayback Machine or Arquivo.pt."
    )


def test_archive_failures_are_reported_not_mistaken_for_no_copy(net):
    net.page[WB_REPLAY] = "Failed to fetch URL: HTTP 503 Service Unavailable"
    out = wayback_fetch("https://python.org/")
    assert out.startswith("No archived copy of https://python.org/ could be read")
    assert "Wayback Machine: Failed to fetch URL: HTTP 503" in out
    assert "Arquivo.pt: Failed to fetch URL: HTTP 503" in out


def test_pinned_archive_is_the_only_one_contacted(net):
    net.raw[AQ_CDX] = (None, "", "")
    wayback_fetch("https://python.org/", archive = "arquivo")
    assert all("archive.org" not in url for _k, url, _p in net.calls)
    assert wayback_fetch("https://python.org/", archive = "nope").startswith("Unknown archive")


@pytest.mark.parametrize("bad", ["bad", "15", "2015-13-01x", "2015010112000000"])
def test_malformed_timestamps_are_refused_offline(net, bad):
    assert wayback_fetch("https://python.org/", timestamp = bad).startswith("Invalid timestamp")
    assert net.calls == []


def test_snapshots_merge_newest_first_and_clamp(net):
    net.raw[WB_CDX] = (
        None,
        json.dumps(
            [
                ["timestamp", "original", "statuscode", "mimetype"],
                ["20200101000000", "https://python.org/", "200", "text/html"],
            ]
        ),
        "application/json",
    )
    net.raw[AQ_CDX] = (None, _ndjson({"timestamp": "20210101000000", "url": "https://python.org/", "status": "200", "mime": "text/html"}), "")
    out = wayback_snapshots("https://python.org/", limit = 999)
    lines = [ln for ln in out.splitlines() if "ts=" in ln]
    assert [ln.split()[2] for ln in lines] == ["arquivo", "wayback"]
    wb = [u for k, u, _p in net.calls if WB_CDX in u][0]
    assert f"limit=-{wayback.MAX_SNAPSHOTS}" in wb and "filter=statuscode%3A200" in wb


def test_outage_page_is_unavailable_not_empty(net):
    net.raw[WB_CDX] = (None, "<html>Temporarily Offline</html>", "text/html")
    net.raw[AQ_CDX] = (None, "", "")
    out = wayback_snapshots("https://python.org/")
    assert out.startswith("No captures of https://python.org/")
    assert "Wayback Machine: unavailable" in out


# --- server wiring ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result, hinted",
    [
        ("Failed to fetch URL: HTTP 404 Not Found", True),
        ("Failed to fetch URL: HTTP 503 Service Unavailable", True),
        ("Failed to resolve host: no addresses for 'gone.example'", True),
        ("Failed to fetch URL: timed out.", True),
        ("Failed to fetch URL: cancelled.", False),
        ("Blocked: refusing to fetch non-public address 127.0.0.1.", False),
        ("Blocked: website access policy disallows python.org.", False),
        ("(binary content, 12 bytes; not readable as text)", False),
        ("# A real page", False),
    ],
)
def test_url_mode_hint_only_on_dead_pages(monkeypatch, result, hinted):
    monkeypatch.setattr(server, "web_search", lambda *a, **k: result)
    out = server.web_search_tool(url = 'https://x.example/"q"')
    assert ("call wayback" in out) is hinted
    if hinted:
        assert json.loads(out.rsplit("call wayback with ", 1)[1].rstrip(".")) == {"url": 'https://x.example/"q"'}


def test_search_mode_is_never_hinted(monkeypatch):
    monkeypatch.setattr(server, "web_search", lambda *a, **k: "Failed to fetch URL: HTTP 404")
    assert "wayback" not in server.web_search_tool(query = "anything")
