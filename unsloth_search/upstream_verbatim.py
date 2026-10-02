# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""GENERATED — DO NOT EDIT.

Every symbol here is an AST-extracted copy of the same symbol in
unslothai/unsloth studio/backend/core/inference/tools.py. Regenerate with:

    scripts/build_port.py

Fidelity is structural, not eyeballed: if upstream changes a symbol, this file changes on the next
build, or `scripts/align.py` says so. Anything that needed local judgement lives in fetch.py /
search.py instead, marked adapted.
"""

from __future__ import annotations

import codecs
import http.client
import os
import queue
import random
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request



_EXEC_TIMEOUT = 300  # 5 minutes


_POLICY_OVERFETCH = 4


EMPTY_SEARCH_RESULTS = (
    "No results found.",
    "No results found within the website access limits.",
)


_DDGS_EMPTY_SWEEP = "No results found"


_SEARCH_ENGINE_TIERS = (
    ("wikipedia", "brave", "duckduckgo", "mojeek", "startpage"),
    ("grokipedia", "google", "yahoo"),
)


WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web and fetch page content. Returns snippets for all results. "
            "Use the url parameter to fetch full page text from a specific URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "url": {
                    "type": "string",
                    "description": "A URL to fetch full page content from (instead of searching). Use this to read a page found in search results.",
                },
            },
            "required": [],
        },
    },
}


_WEB_SEARCH_QUERY_ALIASES = ("query", "q", "search_query", "search", "text")


_WEB_SEARCH_URL_ALIASES = ("url", "uri", "href", "link")


def _first_nonempty_arg(arguments: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_web_search_args(arguments) -> tuple[str, str]:
    args = arguments if isinstance(arguments, dict) else {}
    return (
        _first_nonempty_arg(args, _WEB_SEARCH_QUERY_ALIASES),
        _first_nonempty_arg(args, _WEB_SEARCH_URL_ALIASES),
    )


def canonicalize_web_search_arguments(arguments) -> dict:
    # URL mode returns before _web_search reads query or image_queries, so they are dropped from the key.
    args = dict(arguments) if isinstance(arguments, dict) else {}
    query, url = _resolve_web_search_args(args)
    if url:
        return {"url": url}
    canonical: dict = {}
    if query:
        canonical["query"] = query
    if "image_queries" in args:
        canonical["image_queries"] = args["image_queries"]
    return canonical


_MAX_PAGE_CHARS = 16000  # cap fetched page text (after HTML-to-MD conversion)


_PAGE_CONTEXT_SHARE = 0.35


_MIN_PAGE_CHARS = 2000


_MAX_FETCH_BYTES = 512 * 1024


_IRI_PATH_SAFE = "/%:@!$&'()*+,;="


_IRI_QUERY_SAFE = "/%:@!$&'()*+,;=?"


_BINARY_CHAR_RE = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1a\\x1c-\\x1f\\x7f-\\x9f\\ufffd]")


_MIN_BINARY_CHARS = 16


_BINARY_CHAR_DIVISOR = 8


_PDF_MAGIC = b"%PDF-"


_BINARY_MAGIC = (
    _PDF_MAGIC,
    b"PK\x03\x04",  # zip / docx / xlsx / pptx / epub / jar
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",  # OLE / legacy Office
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",
    b"GIF89a",
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"\x28\xb5\x2f\xfd",  # zstd
)


_UNICODE_BOM_CODECS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
    (codecs.BOM_UTF8, "utf-8-sig"),
)


def _looks_binary(text: str) -> bool:
    """Whether control or undecodable characters exceed the binary threshold."""
    return len(_BINARY_CHAR_RE.findall(text)) > max(
        _MIN_BINARY_CHARS, len(text) // _BINARY_CHAR_DIVISOR
    )


def _magic_head(data: bytes) -> bytes:
    head = data[:1024].lstrip()
    for bom, _codec in _UNICODE_BOM_CODECS:
        if head.startswith(bom):
            head = head.removeprefix(bom).lstrip()
            break
    return head


def _has_binary_magic(data: bytes) -> bool:
    """Whether a common binary signature follows optional BOM or whitespace."""
    return _magic_head(data).startswith(_BINARY_MAGIC)


_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
)


_tls_ctx = ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_PINNED_DIAL_TIMEOUT = 1.0


_PINNED_PROBE_BUDGET = 0.25


def _pinned_create_connection(addresses):
    """``socket.create_connection`` that walks *addresses* instead of resolving.

    Pinning to one validated address dropped the walk every other client gets. Walking
    here rather than around ``opener.open`` keeps the caller's timeout covering one
    whole response.
    """
    import socket

    def create(
        address,
        timeout = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address = None,
    ):
        if timeout is socket._GLOBAL_DEFAULT_TIMEOUT:
            timeout = socket.getdefaulttimeout()
        # A proxy dial is not an origin dial, and callers withhold the addresses for it.
        if address[0] not in addresses:
            return socket.create_connection(address, timeout, source_address)

        port = address[1]
        expiry = None if timeout is None else time.monotonic() + timeout
        probe_timeout = (
            None
            if timeout is None
            else min(
                timeout * _PINNED_PROBE_BUDGET / len(addresses),
                _PINNED_DIAL_TIMEOUT,
            )
        )
        error = None
        # A brief look at every address, so one answering neither way cannot hold up the
        # reachable ones, then an equal share of the rest so none strands the next.
        passes = (True, False) if len(addresses) > 1 and timeout is not None else (False,)
        for probe in passes:
            for index, ip in enumerate(addresses):
                if timeout is None:
                    dial_timeout = None
                elif probe:
                    dial_timeout = probe_timeout
                else:
                    # An overrun leaves nothing; zero still attempts, negative raises.
                    dial_timeout = max(
                        (expiry - time.monotonic()) / (len(addresses) - index),
                        0,
                    )
                try:
                    sock = socket.create_connection((ip, port), dial_timeout, source_address)
                except OSError as exc:
                    error = exc
                    continue
                # create_connection leaves the caller's timeout on the socket, not its own.
                sock.settimeout(timeout)
                return sock
        raise error

    return create


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a pinned IP, using a different hostname for SNI and
    cert verification.

    SSRF IP-pinning rewrites URLs to raw IPs; a normal HTTPSConnection would then
    send no SNI and verify the cert against the IP (both fail). This splits the
    concerns: TCP connects to a validated IP, TLS uses ``sni_hostname``.
    """

    def __init__(
        self,
        host: str,
        *,
        sni_hostname: str,
        addresses = (),
        **kwargs,
    ):
        super().__init__(host, **kwargs)
        self._sni_hostname = sni_hostname
        if addresses:
            self._create_connection = _pinned_create_connection(tuple(addresses))

    def connect(self):
        http.client.HTTPConnection.connect(self)
        # TLS handshake with the real hostname for SNI + cert verification.
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname = self._sni_hostname,
        )


