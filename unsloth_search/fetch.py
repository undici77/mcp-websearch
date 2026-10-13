# SPDX-License-Identifier: AGPL-3.0-only
# Adapted layer over the generated verbatim copy — see upstream_verbatim.py

"""URL fetch: the guards are upstream's, the shell is local.

Every guard here (`_validate_and_resolve_host`, `_read_capped_body`, the pinned/SNI handlers, the
budget arithmetic) is imported unchanged from upstream. What is NOT here is marked ADAPTED with the
reason. Nothing in this file reimplements a safety decision — it only wires upstream's together.
"""

from __future__ import annotations

import codecs
import os
import random
import re
import time
import urllib.request

from ._html_to_md import SiteLinks, html_to_markdown
from .upstream_verbatim import (  # noqa: F401  re-exported for callers and tests
    _BINARY_APPLICATION_SUBTYPES,
    _BINARY_MAGIC,
    _ContentDecoder,
    _DECODED_CODINGS,
    _DOTTED_HOST_RE,
    _GITHUB_NAME_RE,
    _GITHUB_NON_OWNER_SEGMENTS,
    _HTML_DOCUMENT_RE,
    _HTML_LEADING_RE,
    _HTML_LEADING_TAGS,
    _IRI_PATH_SAFE,
    _IRI_QUERY_SAFE,
    _MAX_FETCH_BYTES,
    _MAX_HTML_FETCH_BYTES,
    _MAX_PAGE_CHARS,
    _MIN_PAGE_CHARS,
    _PAGE_CONTEXT_SHARE,
    _PDF_MAGIC,
    _PINNED_DIAL_TIMEOUT,
    _PINNED_PROBE_BUDGET,
    _PORT_RE,
    _UNICODE_BOM_CODECS,
    _USER_AGENTS,
    _NoRedirect,
    _PinnedHTTPHandler,
    _PinnedHTTPSConnection,
    _SNIHTTPSHandler,
    _explicit_proxy_applies,
    _fetch_budget_exceeded,
    _fetch_hop_timeout,
    _github_repo_readme_api_url,
    _has_binary_magic,
    _HTMLBodyLocator,
    _is_text_candidate_content_type,
    _looks_like_html,
    _looks_like_html_document,
    _looks_binary,
    _magic_head,
    _normalize_url_scheme,
    _pinned_create_connection,
    _pinned_netloc,
    _read_capped_body,
    _redirect_hop,
    _resolve_with_budget,
    _validate_and_resolve_host,
)
from .web_access_policy import check_url_access


def page_char_budget() -> int:
    """ADAPTED from upstream `_page_char_budget`.

    Upstream lowers the cap to what the *loaded model's* context window can hold, because a 16,000-char
    page is fine on a 128k model and irreducible on a 4,864-token one. An MCP server has no resident
    model to probe, so the cap is the constant or an explicit override.
    """
    try:
        value = int(os.environ.get("UNSLOTH_PAGE_MAX_CHARS", "") or _MAX_PAGE_CHARS)
    except (TypeError, ValueError):
        return _MAX_PAGE_CHARS
    return value if value > 0 else _MAX_PAGE_CHARS


def _truncate_page_text(text: str, max_chars: int) -> str:
    """ADAPTED from upstream `_truncate_page_text`: drops the `_dense_char_limit` re-scan, which
    exists to claw back budget on markup-dense bodies and depends on Studio's token accounting."""
    if not text:
        return "(page returned no readable text)"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n... (truncated, {len(text)} chars total)"
    return text


