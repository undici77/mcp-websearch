# SPDX-License-Identifier: AGPL-3.0-only
"""Content-Encoding is undone while the capped read runs.

Servers send gzip/deflate unasked, even against urllib's default ``Accept-Encoding: identity``
(#13532). Undecoded, that body reaches the model as binary garbage; codings the decoder does not
undo stay refused.
"""

import email.message
import gzip
import zlib

import pytest

from unsloth_search import fetch

HTML = b"<html><body><h1>Compressed</h1><p>readable text</p></body></html>"


def _raw_deflate(data):
    c = zlib.compressobj(-1, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


class _Stream:
    """Dribbles 7 bytes a read, so the decoder's boundary and refill logic has to work."""

    def __init__(self, data, chunk = 7):
        self._d, self._n, self.i = data, chunk, 0

    def read1(self, n):
        out = self._d[self.i : self.i + min(n, self._n)]
        self.i += len(out)
        return out

    def read(self, n = -1):
        return self.read1(n if n and n > 0 else len(self._d) - self.i)

    def close(self):
        pass


class _FakeResp:
    def __init__(self, data, encoding = None, content_type = "text/html"):
        self.headers = email.message.Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        if encoding is not None:
            self.headers["Content-Encoding"] = encoding
        self._s = _Stream(data)

    def read1(self, n):
        return self._s.read1(n)

    def read(self, n = -1):
        return self._s.read(n)

    def close(self):
        pass


@pytest.fixture
def fake_wire(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fetch,
        "_resolve_with_budget",
        lambda host, port, deadline, cancel: (True, "", ["93.184.216.34"]),
    )
    monkeypatch.setattr(fetch, "_explicit_proxy_applies", lambda scheme, netloc: False)

    class _Opener:
        def open(self, req, timeout = None):
            seen["req"] = req
            return seen["resp"]

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *h: _Opener())
    return seen


def _fetch(monkeypatch, fake_wire, data, encoding, content_type = "text/html"):
    fake_wire["resp"] = _FakeResp(data, encoding = encoding, content_type = content_type)
    return fetch.fetch_url_raw("https://example.com/", timeout = 10)


@pytest.mark.parametrize(
    "encoding,wire",
    [
        ("gzip", gzip.compress(HTML)),
        ("x-gzip", gzip.compress(HTML)),
        ("deflate", zlib.compress(HTML)),
        ("deflate", _raw_deflate(HTML)),
        ("identity", HTML),
        (None, HTML),
    ],
    ids = ["gzip", "x-gzip", "deflate", "deflate-raw", "identity", "unlabeled"],
)
def test_a_coded_body_is_decoded_into_readable_text(monkeypatch, fake_wire, encoding, wire):
    err, text, ct = _fetch(monkeypatch, fake_wire, wire, encoding)

    assert err is None, err
    assert "readable text" in text, text[:200]
    assert ct == "text/html"


@pytest.mark.parametrize("encoding", ["br", "zstd", "compress"])
def test_a_coding_the_decoder_does_not_undo_is_refused(monkeypatch, fake_wire, encoding):
    err, text, _ct = _fetch(monkeypatch, fake_wire, gzip.compress(HTML), encoding)

    assert "unsupported Content-Encoding" in err, err
    assert text == ""


def test_the_request_offers_only_the_codings_it_can_undo(monkeypatch, fake_wire):
    _fetch(monkeypatch, fake_wire, HTML, None)

    assert fake_wire["req"].headers["Accept-encoding"] == "gzip, deflate"


def test_concatenated_gzip_members_are_one_body(monkeypatch, fake_wire):
    wire = gzip.compress(b"<html><body>first ") + gzip.compress(b"second</body></html>")

    err, text, _ct = _fetch(monkeypatch, fake_wire, wire, "gzip")

    assert err is None, err
    assert "first" in text and "second" in text, text[:200]


def test_an_empty_coded_body_is_empty_not_an_error(monkeypatch, fake_wire):
    err, text, _ct = _fetch(monkeypatch, fake_wire, b"", "gzip", content_type = None)

    assert err is None, err
    assert text == ""


def test_the_expansion_is_bounded_by_the_wire_budget():
    decoder = fetch._ContentDecoder("gzip")
    decoder.wire_limit = 8

    with pytest.raises(ValueError, match="byte limit"):
        decoder.read(lambda n: gzip.compress(HTML), 4096)
