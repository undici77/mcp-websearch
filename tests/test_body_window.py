# SPDX-License-Identifier: AGPL-3.0-only
"""Declared HTML gets a wider read window and a bounded body trim.

A flat _MAX_FETCH_BYTES read on a page that inlines its CSS/JS in <head> returns head, not
content: read wider, retain only _MAX_FETCH_BYTES past the end of <head>.
"""

import email.message

import pytest

from unsloth_search import fetch
from unsloth_search.fetch import fetch_page_text
from unsloth_search.upstream_verbatim import (
    _MAX_FETCH_BYTES,
    _MAX_HTML_FETCH_BYTES,
    _read_capped_body,
)

HEAD = b"<html><head>" + b"<!--" + b"y" * 4000 + b"-->" + b"</head><body>"
BODY = b"Z" * 100_000


class _Stream:
    """A response whose read1 dribbles, so the between-chunk locator gets a chance to act."""

    def __init__(self, data, chunk = 512):
        self._d, self._n, self.i = data, chunk, 0
        self.closed = False

    def read1(self, n):
        out = self._d[self.i : self.i + min(n, self._n)]
        self.i += len(out)
        return out

    def close(self):
        self.closed = True


def test_the_body_window_bounds_the_retained_bytes():
    stream = _Stream(HEAD + BODY)

    err, body = _read_capped_body(
        stream, _MAX_FETCH_BYTES, None, None, None, body_window = 200, charset = None
    )

    assert err is None
    assert b"<body>" in body, "the window stopped before the body began"
    assert b"ZZZ" in body
    # head + one chunk of slack + the 200-byte window, nowhere near the 512 KB flat cap
    assert len(body) < 5_000, f"the window did not clamp the read: {len(body)} bytes"


def test_without_a_window_the_full_cap_is_read():
    stream = _Stream(HEAD + BODY)

    err, body = _read_capped_body(stream, 40_000, None, None, None)

    assert err is None
    assert len(body) == 40_000


class _FakeResp:
    def __init__(self, content_type, data):
        self.headers = email.message.Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self._s = _Stream(data, chunk = 4096)

    def read1(self, n):
        return self._s.read1(n)

    def read(self, n = -1):
        return self._s.read1(n)

    def close(self):
        self._s.close()


@pytest.fixture
def fake_wire(monkeypatch):
    """Stub resolution and the opener so the test drives fetch_url_raw's own read decisions."""
    seen = {}

    monkeypatch.setattr(
        fetch, "_resolve_with_budget", lambda host, port, deadline, cancel: (True, "", ["93.184.216.34"])
    )
    monkeypatch.setattr(fetch, "_explicit_proxy_applies", lambda scheme, netloc: False)

    class _Opener:
        def open(self, req, timeout = None):
            return seen["resp"]

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *h: _Opener())
    return seen


def _drive(monkeypatch, fake_wire, content_type, data):
    fake_wire["resp"] = _FakeResp(content_type, data)
    recorded = {}

    def spy(resp, max_bytes, timeout, deadline, cancel_event, **kw):
        recorded["max_bytes"] = max_bytes
        recorded.update(kw)
        return None, data

    monkeypatch.setattr(fetch, "_read_capped_body", spy)
    err, text, ct = fetch.fetch_url_raw("https://example.com/", timeout = 10)
    assert err is None, err
    return recorded


def test_declared_html_gets_the_wider_read_and_a_body_window(monkeypatch, fake_wire):
    rec = _drive(monkeypatch, fake_wire, "text/html", HEAD + BODY)
    assert rec["max_bytes"] == _MAX_HTML_FETCH_BYTES
    assert rec["body_window"] == _MAX_FETCH_BYTES


@pytest.mark.parametrize("content_type", ["text/plain", "application/json", None])
def test_a_non_html_body_keeps_the_flat_cap_and_no_window(monkeypatch, fake_wire, content_type):
    rec = _drive(monkeypatch, fake_wire, content_type, b"plain body bytes")
    assert rec["max_bytes"] == _MAX_FETCH_BYTES
    assert rec["body_window"] is None
    assert rec["charset"] is None


def _link_page(n):
    links = "".join(f'<li><a href="https://ex.com/p{i}">Article {i}</a></li>' for i in range(n))
    return "<html><body><ul>" + links + "</ul></body></html>"


@pytest.mark.parametrize(
    "n,max_chars,urls_expected",
    [(4, 4000, True), (120, 600, False)],
)
def test_same_site_link_urls_survive_only_while_the_page_fits(
    monkeypatch, n, max_chars, urls_expected
):
    body = _link_page(n)
    monkeypatch.setattr(
        fetch, "fetch_url_raw", lambda url, **kw: (None, body, "text/html; charset=utf-8")
    )

    out = fetch_page_text("https://ex.com/", max_chars = max_chars, timeout = 5)

    assert "Article 1" in out, f"the link text itself was dropped: {out[:200]!r}"
    assert ("ex.com/p" in out) is urls_expected, out[:400]
