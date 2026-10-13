#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Generate unsloth_search/upstream_verbatim.py from upstream tools.py by AST extraction.

The verbatim surface is machine-generated, so it cannot drift by transcription. Never hand-edit the
output; edit this script's SYMBOLS list and re-run.

    scripts/build_port.py            # regenerate
    scripts/build_port.py --check    # fail if the generated file is stale (no write)
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import os
import re
import subprocess
import symtable
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_UPSTREAM = os.path.dirname(ROOT)
UPSTREAM_REL = os.path.join("studio", "backend", "core", "inference", "tools.py")
OUT_REL = os.path.join("unsloth_search", "upstream_verbatim.py")

# Extracted in upstream line order: module-level constants depend on earlier ones
# (_BINARY_MAGIC <- _PDF_MAGIC, _HTML_LEADING_RE <- _HTML_LEADING_TAGS).
SYMBOLS = (
    # tiers + model-facing copy
    "_EXEC_TIMEOUT",
    "_POLICY_OVERFETCH",
    "_SEARCH_ENGINE_TIERS",
    "EMPTY_SEARCH_RESULTS",
    "_DDGS_EMPTY_SWEEP",
    "_DDGS_RESET_MARKERS",
    "_DDGS_HTTP1_RETRY_LOCK",
    "_resolve_engine_tiers",
    "_search_failure_message",
    # tool schema + argument canonicalisation
    "WEB_SEARCH_TOOL",
    "_WEB_SEARCH_QUERY_ALIASES",
    "_WEB_SEARCH_URL_ALIASES",
    "_first_nonempty_arg",
    "_resolve_web_search_args",
    "canonicalize_web_search_arguments",
    # fetch limits and tables
    "_MAX_PAGE_CHARS",
    "_MIN_PAGE_CHARS",
    "_PAGE_CONTEXT_SHARE",
    "_MAX_FETCH_BYTES",
    "_MAX_HTML_FETCH_BYTES",
    "_IRI_PATH_SAFE",
    "_IRI_QUERY_SAFE",
    "_USER_AGENTS",
    "_tls_ctx",
    "_PINNED_DIAL_TIMEOUT",
    "_PINNED_PROBE_BUDGET",
    "_BINARY_CHAR_RE",
    "_MIN_BINARY_CHARS",
    "_BINARY_CHAR_DIVISOR",
    "_PDF_MAGIC",
    "_BINARY_MAGIC",
    "_BINARY_APPLICATION_SUBTYPES",
    "_UNICODE_BOM_CODECS",
    "_HTML_LEADING_TAGS",
    "_HTML_LEADING_RE",
    "_HTML_DOCUMENT_RE",
    "_HTMLBodyLocator",
    "_GITHUB_NON_OWNER_SEGMENTS",
    "_GITHUB_NAME_RE",
    "_DOTTED_HOST_RE",
    "_PORT_RE",
    # fetch helpers
    "_looks_like_html",
    "_looks_like_html_document",
    "_is_text_candidate_content_type",
    "_magic_head",
    "_has_binary_magic",
    "_looks_binary",
    "_normalize_url_scheme",
    "_pinned_netloc",
    "_github_repo_readme_api_url",
    "_fetch_budget_exceeded",
    "_fetch_hop_timeout",
    "_validate_and_resolve_host",
    "_resolve_with_budget",
    # Content-Encoding: servers send gzip/deflate unasked, so the capped read must undo it (#13532).
    "_DECODED_CODINGS",
    "_ContentDecoder",
    "_read_capped_body",
    "_NoRedirect",
    "_pinned_create_connection",
    "_PinnedHTTPSConnection",
    "_SNIHTTPSHandler",
    "_PinnedHTTPHandler",
    "_explicit_proxy_applies",
    "_redirect_hop",
    # ddgs hardening: registry parsers and the HTTP/1.1 replay for connection resets (#12638)
    "_class_token",
    "_YAHOO_SECTION_TITLES",
    "_YAHOO_SECTION_SNIPPET",
    "_install_yahoo_layout_parser",
    "_is_connection_reset",
    "_ddgs_http1_replay",
    "_install_ddgs_http1_retry",
    "_wrap_ddgs_client",
    "_usable_search_results",
)

HEADER = '''# SPDX-License-Identifier: AGPL-3.0-only
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
import functools
import http.client
import logging
import os
import queue
import random
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

# Upstream binds `logger = get_logger(__name__)` from studio's own logging module, which is not part of
# the ported surface. stdlib stand-in: no handler configured, so the hardening layers stay silent.
logger = logging.getLogger(__name__)

'''


STUB = os.path.join(ROOT, ".align", "upstream-stub")
# The only repository whose code may become "upstream". Any tools.py elsewhere is just a file.
UPSTREAM_ORIGIN_RE = re.compile(r"^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)unslothai/unsloth(\.git)?/?$")