class _SNIHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPS handler sending the correct SNI hostname during TLS handshake.

    SSRF IP-pinning breaks SNI and cert verification; this returns a
    ``_PinnedHTTPSConnection`` that connects to the pinned IP but verifies TLS
    against the original hostname. *addresses* are every validated address, pinned
    one first, for ``_pinned_create_connection`` to walk.
    """

    def __init__(
        self,
        hostname: str,
        addresses = (),
    ):
        super().__init__(context = _tls_ctx)
        self._sni_hostname = hostname
        self._addresses = tuple(addresses)

    def https_open(self, req):
        return self.do_open(self._sni_connection, req)

    def _sni_connection(self, host, **kwargs):
        kwargs["context"] = _tls_ctx
        return _PinnedHTTPSConnection(
            host,
            sni_hostname = self._sni_hostname,
            addresses = self._addresses,
            **kwargs,
        )


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, addresses = ()):
        super().__init__()
        self._addresses = tuple(addresses)

    def http_open(self, req):
        return self.do_open(self._pinned_connection, req)

    def _pinned_connection(self, host, **kwargs):
        conn = http.client.HTTPConnection(host, **kwargs)
        if self._addresses:
            conn._create_connection = _pinned_create_connection(self._addresses)
        return conn


def _explicit_proxy_applies(scheme: str, host: str) -> bool:
    """Whether urllib routes a *scheme* request for *host* through a proxy.

    Only a proxied fetch may keep the hostname in the request URL: the proxy resolves it, so this
    host never looks it up again. A direct one would, which is the DNS-rebinding window, so it stays
    pinned to the validated IP.

    *host* must be the ``host[:port]`` form ``Request.host`` carries, since that is what
    ``ProxyHandler`` passes to ``proxy_bypass``; probing the bare hostname instead would disagree
    with it on a port-qualified NO_PROXY entry.
    """
    from urllib.request import getproxies, proxy_bypass

    # ProxyHandler lowercases every mapping key, and the Windows registry can hand back "HTTPS=...", so normalize
    # before testing or a proxy-only host goes direct.
    if scheme not in {key.lower() for key in getproxies()}:
        return False
    try:
        return not proxy_bypass(host)
    except (OSError, ValueError):
        # proxy_bypass reads system config on macOS/Windows; failure falls back to pinning.
        return False


def _validate_and_resolve_host(hostname: str, port: int) -> tuple[bool, str, list[str]]:
    """Resolve *hostname*, reject non-public IPs, return the pinned IP strings.

    Returns ``(ok, reason_or_empty, resolved_ips)`` in resolver order. The caller pins
    to these (with a ``Host`` header) rather than resolving again, which is the DNS
    rebinding window, and walks them as ``socket.create_connection`` would.
    """
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(hostname, port, type = socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        # IDNA encoding rejects a hostname with UnicodeError, not OSError.
        return False, f"Failed to resolve host: {e}", []

    if not infos:
        return False, f"Failed to resolve host: no addresses for {hostname!r}", []

    resolved = []
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        # `not ip.is_global` is the source of truth (also rejects CGNAT and benchmarking/doc ranges); the explicit
        # predicates only label the error.
        if (
            not ip.is_global
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, f"Blocked: refusing to fetch non-public address {ip}.", []
        if sockaddr[0] not in resolved:
            resolved.append(sockaddr[0])

    return True, "", resolved


_BINARY_APPLICATION_SUBTYPES = frozenset(
    {
        "epub+zip",
        "gzip",
        "java-archive",
        "pdf",
        "vnd.apple.installer+xml",
        "wasm",
        "x-7z-compressed",
        "x-bzip2",
        "x-gzip",
        "x-rar-compressed",
        "x-tar",
        "x-xz",
        "zip",
        "zstd",
    }
)


def _is_text_candidate_content_type(content_type: str | None) -> bool:
    """Whether a MIME type is textual or ambiguous enough for byte sniffing."""
    match = re.match(r"[\w.+-]+/[\w.+-]+", content_type or "")
    if not match:
        return True
    ct = match.group(0).lower()
    if ct.startswith("text/"):
        return True
    if ct.startswith("application/"):
        subtype = ct[len("application/") :]
        return subtype not in _BINARY_APPLICATION_SUBTYPES
    return False


_GITHUB_NON_OWNER_SEGMENTS = frozenset(
    {
        "about",
        "apps",
        "codespaces",
        "collections",
        "contact",
        "customer-stories",
        "dashboard",
        "discussions",
        "enterprise",
        "explore",
        "features",
        "issues",
        "join",
        "login",
        "marketplace",
        "new",
        "notifications",
        "organizations",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "security",
        "settings",
        "signup",
        "site",
        "sponsors",
        "team",
        "topics",
        "trending",
    }
)


_GITHUB_NAME_RE = re.compile(r"\A[A-Za-z0-9_.\-]{1,100}\Z")


def _github_repo_readme_api_url(url: str) -> str | None:
    """README API URL for a ``github.com/{owner}/{repo}`` page, else None. A repo root page rendered
    as HTML is mostly UI chrome (nav, file table, stats); the ``/readme`` API returns the raw
    README markdown unauthenticated, which is what the model actually wants to read."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2:
        return None
    owner, repo = parts
    if owner.lower() in _GITHUB_NON_OWNER_SEGMENTS:
        return None
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not (_GITHUB_NAME_RE.match(owner) and _GITHUB_NAME_RE.match(repo)):
        return None
    return f"https://api.github.com/repos/{owner}/{repo}/readme"


