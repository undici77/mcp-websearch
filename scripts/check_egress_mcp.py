#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Egress gate for the search tiers — the MCP-side mirror of the fork's §18.

CONNECT-only: the tunnel is never decrypted, so no CA is installed and nothing is a
man in the middle. A CONNECT names the host, which is everything the allowlist needs.
Test-time only: nothing here runs during normal operation.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

SELFTEST_HOST = "mcp-egress-selftest.invalid"

# engine name -> the host it reaches. Every tier engine MUST appear here; a test enforces it,
# so widening the tier list without widening this gate is a failing build.
ENGINE_HOSTS = {
    "wikipedia": "wikipedia.org",
    "brave": "brave.com",
    "duckduckgo": "duckduckgo.com",
    "mojeek": "mojeek.com",
    "startpage": "startpage.com",
    "grokipedia": "grokipedia.com",
    "google": "google.com",
    "yahoo": "yahoo.com",
}


def approved_hosts():
    """Derived from the tier list, never a parallel copy of it."""
    from unsloth_search.upstream_verbatim import _SEARCH_ENGINE_TIERS

    tiers = {str(e).strip() for tier in _SEARCH_ENGINE_TIERS for e in tier}
    missing = tiers - set(ENGINE_HOSTS)
    if missing:
        raise SystemExit(f"tier engine(s) with no host mapping: {sorted(missing)} — widen ENGINE_HOSTS")
    # `url` mode is deliberately absent: it fetches a host the model named, bounded by the
    # client's per-call prompt, never by this list.
    return {ENGINE_HOSTS[e] for e in tiers} | {"serpapi.com"}

_seen = []
_lock = threading.Lock()


def _note(host, kind):
    if host:
        with _lock:
            _seen.append({"host": host.lower(), "kind": kind})


def _tunnel(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode("latin-1").strip()
        if not line:
            return
        parts = line.split()
        if len(parts) < 2:
            return
        method, target = parts[0].upper(), parts[1]

        if method == "CONNECT":
            host = target.split(":")[0]
            _note(host, "connect")
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            try:
                remote = socket.create_connection((host, int(target.rsplit(":", 1)[-1])), timeout = 30)
            except OSError:
                return
            threading.Thread(target = _tunnel, args = (remote, self.connection), daemon = True).start()
            _tunnel(self.connection, remote)
            return

        # Plain HTTP arrives as an absolute URI, which names the full path too.
        parsed = urlparse(target)
        _note(parsed.hostname, method.lower())
        try:
            conn = http.client.HTTPConnection(parsed.netloc, timeout = 30)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            conn.request(method, parsed.path or "/", body = body, headers = dict(self.headers))
            resp = conn.getresponse()
            self.wfile.write(f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode())
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection"):
                    self.wfile.write(f"{k}: {v}\r\n".encode())
            self.wfile.write(b"Connection: close\r\n\r\n")
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _public(host):
    host = str(host or "").lower().strip(".")
    bits = host.split(".")
    return ".".join(bits[-2:]) if len(bits) > 2 else host


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    httpd = Server(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target = httpd.serve_forever, daemon = True).start()

    proxy = f"http://127.0.0.1:{port}"
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["HTTP_PROXY"] = proxy
    os.environ["https_proxy"] = proxy
    os.environ["http_proxy"] = proxy

    # Self-test first: a host that can never be allowed. If the proxy does not see it,
    # the gate is blind and a clean verdict would mean nothing.
    try:
        urllib.request.urlopen(f"https://{SELFTEST_HOST}/", timeout = 10)
    except Exception:
        pass
    time.sleep(0.4)
    seen_selftest = any(e["host"] == SELFTEST_HOST for e in _seen)
    if not seen_selftest:
        print(f"✗ SELF-TEST FAILED: the proxy never saw {SELFTEST_HOST}.")
        print("  The detector is blind — its verdict cannot be trusted.")
        httpd.shutdown()
        return 2
    print(f"  self-test   : proxy caught {SELFTEST_HOST} ✓")
    _seen.clear()

    query = os.environ.get("WEBSEARCH_EGRESS_QUERY", "python packaging standards")
    started = time.monotonic()
    from unsloth_search.search import web_search

    out = web_search(query, timeout = 60)
    time.sleep(0.5)
    httpd.shutdown()

    hosts = sorted({_public(e["host"]) for e in _seen if e["host"] != SELFTEST_HOST})
    print(f"  query       : {query!r} → {len(out)} chars in {time.monotonic() - started:.1f}s")
    print(f"  egress hosts: {hosts or 'NONE'}")

    if not hosts:
        bypassed = out and "Search failed" not in out and "No results" not in out
        if bypassed:
            print("✗ The search succeeded but NOTHING passed the proxy — primp bypasses it.")
            print("  This gate cannot observe the tiers. Use strace (IPs) or accept BLIND+ack.")
            return 2
        print("✗ No egress recorded and the search produced no results — inconclusive, not clean.")
        return 2

    unexpected = [h for h in hosts if h not in approved_hosts()]
    if unexpected:
        print(f"✗ {len(unexpected)} host(s) outside the approved tiers: {unexpected}")
        print("  Fix the tier list, or map the engine in ENGINE_HOSTS and record why.")
        return 1

    print(f"✓ Every destination was inside the approved tiers ({len(hosts)} host(s)).")
    print(json.dumps({"hosts": hosts, "query": query}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
