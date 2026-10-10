#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Check this MCP against upstream unsloth/main.

    scripts/align.py              # fetch origin/main, report
    scripts/align.py --offline    # no network
    scripts/align.py --diff       # show upstream bodies for anything flagged
    scripts/align.py --bump       # anchor to current origin/main (refused while work is owed)

Three surfaces, three tests:
  generated  upstream_verbatim.py is AST-extracted from tools.py -> re-extract and compare, symbol by symbol
  vendored   two whole files copied byte-exact              -> sha256 both ways
  adapted    5 functions that needed local judgement          -> only upstream churn is reported
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_port  # noqa: E402

MANIFEST = os.path.join(ROOT, ".align", "manifest.json")
GOOD, WARN, DRIFT, BAD = "SYNCED", "REVIEW", "DRIFTED", "BROKEN"

# Local judgement was applied here, so equality is not the test; upstream churn is.
ADAPTED = {
    "_web_search": ("unsloth_search/search.py", "web_search"),
    "_fetch_url_raw": ("unsloth_search/fetch.py", "fetch_url_raw"),
    "_fetch_page_text": ("unsloth_search/fetch.py", "fetch_page_text"),
    "_truncate_page_text": ("unsloth_search/fetch.py", "_truncate_page_text"),
    "_page_char_budget": ("unsloth_search/fetch.py", "page_char_budget"),
    "_result_char_budget": ("unsloth_search/fetch.py", "page_char_budget"),
}

VENDORED = {
    "web_access_policy.py": "unsloth_search/web_access_policy.py",
    "_html_to_md.py": "unsloth_search/_html_to_md.py",
}

# Upstream features deliberately not ported.
OUT_OF_SCOPE = (
    "_image_search",
    "_web_search_images_suffix",
    "web_search_tool_with_images",
    "_clean_image_queries",
    "_extract_pdf_text",
    "_meta_refresh_target",
    "_dense_char_limit",
)

# Upstream's approval gate is expressed as MCP tool annotations (readOnlyHint/openWorldHint) and left
# to the client, not reimplemented here.
ANNOTATED = (
    "_web_search_fetches_url",
    "_ALWAYS_SAFE_TOOLS",
)


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell = True, cwd = ROOT, capture_output = True, text = True).stdout.strip()


# Reading the clone's working tree is a trap: `git fetch` moves origin/main but checks nothing out, so a
# port checked against a stale HEAD reads as aligned forever. Always read the ref, not the checkout.
_REF_CACHE: dict[str, str] = {}


def upstream_file(up: str, rel: str, ref: str) -> str:
    """Path to *rel* as it exists at *ref* (a temp file), falling back to the working tree."""
    key = f"{ref}:{rel}"
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    out = os.path.join(ROOT, ".align", "refcache", ref[:12], os.path.basename(rel))
    os.makedirs(os.path.dirname(out), exist_ok = True)
    got = subprocess.run(
        f"git -C {up} show {ref}:{rel}", shell = True, capture_output = True, text = True
    )
    if got.returncode == 0:
        with open(out, "w", encoding = "utf-8") as fh:
            fh.write(got.stdout)
        path = out
    else:
        path = os.path.join(up, rel)
    _REF_CACHE[key] = path
    return path


def sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def find_upstream(explicit: str | None) -> str | None:
    for c in (explicit, os.environ.get("UNSLOTH_UPSTREAM"), os.path.dirname(ROOT), ROOT, os.path.join(ROOT, ".align", "upstream")):
        if c and os.path.isfile(os.path.join(c, build_port.UPSTREAM_REL)):
            return c
    return None


def clone_upstream(branch: str) -> str | None:
    dest = os.path.join(ROOT, ".align", "upstream")
    if not os.path.isdir(dest):
        print(f"shallow-cloning unsloth/{branch} -> {dest} (one-off)")
        if subprocess.call(f"git clone --depth 1 --branch {branch} https://github.com/unslothai/unsloth {dest}", shell = True):
            return None
    return dest