def fetch_url_raw(
    url: str,
    timeout: int = 30,
    extra_headers: dict | None = None,
    deadline: float | None = None,
    cancel_event = None,
    website_policy: dict | None = None,
) -> tuple[str | None, str, str]:
    """ADAPTED from upstream `_fetch_url_raw`: returns ``(error, body_text, content_type)``.

    Same gates, in the same order: scheme, domain policy, non-public-IP refusal, DNS pinning with the
    hostname kept for SNI and cert verification, one wall-clock deadline across every redirect hop, a
    capped chunked body read that undoes gzip/deflate, binary rejection by MIME then magic, then
    declared/BOM/utf-8 decode.

    ADAPTED out, both failing closed:
      * PDF text extraction (upstream reads to `_MAX_PDF_FETCH_BYTES` for the EOF xref) — a PDF is
        refused by magic instead of half-read.
      * `<meta http-equiv=refresh>` as a redirect hop — the page's own converted body is returned.

    No input reaches the caller as an exception: the URL is model-supplied, so every malformed form
    resolves to one of these strings.
    """
    from urllib.error import HTTPError as _HTTPError
    from urllib.parse import quote, urljoin, urlunparse, urlparse

    # Before the policy gate: it requires an http(s) scheme, so a bare host would be refused there and never reach the
    # fetch.
    url = _normalize_url_scheme(url)
    allowed, reason, canonical_host = check_url_access(url, website_policy)
    if not allowed:
        return reason, "", ""

    # check_url_access already parsed this and read .port, so this cannot raise.
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ok, reason, pinned_ips = _resolve_with_budget(
        canonical_host,
        port,
        deadline,
        cancel_event,
    )
    if not ok:
        return reason, "", ""

    try:
        max_bytes = _MAX_FETCH_BYTES
        current_url = url
        current_host = canonical_host
        ua = random.choice(_USER_AGENTS)

        for _hop in range(5):
            budget_error = _fetch_budget_exceeded(deadline, cancel_event)
            if budget_error is not None:
                return budget_error, "", ""
            cp = urlparse(current_url)
            # http.client rejects a non-ASCII selector outright.
            cp = cp._replace(
                path = quote(cp.path, safe = _IRI_PATH_SAFE),
                params = quote(cp.params, safe = _IRI_PATH_SAFE),
                query = quote(cp.query, safe = _IRI_QUERY_SAFE),
            )
            # Bracket IPv6 so the netloc stays a valid URL.
            validated_netloc = f"[{current_host}]" if ":" in current_host else current_host
            if cp.port:
                validated_netloc = f"{validated_netloc}:{cp.port}"
            # Decide routing once, on the netloc urllib tests: a pinned request carries an IP, which no NO_PROXY entry
            # matches, so the opener below has to carry the decision rather than re-derive it.
            proxied = _explicit_proxy_applies(cp.scheme, validated_netloc)
            if os.environ.get("UNSLOTH_STUDIO_DISABLE_DNS_PINNING") == "1" and proxied:
                # Enterprise proxies need the hostname in CONNECT for policy and TLS interception, and they resolve
                # it, so nothing rebinds behind us.
                request_url = urlunparse(cp._replace(netloc = validated_netloc))
            else:
                # Pin the first validated IP against DNS rebinding; the rest are below.
                request_url = urlunparse(cp._replace(netloc = _pinned_netloc(pinned_ips[0], cp.port)))

            # The proxy makes the origin connection, to the one address the URL pins.
            walk = () if proxied else pinned_ips
            handlers = [
                _NoRedirect,
                _SNIHTTPSHandler(current_host, walk),
                _PinnedHTTPHandler(walk),
            ]
            if not proxied:
                # An empty ProxyHandler is the documented way to opt a request out.
                handlers.append(urllib.request.ProxyHandler({}))
            opener = urllib.request.build_opener(*handlers)

            headers = {
                "User-Agent": ua,
                "Host": validated_netloc,
                "Accept-Encoding": "gzip, deflate",
            }
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(request_url, headers = headers)
            try:
                # Cap the socket timeout at the time left on the overall deadline so a single slow hop cannot outlast
                # the whole fetch budget.
                resp = opener.open(req, timeout = _fetch_hop_timeout(timeout, deadline))
            except _HTTPError as e:
                if e.code not in (301, 302, 303, 307, 308):
                    return f"Failed to fetch URL: HTTP {e.code} {getattr(e, 'reason', '')}", "", ""
                location = e.headers.get("Location")
                if not location:
                    return "Failed to fetch URL: redirect missing Location header.", "", ""
                current_url = urljoin(current_url, location)
                hop_error, current_host, pinned_ips = _redirect_hop(
                    current_url,
                    website_policy,
                    deadline,
                    cancel_event,
                )
                if hop_error is not None:
                    return hop_error, "", ""
                continue

            # Brotli/zstd stay refused: only what _ContentDecoder undoes is decoded.
            coding = (resp.headers.get("Content-Encoding") or "").strip().lower()
            if coding not in ("", "identity", *_DECODED_CODINGS):
                return f"Failed to fetch URL: unsupported Content-Encoding {coding[:40]!r}.", "", ""
            decoder = _ContentDecoder(coding) if coding in _DECODED_CODINGS else None

            # get_content_type() defaults to "text/plain" when the header is absent (RFC 2045); report "" instead so
            # callers can tell a missing header apart from a server that really declared text/plain.
            if resp.headers.get("Content-Type") is None:
                content_type = ""
            else:
                content_type = (resp.headers.get_content_type() or "").lower()

            # Read the capped body in chunks, re-checking the budget between each (see _read_capped_body).
            # Declared HTML reads wider but retains only _MAX_FETCH_BYTES past </head>: a flat cap there
            # returns head, not content. PDFs stay at max_bytes — refused by magic, never read for xref.
            declared_html = content_type in ("text/html", "application/xhtml+xml")
            body_error, raw_bytes = _read_capped_body(
                resp,
                _MAX_HTML_FETCH_BYTES if declared_html else max_bytes,
                timeout,
                deadline,
                cancel_event,
                body_window = max_bytes if declared_html else None,
                charset = resp.headers.get_content_charset() if declared_html else None,
                decoder = decoder,
            )
            if body_error is not None:
                return body_error, "", ""
            break
        else:
            return "Failed to fetch URL: too many redirects.", "", ""

        # Reject known-binary MIME types before decoding. Binary is returned as the error string so the caller
        # surfaces the placeholder, not replacement chars.
        if not _is_text_candidate_content_type(content_type):
            # Only echo a clean MIME token back to the model.
            m = re.match(r"[\w.+-]+/[\w.+-]+", content_type or "")
            safe_type = m.group(0) if m else "unknown type"
            return (
                f"(non-text content: {safe_type}, {len(raw_bytes)} bytes; not readable as text)",
                "",
                content_type,
            )

        # Catch text-labeled binary via its magic signature.
        if _has_binary_magic(raw_bytes):
            return (
                f"(binary content, {len(raw_bytes)} bytes; not readable as text)",
                "",
                content_type,
            )

        declared = resp.headers.get_content_charset()
        try:
            if declared:
                codecs.lookup(declared)
        except (LookupError, ValueError):
            # ValueError, not only LookupError: a NUL inside the label.
            declared = None
        bom_codec = next(
            (codec for bom, codec in _UNICODE_BOM_CODECS if raw_bytes.startswith(bom)),
            None,
        )
        # ADAPTED: upstream also prescans <meta charset> per WHATWG labels; declared/BOM/utf-8 here.
        fallback_codec = bom_codec or "utf-8"
        try:
            raw_text = raw_bytes.decode(declared or fallback_codec, errors = "replace")
        except (LookupError, ValueError):
            declared = None
            raw_text = raw_bytes.decode(fallback_codec, errors = "replace")

        if _looks_binary(raw_text):
            return (
                f"(binary content, {len(raw_bytes)} bytes; not readable as text)",
                "",
                content_type,
            )

        return None, raw_text, content_type
    except _HTTPError as e:
        return f"Failed to fetch URL: HTTP {e.code} {getattr(e, 'reason', '')}", "", ""
    except Exception as e:
        return f"Failed to fetch URL: {e}", "", ""


