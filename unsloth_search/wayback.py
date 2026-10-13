# SPDX-License-Identifier: AGPL-3.0-only
# Fork-owned web archives — NOT from upstream. Reads an archived copy of a page that is gone or changed.

"""Wayback Machine, then Arquivo.pt: archived copies through upstream's fetcher, unchanged.

Every request goes through `fetch_url_raw` / `fetch_page_text`, so the SSRF refusal, DNS pinning,
redirect re-checks, body cap and html->md conversion are upstream's. Two local rules sit on top:

  * the caller's website policy judges the ORIGINAL url, never the archive host;
  * each archive request carries a policy admitting only that archive's domain, so no redirect can
    leave the archive that served it.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.parse

from .fetch import fetch_page_text, fetch_url_raw, page_char_budget
from .upstream_verbatim import _normalize_url_scheme
from .web_access_policy import check_url_access

# name -> (label, policy domain, CDX endpoint, replay prefix). The egress gate derives its hosts from here.
ARCHIVES = {
    "wayback": (
        "Wayback Machine",
        "archive.org",
        "https://web.archive.org/cdx/search/cdx",
        "https://web.archive.org/web/",
    ),
    "arquivo": (
        "Arquivo.pt",
        "arquivo.pt",
        "https://arquivo.pt/wayback/cdx",
        "https://arquivo.pt/wayback/",
    ),
}
ORDER = ("wayback", "arquivo")
# A far-future timestamp: replay redirects to the capture closest to it, i.e. the latest.
LATEST = "99991231235959"
MAX_SNAPSHOTS = 50
_TS_RE = re.compile(r"^\d{4,14}$")


def archive_hosts() -> set[str]:
    return {domain for _label, domain, _cdx, _replay in ARCHIVES.values()}


def _policy(name: str) -> dict:
    return {"allowedDomains": [ARCHIVES[name][1]]}


def _when(ts: str) -> str:
    ts = (ts or "").ljust(14, "0")
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"


def _clean_ts(value: str) -> str | None:
    """'' for latest, digits for a date, None when malformed. Accepts 2015, 2015-01-01, 20150101120000."""
    ts = re.sub(r"[-:T ]", "", (value or "").strip())
    if not ts:
        return ""
    return ts if _TS_RE.match(ts) else None


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _gate(url: str, website_policy: dict | None) -> tuple[str | None, str]:
    url = _normalize_url_scheme((url or "").strip())
    allowed, reason, host = check_url_access(url, website_policy)
    if not allowed:
        return reason, url
    # Bytes come from the archive, never this host, so this is not the SSRF gate — but an archived
    # private/loopback address is not something the model has a reason to read. Literal only: no DNS.
    host = (host or "").strip("[]").lower()
    try:
        private = not ipaddress.ip_address(host).is_global
    except ValueError:
        private = host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
    if private:
        return f"Blocked: refusing archived copies of non-public address {host}.", url
    return None, url


def _cdx(name: str, params: dict, deadline: float) -> tuple[str | None, list[dict]]:
    """(error, rows) where each row is {timestamp, url, status, mime}."""
    _label, _domain, endpoint, _replay = ARCHIVES[name]
    if name == "wayback":
        params = {**params, "fl": "timestamp,original,statuscode,mimetype"}
    else:
        params = {**params, "fl": "timestamp,url,status,mime"}
    timeout = _remaining(deadline)
    if timeout <= 0:
        return "Failed to fetch URL: timed out.", []
    err, body, _ctype = fetch_url_raw(
        f"{endpoint}?{urllib.parse.urlencode(params)}",
        timeout = max(1, int(timeout)),
        deadline = deadline,
        website_policy = _policy(name),
    )
    if err is not None:
        return err, []
    body = body.strip()
    if not body:
        return None, []
    rows: list[dict] = []
    try:
        if name == "wayback":
            # Array of arrays; the first row is the field header.
            data = json.loads(body)
            for row in data[1:] if data and data[0] and data[0][0] == "timestamp" else data:
                ts, original, status, mime = (list(row) + ["", "", "", ""])[:4]
                rows.append({"timestamp": str(ts), "url": str(original), "status": str(status), "mime": str(mime)})
        else:
            # pywb: one JSON object per line.
            for line in body.splitlines():
                if line.strip():
                    obj = json.loads(line)
                    rows.append({k: str(obj.get(k, "")) for k in ("timestamp", "url", "status", "mime")})
    except (ValueError, TypeError, IndexError, AttributeError):
        # An outage page answers 200 with HTML; that is "unavailable", not "no copy".
        return f"{ARCHIVES[name][0]} returned an unreadable index (likely unavailable).", []
    return None, [r for r in rows if _TS_RE.match(r["timestamp"])]


def _fetch_wayback(url: str, ts: str, deadline: float, max_chars: int) -> tuple[str | None, str]:
    # Replay redirects to the closest capture itself, so no index lookup is needed (and the CDX
    # index is the less reliable half of the Wayback Machine).
    timeout = _remaining(deadline)
    if timeout <= 0:
        return "Failed to fetch URL: timed out.", ""
    text = fetch_page_text(
        f"{ARCHIVES['wayback'][3]}{ts or LATEST}id_/{url}",
        max_chars = max_chars,
        timeout = max(1, int(timeout)),
        website_policy = _policy("wayback"),
    )
    if _is_failure(text):
        return text, ""
    when = f"the capture closest to {_when(ts)}" if ts else "the latest capture"
    return None, f"Archived copy of {url} from the Wayback Machine ({when}):\n\n{text}"


def _fetch_arquivo(url: str, ts: str, deadline: float, max_chars: int) -> tuple[str | None, str]:
    params = {"url": url, "output": "json", "limit": 1, "filter": "status:200"}
    params.update({"closest": ts.ljust(14, "0"), "sort": "closest"} if ts else {"sort": "reverse"})
    err, rows = _cdx("arquivo", params, deadline)
    if err is not None:
        return err, ""
    if not rows:
        return "", ""
    hit = rows[0]
    timeout = _remaining(deadline)
    if timeout <= 0:
        return "Failed to fetch URL: timed out.", ""
    text = fetch_page_text(
        f"{ARCHIVES['arquivo'][3]}{hit['timestamp']}id_/{hit['url'] or url}",
        max_chars = max_chars,
        timeout = max(1, int(timeout)),
        website_policy = _policy("arquivo"),
    )
    if _is_failure(text):
        return text, ""
    return None, f"Archived copy of {url} from Arquivo.pt, captured {_when(hit['timestamp'])} UTC:\n\n{text}"


def _is_failure(text: str) -> bool:
    return text.startswith(("Failed to ", "Blocked: ", "(non-text content", "(binary content"))


def _is_miss(err: str) -> bool:
    return err == "" or err.startswith("Failed to fetch URL: HTTP 404")


def wayback_fetch(
    url: str,
    timestamp: str = "",
    archive: str = "",
    timeout: int = 30,
    website_policy: dict | None = None,
    max_chars: int | None = None,
) -> str:
    """Archived page text. Wayback first; Arquivo.pt only when Wayback has no copy or fails."""
    refusal, url = _gate(url, website_policy)
    if refusal:
        return refusal
    ts = _clean_ts(timestamp)
    if ts is None:
        return "Invalid timestamp: use YYYY, YYYYMMDD or YYYYMMDDhhmmss."
    names = (archive,) if archive else ORDER
    if any(n not in ARCHIVES for n in names):
        return f"Unknown archive {archive!r}: use one of {', '.join(ORDER)}."
    max_chars = max_chars or page_char_budget()
    deadline = time.monotonic() + (timeout or 30)

    errors = []
    fetchers = {"wayback": _fetch_wayback, "arquivo": _fetch_arquivo}
    for name in names:
        err, text = fetchers[name](url, ts, deadline, max_chars)
        if err is None:
            return text
        if not _is_miss(err):
            errors.append(f"{ARCHIVES[name][0]}: {err}")
    labels = " or ".join(ARCHIVES[n][0] for n in names)
    if errors:
        return f"No archived copy of {url} could be read from {labels}.\n" + "\n".join(errors)
    return f"No archived copy of {url} in {labels}."


def wayback_snapshots(
    url: str,
    from_date: str = "",
    to_date: str = "",
    limit: int = 10,
    archive: str = "",
    timeout: int = 30,
    website_policy: dict | None = None,
) -> str:
    """Newest-first list of captures across the archives, each fetchable by its timestamp."""
    refusal, url = _gate(url, website_policy)
    if refusal:
        return refusal
    lo, hi = _clean_ts(from_date), _clean_ts(to_date)
    if lo is None or hi is None:
        return "Invalid date: use YYYY, YYYYMMDD or YYYYMMDDhhmmss."
    names = (archive,) if archive else ORDER
    if any(n not in ARCHIVES for n in names):
        return f"Unknown archive {archive!r}: use one of {', '.join(ORDER)}."
    try:
        limit = min(max(int(limit), 1), MAX_SNAPSHOTS)
    except (TypeError, ValueError):
        limit = 10
    deadline = time.monotonic() + (timeout or 30)

    found, notes = [], []
    for name in names:
        params = {"url": url, "output": "json"}
        if lo:
            params["from"] = lo
        if hi:
            params["to"] = hi
        # Readable captures only: a 3xx capture replays as a hop to some other capture.
        if name == "wayback":
            # A negative limit returns the newest N; collapse drops identical consecutive captures.
            params.update({"limit": -limit, "collapse": "digest", "filter": "statuscode:200"})
        else:
            params.update({"limit": limit, "sort": "reverse", "filter": "status:200"})
        err, rows = _cdx(name, params, deadline)
        if err is not None:
            notes.append(f"{ARCHIVES[name][0]}: unavailable ({err})")
            continue
        found.extend((name, r) for r in rows)

    if not found:
        labels = " or ".join(ARCHIVES[n][0] for n in names)
        text = f"No captures of {url} in {labels}."
        return text + ("\n" + "\n".join(notes) if notes else "")
    found.sort(key = lambda item: item[1]["timestamp"], reverse = True)
    lines = [f"Captures of {url} (newest first):", ""]
    for name, r in found[:limit]:
        lines.append(f"{_when(r['timestamp'])}  {name:<7}  {r['status'] or '-':>3}  {r['mime'] or '-'}  ts={r['timestamp']}")
    if notes:
        lines += [""] + notes
    lines += [
        "",
        'To read one, call wayback with {"url": "<URL>", "timestamp": "<ts>", "archive": "<archive>"}.',
    ]
    return "\n".join(lines)