def git(*args: str, cwd: str | None = None) -> str:
    """argv only, never a shell: paths and refs are data, not command text."""
    got = subprocess.run(["git", *args], cwd = cwd, capture_output = True, text = True)
    return got.stdout.strip() if got.returncode == 0 else ""


def origin_problem(path: str) -> str | None:
    """None when *path* is the root of a clone of unslothai/unsloth, else why it is not."""
    top = git("-C", path, "rev-parse", "--show-toplevel")
    if not top:
        return "not a git checkout"
    if os.path.realpath(top) != os.path.realpath(path):
        return f"inside another repository ({top})"
    url = git("-C", path, "remote", "get-url", "origin")
    if not UPSTREAM_ORIGIN_RE.match(url):
        return f"origin is {url or '(none)'}, not github.com/unslothai/unsloth"
    return None


def candidates(explicit: str | None):
    """(path, trusted_without_git). Only an explicit path or the anchored stub may be a non-git
    snapshot; every implicit location must prove it is an unslothai/unsloth clone."""
    if explicit:
        yield explicit, True
    yield os.environ.get("UNSLOTH_UPSTREAM"), False
    yield DEFAULT_UPSTREAM, False
    yield os.path.join(ROOT, ".align", "upstream"), False
    # Snapshot of the anchored ref, so the fidelity gate still runs with no clone; its hashes are
    # pinned in the manifest (tests/test_fidelity.py), so it cannot drift silently.
    yield STUB, True


def find_root(explicit: str | None = None, quiet: bool = False) -> str | None:
    for cand, snapshot_ok in candidates(explicit):
        if not cand or not os.path.isfile(os.path.join(cand, UPSTREAM_REL)):
            continue
        problem = origin_problem(cand)
        if problem is None or (snapshot_ok and problem == "not a git checkout"):
            return cand
        if not quiet:
            print(f"skipping {cand}: {problem}", file = sys.stderr)
    return None


def locate(upstream: str | None) -> str:
    root = find_root(upstream)
    if root is None:
        raise SystemExit(f"cannot find {UPSTREAM_REL} in a verified unslothai/unsloth clone; pass a path")
    return os.path.join(root, UPSTREAM_REL)


def closure_gaps(path: str) -> list[str]:
    """Globals the generated file uses but never binds. Importing cannot catch these: they raise
    NameError only at the call, often inside an except handler that then masks the real error."""
    table = symtable.symtable(open(path, encoding = "utf-8").read(), path, "exec")
    bound = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
    need: set[str] = set()

    def walk(t):
        for s in t.get_symbols():
            name = s.get_name()
            if s.is_referenced() and s.is_global() and name not in bound and not hasattr(builtins, name):
                need.add(name)
        for child in t.get_children():
            walk(child)

    walk(table)
    return sorted(need)


def extract(path: str) -> tuple[str, list[str]]:
    lines = open(path, encoding = "utf-8").read().splitlines()
    tree = ast.parse("\n".join(lines))
    found: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for n in names:
            found[n] = (node.lineno, node.end_lineno)

    missing = [s for s in SYMBOLS if s not in found]
    if missing:
        print(f"WARNING upstream has no: {', '.join(missing)}", file = sys.stderr)

    picked = sorted((s for s in SYMBOLS if s in found), key=lambda s: found[s][0])
    blocks = []
    for sym in picked:
        start, end = found[sym]
        blocks.append("\n".join(lines[start - 1 : end]))
    return HEADER + "\n\n" + "\n\n\n".join(blocks) + "\n", picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("upstream", nargs = "?")
    ap.add_argument("--check", action = "store_true", help = "fail if the generated file is stale")
    args = ap.parse_args()

    src = locate(args.upstream)
    body, picked = extract(src)

    out = os.path.join(ROOT, OUT_REL)
    if args.check:
        current = open(out, encoding = "utf-8").read() if os.path.isfile(out) else ""
        same = hashlib.sha256(current.encode()).hexdigest() == hashlib.sha256(body.encode()).hexdigest()
        # Name the source: a snapshot stub must never read as a live-upstream verdict.
        origin = "snapshot" if os.path.realpath(src).startswith(os.path.realpath(STUB)) else "live clone"
        print(("generated file is up to date" if same else "STALE: re-run scripts/build_port.py") + f" ({len(picked)} symbols, {origin}: {src})")
        return 0 if same else 1

    with open(out, "w", encoding = "utf-8") as fh:
        fh.write(body)
    gaps = closure_gaps(out)
    if gaps:
        print(f"UNBOUND in generated file (add to SYMBOLS or HEADER): {', '.join(gaps)}", file = sys.stderr)
        return 1
    print(f"wrote {out}")
    print(f"  {len(picked)} symbols extracted from {src}")
    print(f"  {body.count(chr(10)) + 1} lines, sha256 {hashlib.sha256(body.encode()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
