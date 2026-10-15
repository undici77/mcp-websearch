# SPDX-License-Identifier: AGPL-3.0-only
"""A GitHub blob link reads the raw file, not the blob page's chrome (mirrors upstream #13754).

github.com/{owner}/{repo}/blob/{ref}/{path} renders the file inside GitHub's UI; the same commit's
source lives on raw.githubusercontent.com. Same URL the model named, same fetch gates, no schema
field — so this is a better read, not a new capability. Only a blob link with a real path reroutes:
repo roots keep the README API, and everything else stays on its own page.
"""

import pytest

from unsloth_search import fetch
from unsloth_search.fetch import fetch_page_text

GITHUB_PAGE = "<html><body><div>Unsloth Studio</div></body></html>"
SOURCE = "<!doctype html>\n<html>\n  <body><script>run()</script></body>\n</html>\n"


def _fake(monkeypatch, raw_body=None, raw_err=None):
    """Record every URL the fetcher asks for, answering the raw host with the file."""
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        if url.startswith("https://raw.githubusercontent.com/"):
            if raw_err is not None:
                return raw_err, "", ""
            return None, raw_body, "text/plain"
        return None, GITHUB_PAGE, "text/html"

    monkeypatch.setattr(fetch, "fetch_url_raw", fake)
    return calls


def test_a_blob_link_reads_the_raw_host_and_never_the_blob_page(monkeypatch):
    calls = _fake(monkeypatch, raw_body=SOURCE)

    out = fetch_page_text(
        "https://github.com/unslothai/unsloth/blob/main/studio/frontend/index.html?plain=1#L3",
        timeout=5,
    )

    # query and fragment are GitHub's viewer state, not part of the raw path.
    assert [c[0] for c in calls] == [
        "https://raw.githubusercontent.com/unslothai/unsloth/main/studio/frontend/index.html"
    ]
    assert out == SOURCE.strip()


def test_the_raw_route_reuses_the_deadline_so_the_budget_is_not_doubled(monkeypatch):
    calls = _fake(monkeypatch, raw_body=SOURCE)

    fetch_page_text("https://github.com/o/r/blob/main/a.py", timeout=5)

    deadline = calls[0][1]["deadline"]
    assert deadline is not None, "the raw fetch started without the shared wall-clock budget"


def test_a_refused_raw_route_falls_back_to_the_blob_page(monkeypatch):
    calls = _fake(monkeypatch, raw_err="Failed to fetch URL: HTTP 404 Not Found")

    out = fetch_page_text("https://github.com/owner/repo/blob/v1.2/src/app.py", timeout=5)

    assert [c[0] for c in calls] == [
        "https://raw.githubusercontent.com/owner/repo/v1.2/src/app.py",
        "https://github.com/owner/repo/blob/v1.2/src/app.py",
    ]
    assert "Unsloth Studio" in out, out[:200]


def test_a_semicolon_in_a_file_name_survives_the_raw_url(monkeypatch):
    calls = _fake(monkeypatch, raw_body="file body")

    fetch_page_text("https://github.com/owner/repo/blob/main/t/foo;bar.py", timeout=5)

    assert [c[0] for c in calls] == ["https://raw.githubusercontent.com/owner/repo/main/t/foo;bar.py"]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/unslothai/unsloth",
        "https://github.com/unslothai/unsloth/tree/main/studio",
        "https://github.com/unslothai/unsloth/blob/main",
        "https://github.com/unslothai/unsloth/issues/123",
        "https://example.com/owner/repo/blob/main/app.py",
    ],
)
def test_other_urls_never_reach_the_raw_host(monkeypatch, url):
    calls = _fake(monkeypatch, raw_err="Failed to fetch URL: HTTP 403 Forbidden")

    fetch_page_text(url, timeout=5)

    assert not any("raw.githubusercontent.com" in c[0] for c in calls), [c[0] for c in calls]
    # A repo root legitimately stops at the README API; anything else ends on its own page.
    assert calls[-1][0] in (url, "https://api.github.com/repos/unslothai/unsloth/readme"), calls[-1][0]