def strip_doc(node):
    for sub in ast.walk(node):
        body = getattr(sub, "body", None)
        if not body or not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        f = body[0]
        if isinstance(f, ast.Expr) and isinstance(f.value, ast.Constant) and isinstance(f.value.value, str):
            sub.body = body[1:] or [ast.Pass()]
    return node


def fp(node) -> str:
    return hashlib.sha256(ast.dump(strip_doc(node), include_attributes = False).encode()).hexdigest()[:16]


def symbols(path: str) -> dict:
    try:
        tree = ast.parse(open(path, encoding = "utf-8").read())
    except (OSError, SyntaxError):
        return {}
    out: dict = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
    return out


def body_of(path: str, name: str) -> str:
    nodes = symbols(path)
    if name not in nodes:
        return ""
    lines = open(path, encoding = "utf-8").read().splitlines()
    n = nodes[name]
    return "\n".join(lines[n.lineno - 1 : n.end_lineno])


def diff(title: str, a: str, b: str) -> None:
    print(f"\n--- {title} (local -> upstream) ---")
    print("\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), "local", "upstream", lineterm = "")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action = "store_true")
    ap.add_argument("--diff", action = "store_true")
    ap.add_argument("--bump", action = "store_true")
    ap.add_argument("-v", "--verbose", action = "store_true")
    ap.add_argument("--upstream")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST, encoding = "utf-8")) if os.path.isfile(MANIFEST) else {}
    branch = manifest.get("upstream_branch", "main")
    up = find_upstream(args.upstream) or (None if args.offline else clone_upstream(branch))
    if up is None:
        print("no upstream clone; pass --upstream <path>", file = sys.stderr)
        return 2

    if not args.offline:
        sh(f"git -C {up} fetch origin {branch} -q")
    origin = sh(f"git -C {up} rev-parse origin/{branch}") or sh(f"git -C {up} rev-parse HEAD")
    last = manifest.get("upstream_commit", "")

    print(f"upstream : {up}")
    print(f"anchor   : {last[:12] or '(never)'}")
    print(f"origin/{branch}: {origin[:12]}")

    rows: list[tuple] = []
    ref = f"origin/{branch}"
    upath = upstream_file(up, build_port.UPSTREAM_REL, ref)
    if os.path.realpath(upath) != os.path.realpath(os.path.join(up, build_port.UPSTREAM_REL)):
        print(f"reading {ref}, not the working tree (HEAD {sh(f'git -C {up} rev-parse HEAD')[:12]})")
    usyms = symbols(upath)
    genpath = os.path.join(ROOT, build_port.OUT_REL)
    gsyms = symbols(genpath)

    # 1. generated surface: re-extract from upstream, compare each symbol's AST.
    for sym in build_port.SYMBOLS:
        if sym not in usyms:
            rows.append((BAD, "generated", sym, "REMOVED upstream"))
            continue
        if sym not in gsyms:
            rows.append((BAD, "generated", sym, "not in generated file — run build_port.py"))
            continue
        if fp(usyms[sym]) == fp(gsyms[sym]):
            rows.append((GOOD, "generated", sym, "identical"))
            continue
        rows.append((DRIFT, "generated", sym, "upstream changed — rebuild"))
        if args.diff:
            diff(sym, body_of(genpath, sym), body_of(upath, sym))

    # 2. vendored files: byte-exact against the ref, not the checkout.
    for base, local in VENDORED.items():
        rel = os.path.join(os.path.dirname(build_port.UPSTREAM_REL), base)
        upf = upstream_file(up, rel, ref)
        locf = os.path.join(ROOT, local)
        if not os.path.isfile(locf):
            rows.append((BAD, "vendored", base, "local copy missing"))
            continue
        if sha(upf) == sha(locf):
            rows.append((GOOD, "vendored", base, "byte-exact"))
            continue
        up_changed = sha(upf) == manifest.get("vendored", {}).get(base, {}).get("upstream_sha256")
        rows.append((DRIFT if up_changed else BAD, "vendored", base, "upstream changed" if up_changed else "local edited, upstream unchanged"))
        if args.diff:
            diff(base, open(locf, encoding = "utf-8").read(), open(upf, encoding = "utf-8").read())

    # 3. adapted: only upstream churn is reported; equality is not expected here.
    rec = manifest.get("adapted", {})
    for sym, (local, local_sym) in ADAPTED.items():
        if sym not in usyms:
            rows.append((BAD, "adapted", sym, "REMOVED upstream — drop the local one"))
            continue
        now = fp(usyms[sym])
        if now != rec.get(sym):
            rows.append((WARN, "adapted", sym, "upstream changed — re-justify the adaptation"))
            if args.diff:
                diff(sym, body_of(os.path.join(ROOT, local), local_sym), body_of(upath, sym))
        else:
            rows.append((GOOD, "adapted", sym, "upstream untouched"))

    # 4. out-of-scope: shout only if upstream grew the feature.
    for sym in OUT_OF_SCOPE:
        if sym not in usyms:
            rows.append((WARN, "out-of-scope", sym, "gone upstream — is the exclusion still needed?"))
        else:
            rows.append((GOOD, "out-of-scope", sym, "still out of scope"))

    # 5. mirrored as MCP annotations, not code.
    for sym in ANNOTATED:
        if sym not in usyms:
            rows.append((WARN, "annotated", sym, "gone upstream — re-check the annotation still covers it"))
        else:
            rows.append((GOOD, "annotated", sym, "mirrored as readOnlyHint/openWorldHint"))

    # 6. unmapped: anything new upstream that looks in scope.
    known = set(build_port.SYMBOLS) | set(ADAPTED) | set(OUT_OF_SCOPE) | set(ANNOTATED)
    new = sorted(
        n for n in usyms
        if n not in known and any(k in n.lower() for k in ("web_search", "engine_tier", "fetch_page", "fetch_url", "search_engine"))
    )

    w = max((len(r[2]) for r in rows), default = 20)
    print()
    for st, mode, sym, note in rows:
        if st == GOOD and not args.verbose:
            continue
        print(f"  {st:<7} {mode:<12} {sym:<{w}} {note}")
    for n in new:
        print(f"  {'NEW':<7} {'?':<12} {n:<{w}} decide: port, or record the exclusion")

    # Counted over every row, never over the displayed subset: a quiet report must still add up.
    counts = {s: sum(1 for r in rows if r[0] == s) for s in (GOOD, WARN, DRIFT, BAD)}
    print(f"\n{len(rows)} tracked · {counts[GOOD]} synced · {counts[WARN]} review · {counts[DRIFT]} drifted · {counts[BAD]} broken · {len(new)} new")

    owed = counts[DRIFT] + counts[BAD]
    if owed:
        print("\nDo, in order:")
        if counts[DRIFT]:
            print("  1. scripts/build_port.py    # regenerate the verbatim surface")
            print("  2. apply the DRIFTED vendored/adapted entries by hand")
        print("  3. .venv/bin/pytest -q tests")
        print("  4. scripts/align.py --bump")
        if args.bump:
            print("\n--bump refused while work is owed.")
        return 1

    print("\nALIGNED." if last == origin else f"\nAligned, but anchored to an older commit. Run: scripts/align.py --bump")
    if args.bump:
        manifest["upstream_branch"] = branch
        manifest["upstream_commit"] = origin
        manifest["adapted"] = {s: fp(usyms[s]) for s in ADAPTED if s in usyms}
        manifest["vendored"] = {
            b: {"upstream_sha256": sha(upstream_file(up, os.path.join(os.path.dirname(build_port.UPSTREAM_REL), b), ref))}
            for b in VENDORED
        }
        manifest["generated_symbols"] = len(build_port.SYMBOLS)
        with open(MANIFEST, "w", encoding = "utf-8") as fh:
            json.dump(manifest, fh, indent = 2)
            fh.write("\n")
        print(f"anchored to {origin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