def fetch_page_text(
    url: str,
    max_chars: int | None = None,
    timeout: int = 30,
    cancel_event = None,
    website_policy: dict | None = None,
) -> str:
    """ADAPTED from upstream `_fetch_page_text`: same routing, same conversion, same cap.

    GitHub repo roots go to the README API so the model reads the README, not the repo page's UI
    chrome. HTML becomes Markdown with the vendored stdlib converter; plain text is kept as-is so its
    whitespace survives.
    """
    if max_chars is None:
        max_chars = page_char_budget()
    # One wall-clock budget for the whole fetch. The README API attempt and its HTML fallback both draw from it, so a
    # slow/failed API call cannot hand the fallback a fresh full timeout and double the worst case.
    deadline = None if timeout is None else time.monotonic() + timeout

    # Before the policy gate (needs a scheme) and the README routing (reads host/path).
    url = _normalize_url_scheme(url)
    allowed, reason, _hostname = check_url_access(url, website_policy)
    if not allowed:
        return reason
    policy_kwargs = {"website_policy": website_policy} if website_policy is not None else {}
    readme_api_url = _github_repo_readme_api_url(url)
    if readme_api_url:
        err, body, _ctype = fetch_url_raw(
            readme_api_url,
            timeout = timeout,
            extra_headers = {
                "Accept": "application/vnd.github.raw+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            deadline = deadline,
            cancel_event = cancel_event,
            **policy_kwargs,
        )
        # The README API is unauthenticated and rate-limited; on any failure fall back to the HTML page fetch. A 200
        # body is authoritative even when it is HTML (a .html README): convert it rather than falling back to the repo
        # page's UI chrome, keeping the raw body if extraction yields nothing.
        if err is None and body.strip():
            readme_body = body
            # The raw file is almost always Markdown. Only a real HTML document (a .html README) is converted; a
            # Markdown README that merely opens with a block tag is kept as-is (see _HTML_DOCUMENT_RE).
            if _looks_like_html_document(body):
                converted = html_to_markdown(
                    body, main_content = True, max_span_chars = max_chars // 2
                )
                readme_body = converted if converted.strip() else body
            if readme_body.strip():
                return _truncate_page_text(
                    f"README of {url} (fetched via the GitHub README API):\n\n" + readme_body,
                    max_chars,
                )

    err, body, content_type = fetch_url_raw(
        url,
        timeout = timeout,
        deadline = deadline,
        cancel_event = cancel_event,
        **policy_kwargs,
    )
    if err is not None:
        return err

    # Trust a declared HTML type, and otherwise sniff the body: servers with a missing or wrong Content-Type still get
    # converted, matching the pre-extraction behavior of always converting.
    is_html = "html" in content_type or _looks_like_html(body)
    if not is_html:
        # Plain text / markdown / JSON (e.g. raw.githubusercontent.com): converting through the HTML renderer would
        # collapse its whitespace.
        return _truncate_page_text(body.strip(), max_chars)

    # Convert HTML to Markdown with the builtin converter (no external deps). Same-site links are kept
    # while the page fits and dropped to their text when it does not. Gated on length alone:
    # upstream's extra _dense_char_limit density re-scan stays out of scope here.
    site_links = SiteLinks(url)
    text = html_to_markdown(
        body, main_content = True, site_links = site_links, max_span_chars = max_chars // 2
    )
    if text and len(text) <= max_chars:
        return text
    return _truncate_page_text(site_links.strip(text), max_chars)
