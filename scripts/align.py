#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Check this MCP against upstream unsloth/main.

    scripts/align.py              # fetch origin/main, report
    scripts/align.py --sync       # the whole ritual, atomically, against one frozen sha
    scripts/align.py --offline    # no network
    scripts/align.py --diff       # show upstream bodies for anything flagged
    scripts/align.py --bump       # anchor to current origin/main (refused while work is owed)

Four surfaces, four tests:
  generated  upstream_verbatim.py is AST-extracted from tools.py -> re-extract and compare, symbol by symbol
  vendored   two whole files copied byte-exact              -> sha256 both ways
  adapted    functions that needed local judgement        -> only upstream churn is reported
  safety     risky features new vs the accepted port      -> scripts/safety_diff.py, acked by a human
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
import safety_diff  # noqa: E402
from build_port import git  # noqa: E402

MANIFEST = os.path.join(ROOT, ".align", "manifest.json")
GOOD, WARN, DRIFT, BAD = "SYNCED", "REVIEW", "DRIFTED", "BROKEN"
UPSTREAM_URL = "https://github.com/unslothai/unsloth"

# Local judgement was applied here, so equality is not the test; upstream churn is.
ADAPTED = {
    "_web_search": ("unsloth_search/search.py", "web_search"),
    "_fetch_url_raw": ("unsloth_search/fetch.py", "fetch_url_raw"),
    "_fetch_page_text": ("unsloth_search/fetch.py", "fetch_page_text"),
    "_truncate_page_text": ("unsloth_search/fetch.py", "_truncate_page_text"),
    "_page_char_budget": ("unsloth_search/fetch.py", "page_char_budget"),
    "_result_char_budget": ("unsloth_search/fetch.py", "page_char_budget"),
}

VENDORED = safety_diff.VENDORED

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


# Reading the clone's working tree is a trap: `git fetch` moves origin/main but checks nothing out, so a
# port checked against a stale HEAD reads as aligned forever. Always read the ref, not the checkout.
_REF_CACHE: dict[str, str] = {}


def upstream_file(up: str, rel: str, ref: str) -> str:
    """Path to *rel* as it exists at *ref* (a temp file), falling back to the working tree."""
    key = f"{ref}:{rel}"
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    out = os.path.join(ROOT, ".align", "refcache", ref.replace("/", "_")[:40], os.path.basename(rel))
    os.makedirs(os.path.dirname(out), exist_ok = True)
    got = subprocess.run(["git", "-C", up, "show", f"{ref}:{rel}"], capture_output = True, text = True)
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
    # Every implicit location must be a verified unslothai/unsloth clone (build_port.find_root);
    # the stub is never an align source: it is the thing align refreshes.
    root = build_port.find_root(explicit)
    if root and os.path.realpath(root) == os.path.realpath(build_port.STUB) and not explicit:
        return None
    return root


def clone_upstream(branch: str) -> str | None:
    dest = os.path.join(ROOT, ".align", "upstream")
    if not os.path.isdir(dest):
        print(f"shallow-cloning unsloth/{branch} -> {dest} (one-off)")
        if subprocess.call(["git", "clone", "--depth", "1", "--branch", branch, UPSTREAM_URL, dest]):
            return None
    problem = build_port.origin_problem(dest)
    if problem:
        print(f"refusing {dest}: {problem}", file = sys.stderr)
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