def _fetch_budget_exceeded(deadline, cancel_event):
    """User-facing error string when the fetch must stop early, else None."""
    if cancel_event is not None and cancel_event.is_set():
        return "Failed to fetch URL: cancelled."
    if deadline is not None and time.monotonic() >= deadline:
        return "Failed to fetch URL: timed out."
    return None


def _fetch_hop_timeout(timeout, deadline):
    """Per-operation socket timeout: the lesser of the caller's per-op timeout and the time left on
    the deadline, so one slow hop cannot overrun the whole budget. Callers check
    ``_fetch_budget_exceeded`` first, so remaining time is positive here; the tiny floor only
    guards a race."""
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        remaining = 0.001
    return remaining if timeout is None else min(timeout, remaining)


def _resolve_with_budget(hostname, port, deadline, cancel_event):
    """``_validate_and_resolve_host`` bounded by the overall fetch budget. ``getaddrinfo`` is
    blocking with no deadline of its own, so a slow resolver (or a request cancelled before
    dispatch) could run past the budget. Resolve on a daemon thread and poll the budget so the
    fetch aborts on time; the abandoned lookup is discarded. With no deadline and no cancel_event
    this is a plain synchronous call, so opt-out callers keep the old behavior and cost."""
    budget_error = _fetch_budget_exceeded(deadline, cancel_event)
    if budget_error is not None:
        return False, budget_error, []
    if deadline is None and cancel_event is None:
        return _validate_and_resolve_host(hostname, port)

    result: "queue.Queue" = queue.Queue(maxsize = 1)

    def _resolve():
        try:
            result.put(_validate_and_resolve_host(hostname, port))
        except Exception as exc:  # defensive: never let the worker die silently
            result.put((False, f"Failed to resolve host: {exc}", []))

    threading.Thread(target = _resolve, name = "web-fetch-dns", daemon = True).start()
    while True:
        budget_error = _fetch_budget_exceeded(deadline, cancel_event)
        if budget_error is not None:
            return False, budget_error, []
        try:
            return result.get(timeout = 0.05)
        except queue.Empty:
            continue