def report(up: str, ref: str, manifest: dict, show_diff: bool) -> tuple[list[tuple], list[str], dict]:
    """(rows, new, upstream symbols) for upstream at *ref*. Pure: writes nothing but the refcache."""
    rows: list[tuple] = []
    upath = upstream_file(up, build_port.UPSTREAM_REL, ref)
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
        if show_diff:
            diff(sym, body_of(genpath, sym), body_of(upath, sym))

    # 2. vendored files: byte-exact against the ref, not the checkout. A mismatch is always fixed by
    #    copying the ref's file; which side moved only decides the label.
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
        recorded = manifest.get("vendored", {}).get(base, {}).get("upstream_sha256")
        upstream_moved = sha(upf) != recorded
        rows.append((DRIFT if upstream_moved else BAD, "vendored", base, "upstream changed — copy it" if upstream_moved else "local edited — copy the ref back"))
        if show_diff:
            diff(base, open(locf, encoding = "utf-8").read(), open(upf, encoding = "utf-8").read())

    # 3. adapted: only upstream churn is reported; equality is not expected here.
    rec = manifest.get("adapted", {})
    for sym, (local, local_sym) in ADAPTED.items():
        if sym not in usyms:
            rows.append((BAD, "adapted", sym, "REMOVED upstream — drop the local one"))
            continue
        if fp(usyms[sym]) != rec.get(sym):
            rows.append((WARN, "adapted", sym, "upstream changed — re-justify the adaptation"))
            if show_diff:
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
    return rows, new, usyms


def print_rows(rows: list[tuple], new: list[str], safety: int, verbose: bool) -> dict:
    w = max((len(r[2]) for r in rows), default = 20)
    print()
    for st, mode, sym, note in rows:
        if st == GOOD and not verbose:
            continue
        print(f"  {st:<7} {mode:<12} {sym:<{w}} {note}")
    for n in new:
        print(f"  {'NEW':<7} {'?':<12} {n:<{w}} decide: port, or record the exclusion")
    # Counted over every row, never over the displayed subset: a quiet report must still add up.
    counts = {s: sum(1 for r in rows if r[0] == s) for s in (GOOD, WARN, DRIFT, BAD)}
    print(f"\n{len(rows)} tracked · {counts[GOOD]} synced · {counts[WARN]} review · {counts[DRIFT]} drifted · {counts[BAD]} broken · {len(new)} new · {safety} unacked safety")
    return counts


def stub_hashes(up: str, ref: str) -> dict:
    """sha256 of every stub file as it must be at *ref*; tests/test_fidelity.py holds the stub to it."""
    inference = os.path.dirname(build_port.UPSTREAM_REL)
    rels = [build_port.UPSTREAM_REL] + [os.path.join(inference, b) for b in VENDORED]
    return {os.path.basename(r): sha(upstream_file(up, r, ref)) for r in rels}


def write_manifest(manifest: dict, branch: str, commit: str, up: str, ref: str, usyms: dict) -> None:
    manifest["upstream_branch"] = branch
    manifest["upstream_commit"] = commit
    manifest["adapted"] = {s: fp(usyms[s]) for s in ADAPTED if s in usyms}
    inference = os.path.dirname(build_port.UPSTREAM_REL)
    manifest["vendored"] = {b: {"upstream_sha256": sha(upstream_file(up, os.path.join(inference, b), ref))} for b in VENDORED}
    manifest["generated_symbols"] = len(build_port.SYMBOLS)
    manifest["stub_sha256"] = stub_hashes(up, ref)
    with open(MANIFEST, "w", encoding = "utf-8") as fh:
        json.dump(manifest, fh, indent = 2)
        fh.write("\n")


def run(step: str, argv: list[str]) -> bool:
    print(f"\n==> {step}")
    ok = subprocess.call(argv, cwd = ROOT) == 0
    if not ok:
        print(f"✗ {step} failed — nothing was anchored. Fix it and re-run --sync.")
    return ok