def _read_capped_body(resp, max_bytes, timeout, deadline, cancel_event):
    """Read up to ``max_bytes``, enforcing the overall budget between chunks. A single
    ``resp.read(max_bytes)`` can block for the whole transfer if the server dribbles bytes just
    inside each socket-inactivity timeout, so the body is read in chunks with the budget
    re-checked (and the socket timeout re-tightened toward the deadline) each round. The joined
    bytes are identical to one capped read. Returns ``(error_or_None, body_bytes)``."""
    # Best-effort handle on the underlying socket so its timeout tightens as the deadline nears; absent on test
    # doubles, where the between-chunk budget check still bounds the read.
    sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
    # A buffered read(n) keeps receiving until n bytes arrive, so a drip never reaches the
    # budget check; read1 returns after one receive.
    read = getattr(resp, "read1", None) or resp.read
    chunks = []
    remaining = max_bytes
    while remaining > 0:
        budget_error = _fetch_budget_exceeded(deadline, cancel_event)
        if budget_error is not None:
            try:
                resp.close()
            except Exception:
                pass
            return budget_error, b""
        if sock is not None:
            try:
                sock.settimeout(_fetch_hop_timeout(timeout, deadline))
            except Exception:
                pass
        chunk = read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    budget_error = _fetch_budget_exceeded(deadline, cancel_event)
    if budget_error is not None:
        try:
            resp.close()
        except Exception:
            pass
        return budget_error, b""
    return None, b"".join(chunks)


_DOTTED_HOST_RE = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


_PORT_RE = re.compile(r"[0-9]{1,5}")


def _normalize_url_scheme(url: str) -> str:
    """Prepend ``https://`` to bare hosts (``google.com``, ``example.com:8443``).

    ``urlparse`` reads the host of a ``host:port`` input as the scheme, so those are recognised by a
    dotted host-like scheme with an empty netloc. Rewrites a dotted host with an optional in-range
    port, and the ``//host`` form. Real schemes (``file:``, ``javascript:``, including ``file:80``),
    root-relative paths (``/login``) and bad ports are returned untouched so the caller rejects
    them. A dotted scheme is indistinguishable from ``host:port``, so ``com.acme.app:443/cb`` is
    rewritten too; an empty port is kept as-is.

    The host is matched against the raw authority, never against what ``urlparse`` returned, because
    urlsplit strips tabs/newlines (3.10) and leading C0/space (3.12). Anything it would strip fails
    the match, so the decision and the rewritten string cannot disagree across versions.
    """
    from urllib.parse import urlparse

    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        # Unmatched IPv6 brackets, or an NFKC-decomposing netloc: not a bare host.
        return url
    if parsed.scheme:
        if parsed.netloc or not _DOTTED_HOST_RE.fullmatch(parsed.scheme):
            return url
        rest = url
    elif url.startswith("//"):
        rest = url[2:]
    elif url.startswith("/"):
        return url
    else:
        rest = url

    authority = re.split(r"[/?#]", rest, maxsplit = 1)[0]
    host, _, port = authority.partition(":")
    if not _DOTTED_HOST_RE.fullmatch(host):
        return url
    if port and not (_PORT_RE.fullmatch(port) and 1 <= int(port) <= 65535):
        return url
    return "https://" + rest