def sync(up: str, branch: str, manifest: dict, args) -> int:
    """Steps 2-9 of the ritual against ONE sha, refusing at the first mismatch. Only the last step
    writes the manifest, so a failed sync leaves the anchor where it was and the diff on disk."""
    if args.offline:
        print("--sync needs the network: it fetches, then freezes origin/main to a sha.", file = sys.stderr)
        return 2
    ref = git("-C", up, "rev-parse", f"origin/{branch}")
    if not ref:
        print(f"cannot resolve origin/{branch} in {up}", file = sys.stderr)
        return 2
    old = manifest.get("upstream_commit", "")
    print(f"frozen   : {ref}  (origin/{branch} may move from here on; this run never re-reads it)")

    # Safety first, before a byte is written: findings must be acknowledged with a reason.
    if safety_diff.check(up, ref, ack = args.ack):
        print("\n✗ --sync stopped at the safety gate. Nothing was written except the ack file (with --ack).")
        return 1

    # build_port and the docs' manual steps read the TREE; keep it on the same sha as everything else.
    git("-C", up, "checkout", "-q", "--detach", ref)
    if git("-C", up, "rev-parse", "HEAD") != ref:
        print(f"✗ could not check out {ref[:12]} in {up}")
        return 1

    # Regenerate from the frozen ref (not the tree), then copy the vendored files and refresh the stub.
    tools = upstream_file(up, build_port.UPSTREAM_REL, ref)
    body, picked = build_port.extract(tools)
    if len(picked) != len(build_port.SYMBOLS):
        print(f"✗ extracted {len(picked)} of {len(build_port.SYMBOLS)} symbols — see the warning above")
        return 1
    out = os.path.join(ROOT, build_port.OUT_REL)
    with open(out, "w", encoding = "utf-8") as fh:
        fh.write(body)
    gaps = build_port.closure_gaps(out)
    if gaps:
        print(f"✗ generated file uses unbound names: {', '.join(gaps)} — add them to SYMBOLS or HEADER")
        return 1
    inference = os.path.dirname(build_port.UPSTREAM_REL)
    for base, local in VENDORED.items():
        with open(upstream_file(up, os.path.join(inference, base), ref), "rb") as src, open(os.path.join(ROOT, local), "wb") as dst:
            dst.write(src.read())
    for rel in [build_port.UPSTREAM_REL] + [os.path.join(inference, b) for b in VENDORED]:
        dest = os.path.join(build_port.STUB, rel)
        os.makedirs(os.path.dirname(dest), exist_ok = True)
        with open(upstream_file(up, rel, ref), "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
    print(f"\n==> regenerated {len(picked)} symbols, copied {len(VENDORED)} vendored files, refreshed the stub")

    rows, new, usyms = report(up, ref, manifest, show_diff = True)
    counts = print_rows(rows, new, 0, args.verbose)
    if counts[DRIFT] or counts[BAD]:
        print("\n✗ still drifted/broken after regeneration — a SYMBOLS or ADAPTED change is owed by hand.")
        return 1
    if counts[WARN] and not args.reviewed:
        print("\n✗ REVIEW rows: read the diffs above, re-justify or port into search.py/fetch.py, then")
        print("  re-run with --sync --reviewed. That flag is your signature that you read them.")
        return 1
    if new:
        print("\n✗ NEW upstream symbols: port them or record them in OUT_OF_SCOPE/ANNOTATED first.")
        return 1

    python = sys.executable
    # The stub-pin test checks the stub against the manifest, which is written only below.
    pin = "tests/test_fidelity.py::test_stub_is_pinned_to_the_anchor"
    if not run("tests", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", "--deselect", pin]):
        return 1
    if not args.no_egress:
        if not run("egress gate: search tiers", [python, "scripts/check_egress_mcp.py"]):
            return 1
        if not run("egress gate: archives", [python, "scripts/check_egress_mcp.py", "--wayback"]):
            return 1

    write_manifest(manifest, branch, ref, up, ref, usyms)
    # Re-test: the stub/manifest pin test only now sees the new hashes.
    if not run("tests against the new anchor", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]):
        return 1

    head = git("-C", up, "rev-parse", "HEAD")
    written = json.load(open(MANIFEST, encoding = "utf-8"))["upstream_commit"]
    print(f"\ncheckout : {head}\nref      : {ref}\nmanifest : {written}")
    if not (head == ref == written):
        print("✗ the three disagree — do not commit; re-run --sync.")
        return 1
    print(f"\nALIGNED.  {old[:12] or '(never)'} -> {ref[:12]}" + ("  (egress NOT checked)" if args.no_egress else ""))
    print("Review `git diff`, then commit the port, the stub-pinned manifest and any safety acks together.")
    return 0


def main() -> int:
    # Step headers must land between the subprocess outputs they introduce, not after them.
    sys.stdout.reconfigure(line_buffering = True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action = "store_true")
    ap.add_argument("--diff", action = "store_true")
    ap.add_argument("--bump", action = "store_true")
    ap.add_argument("--sync", action = "store_true", help = "steps 2-9 atomically against one frozen sha")
    ap.add_argument("--ack", action = "store_true", help = "with --sync: append unacked safety findings for review")
    ap.add_argument("--reviewed", action = "store_true", help = "with --sync: I read every REVIEW diff")
    ap.add_argument("--no-egress", action = "store_true", help = "with --sync: skip the live egress gates (reported)")
    ap.add_argument("-v", "--verbose", action = "store_true")
    ap.add_argument("--upstream")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST, encoding = "utf-8")) if os.path.isfile(MANIFEST) else {}
    branch = manifest.get("upstream_branch", "main")
    up = find_upstream(args.upstream) or (None if args.offline else clone_upstream(branch))
    if up is None:
        print("no verified upstream clone; pass --upstream <path>", file = sys.stderr)
        return 2
    is_git = build_port.origin_problem(up) is None

    if not args.offline and is_git:
        git("-C", up, "fetch", "origin", branch, "-q")
    origin = git("-C", up, "rev-parse", f"origin/{branch}") or git("-C", up, "rev-parse", "HEAD")
    last = manifest.get("upstream_commit", "")

    print(f"upstream : {up}")
    print(f"anchor   : {last[:12] or '(never)'}")
    print(f"origin/{branch}: {origin[:12] or '(not a git clone)'}")

    if args.sync:
        if not is_git:
            print("--sync needs a verified git clone, not a snapshot.", file = sys.stderr)
            return 2
        return sync(up, branch, manifest, args)

    ref = origin or f"origin/{branch}"
    head = git("-C", up, "rev-parse", "HEAD")
    if is_git and head and head != origin:
        print(f"reading {ref[:12]}, not the working tree (HEAD {head[:12]})")
    rows, new, usyms = report(up, ref, manifest, args.diff)
    # Safety owed counts like drift: ALIGNED. is impossible while a risky change is unacknowledged.
    pending = safety_diff.check(up, ref if is_git else "", quiet = not args.diff) if is_git else []
    counts = print_rows(rows, new, len(pending), args.verbose)

    owed = counts[DRIFT] + counts[BAD] + len(pending)
    if owed:
        print("\nDo: scripts/align.py --sync   (it regenerates, copies, refreshes, tests, gates, anchors)")
        if pending:
            print("    safety findings first: scripts/align.py --diff to read them, then --sync --ack")
        if args.bump:
            print("\n--bump refused while work is owed.")
        return 1

    print("\nALIGNED." if last == origin else "\nAligned, but anchored to an older commit. Run: scripts/align.py --sync")
    if args.bump:
        # Never from a snapshot: there is no sha to anchor to, and an empty one erases the anchor.
        if not is_git or not origin:
            print("--bump refused: not a git clone, so there is no commit to anchor to.")
            return 1
        if head != origin:
            print(f"--bump refused: checkout HEAD {head[:12]} is not origin/{branch} {origin[:12]}.")
            print("  build_port reads the TREE: anchoring now would stamp content from another commit. Use --sync.")
            return 1
        write_manifest(manifest, branch, origin, up, origin, usyms)
        print(f"anchored to {origin} — refresh the stub (or use --sync), then re-run the tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