def _pinned_netloc(ip: str, port: int | None) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return f"{host}:{port}" if port else host


def _redirect_hop(url: str, website_policy, deadline, cancel_event) -> tuple[str | None, str, list]:
    from urllib.parse import urlparse
    from .web_access_policy import check_url_access

    allowed, reason, host = check_url_access(url, website_policy)
    if not allowed:
        return reason, "", []
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ok, reason, pinned_ips = _resolve_with_budget(host, port, deadline, cancel_event)
    if not ok:
        return reason, "", []
    return None, host, pinned_ips


_HTML_LEADING_TAGS = (
    "html",
    "head",
    "body",
    "title",
    "meta",
    "link",
    "script",
    "style",
    "article",
    "section",
    "main",
    "header",
    "footer",
    "nav",
    "aside",
    "figure",
    "form",
    "ul",
    "ol",
    "dl",
    "pre",
    "blockquote",
)


_HTML_LEADING_RE = re.compile(r"<(?:!doctype\s+html|/?(?:" + "|".join(_HTML_LEADING_TAGS) + r")\b)")


def _looks_like_html(body: str) -> bool:
    """True only when the document ITSELF opens with HTML. Matches an HTML doctype or a leading
    document/structure tag after optional whitespace, not a mere substring, so a Markdown README
    with a fenced HTML example or tags further down stays Markdown. Also detects bare fragments
    with no doctype, so a page with a missing/wrong Content-Type is still converted."""
    probe = body.lstrip()[:256].lower()
    return bool(_HTML_LEADING_RE.match(probe))


_HTML_DOCUMENT_RE = re.compile(r"<(?:!doctype\s+html\b|/?(?:html|head|body)\b)")


def _looks_like_html_document(body: str) -> bool:
    """True only when the body opens as a full HTML document (e.g. a .html README)."""
    probe = body.lstrip()[:256].lower()
    return bool(_HTML_DOCUMENT_RE.match(probe))


def _search_failure_message(exc: BaseException, timeout: int) -> str:
    """Turn a ddgs exception into text the model and the UI can act on.

    ddgs raises for an empty sweep as well as for refusals, so an unclassified ``Search failed:
    {exc}`` reports nothing matched and every engine throttled us the same way. Matched by class
    name because ddgs is imported lazily and tests stub the module.

    The RatelimitException arm is forward-looking: ddgs 9.14.4 defines the class but raises it
    nowhere, and no engine inspects the status code, so a throttled sweep parses to zero items and
    arrives here as the empty-sweep DDGSException instead.
    """
    name = type(exc).__name__
    if name == "RatelimitException":
        return (
            "Search failed: the search engines are rate limiting this machine. Wait a minute "
            'before searching again, or read a known page directly with {"url": "<URL>"}.'
        )
    if name == "TimeoutException":
        budget = f" within {timeout}s" if timeout else ""
        return f"Search failed: the search engines did not respond{budget}."
    # Only the base exception, so a subclass that happens to quote the phrase stays an error.
    if name == "DDGSException" and _DDGS_EMPTY_SWEEP in str(exc):
        return EMPTY_SEARCH_RESULTS[0]
    return f"Search failed: {exc}"


def _resolve_engine_tiers(text_engines) -> list:
    """``_SEARCH_ENGINE_TIERS`` reduced to the engines this ddgs actually has, in tier order.

    Naming an engine the registry lacks is not an error: ddgs 9.8.0 raises ``KeyError`` on the first
    unknown name and silently re-runs the request as ``auto``, the Yandex fan-out this exists to
    prevent, and tier 1 trips it there because 9.8.0 ships no ``startpage``. An empty tier is dropped
    for the same reason.
    """
    engines = text_engines or {}
    resolved = []
    for tier in _SEARCH_ENGINE_TIERS:
        live = [
            name
            for name in tier
            if engines.get(name) is not None and not getattr(engines.get(name), "disabled", False)
        ]
        if live:
            resolved.append(",".join(live))
    return resolved
