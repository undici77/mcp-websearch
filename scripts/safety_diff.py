#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Safety gate for code entering from upstream. align.py proves the port EQUALS upstream; this
proves nothing risky arrived with it unseen.

    scripts/safety_diff.py               # new risky features vs the accepted port; exit 1 if unacked
    scripts/safety_diff.py --ack         # append unacked findings to .align/safety-ack.json (reason "")
    scripts/safety_diff.py --inventory   # every risky feature in the accepted port, for a full read

Accepted = the generated + vendored files as committed at HEAD here. Candidate = the same surface as
upstream has it at the ref under review (origin/main by default; align.py --sync passes its frozen
sha). Only features the candidate has and the accepted port lacks are findings, so a quiet sync
stays quiet and a malicious commit cannot hide inside an otherwise-unchanged symbol.

A finding passes only with a written reason in .align/safety-ack.json, which is committed: the diff
of that file IS the record of what a human looked at and approved.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_port  # noqa: E402

ACK_FILE = os.path.join(ROOT, ".align", "safety-ack.json")
VENDORED = {
    "web_access_policy.py": "unsloth_search/web_access_policy.py",
    "_html_to_md.py": "unsloth_search/_html_to_md.py",
}

# Dotted-name prefixes whose appearance is a decision, not a detail: code execution, process and
# filesystem control, raw sockets, deserialisation, native code, runtime patching, decoding.
RISKY = (
    "eval", "exec", "compile", "__import__", "importlib", "runpy", "subprocess", "pty",
    "os.system", "os.popen", "os.exec", "os.spawn", "os.fork", "os.kill", "os.remove", "os.unlink",
    "os.rmdir", "os.rename", "os.replace", "os.chmod", "os.chown", "os.symlink", "os.putenv",
    "shutil", "tempfile", "socket", "pickle", "marshal", "shelve", "dill", "ctypes", "cffi",
    "base64", "binascii", "codecs.decode", "sys.settrace", "sys.setprofile", "sys.modules",
    "sys.path", "builtins", "globals", "setattr", "delattr", "webbrowser", "smtplib", "ftplib",
    "telnetlib", "http.server", "socketserver", "multiprocessing", "signal", "atexit",
)
FS_WRITE_ATTRS = {"write_text", "write_bytes", "unlink", "rmdir", "mkdir", "makedirs", "touch", "chmod"}
URL_RE = re.compile(r"\b(?:https?|wss?|ftp)://((?:[a-z0-9-]+\.)+[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})", re.I)
# Encoded payloads come unbroken (newline-wrapped at most); prose and word lists have spaces.
BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_\-]{200,}$")


def dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _risky(name: str) -> bool:
    return any(name == p or name.startswith(p if p.endswith(".") else p + ".") or
               (p in ("os.exec", "os.spawn") and name.startswith(p)) for p in RISKY)


def _aliases(node) -> dict[str, str]:
    """Local name -> what it really is, so `from os import system` and `import os as o` cannot hide
    `os.system` behind a name the rules do not match."""
    out: dict[str, str] = {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for a in sub.names:
                if a.asname:
                    out[a.asname] = a.name
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            for a in sub.names:
                out[a.asname or a.name] = f"{sub.module}.{a.name}"
    return out


def features(node) -> set[str]:
    """Risky features of one symbol or module, as stable 'rule:detail' strings."""
    out: set[str] = set()
    aliases = _aliases(node)

    def resolve(n) -> str:
        name = dotted(n)
        head, _, rest = name.partition(".")
        if head in aliases:
            return aliases[head] + ("." + rest if rest else "")
        return name

    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            out.update(f"import:{a.name}" for a in sub.names)
        elif isinstance(sub, ast.ImportFrom):
            # Per imported name, not per module: `from os import system` must differ from `from os import path`.
            mod = sub.module or "."
            out.update(f"import:{mod}.{a.name}" for a in sub.names)
            out.update(f"ref:{mod}.{a.name}" for a in sub.names if _risky(f"{mod}.{a.name}"))
        elif isinstance(sub, ast.Call):
            name = resolve(sub.func)
            if _risky(name):
                out.add(f"call:{name}")
            if name in ("open", "io.open", "os.open", "os.fdopen") or name.endswith(".open"):
                mode = sub.args[1] if len(sub.args) > 1 else next((k.value for k in sub.keywords if k.arg == "mode"), None)
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    if set(mode.value) & set("wax+"):
                        out.add(f"fs-write:{name}({mode.value!r})")
                elif mode is not None or name == "os.open":
                    out.add(f"fs-write:{name}(dynamic mode)")
            if isinstance(sub.func, ast.Attribute) and sub.func.attr in FS_WRITE_ATTRS:
                out.add(f"fs-write:.{sub.func.attr}")
            if name in ("os.getenv", "os.environ.get", "os.environ.setdefault", "os.environ.pop"):
                key = sub.args[0].value if sub.args and isinstance(sub.args[0], ast.Constant) else "<dynamic>"
                out.add(f"env:{key}")
        elif isinstance(sub, ast.Subscript) and resolve(sub.value) == "os.environ":
            key = sub.slice.value if isinstance(sub.slice, ast.Constant) else "<dynamic>"
            out.add(f"env:{key}")
        elif isinstance(sub, ast.Attribute) and _risky(resolve(sub)) and not isinstance(getattr(sub, "ctx", None), ast.Store):
            out.add(f"ref:{resolve(sub)}")
        elif isinstance(sub, ast.Name) and sub.id in ("eval", "exec", "compile", "__import__", "globals", "setattr", "delattr", "__builtins__"):
            out.add(f"ref:{sub.id}")
        elif isinstance(sub, ast.Name) and sub.id in aliases and _risky(aliases[sub.id]):
            out.add(f"ref:{aliases[sub.id]}")
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, (str, bytes)):
            text = sub.value.decode("latin-1") if isinstance(sub.value, bytes) else sub.value
            out.update(f"host:{h.lower()}" for h in URL_RE.findall(text))
            if BLOB_RE.match(text.replace("\n", "").replace("\r", "")):
                out.add(f"blob:{len(text)}c:{hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:12]}")
    # A bare attribute reference is implied by the call on it; keep the report to one line each.
    calls = {f[5:] for f in out if f.startswith("call:")}
    return {f for f in out if not (f.startswith("ref:") and f[4:] in calls)}


def _parse(text: str):
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def units(generated: str, vendored: dict[str, str]) -> dict[str, set[str]]:
    """unit name -> features. Generated: one unit per top-level symbol. Vendored: one per file."""
    out: dict[str, set[str]] = {}
    tree = _parse(generated)
    if tree is None:
        out["upstream_verbatim.py"] = {"parse:SyntaxError"}
    else:
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for n in names:
                out[n] = features(node)
    for base, text in vendored.items():
        t = _parse(text)
        out[base] = features(t) if t is not None else {"parse:SyntaxError"}
    return out


def accepted_surface() -> tuple[str, dict[str, str]]:
    """The port as committed here (HEAD); the working file only when there is no git history."""
    def read(rel: str) -> str:
        text = build_port.git("show", f"HEAD:{rel}", cwd = ROOT)
        if not text:
            path = os.path.join(ROOT, rel)
            text = open(path, encoding = "utf-8").read() if os.path.isfile(path) else ""
        return text

    return read(build_port.OUT_REL.replace(os.sep, "/")), {b: read(l) for b, l in VENDORED.items()}


def candidate_surface(up: str, ref: str) -> tuple[str, dict[str, str]]:
    """Upstream at *ref*, run through the same extraction the port uses."""
    import tempfile

    def show(rel: str) -> str:
        rel = rel.replace(os.sep, "/")
        text = build_port.git("-C", up, "show", f"{ref}:{rel}") if ref else ""
        if not text:
            path = os.path.join(up, rel)
            text = open(path, encoding = "utf-8").read() if os.path.isfile(path) else ""
        return text

    src = show(build_port.UPSTREAM_REL)
    with tempfile.NamedTemporaryFile("w", suffix = ".py", delete = False, encoding = "utf-8") as fh:
        fh.write(src)
    try:
        body, _picked = build_port.extract(fh.name)
    finally:
        os.unlink(fh.name)
    inference = os.path.dirname(build_port.UPSTREAM_REL)
    return body, {b: show(os.path.join(inference, b)) for b in VENDORED}


def findings(accepted: dict[str, set[str]], candidate: dict[str, set[str]]) -> list[tuple[str, str]]:
    """(unit, feature) present in the candidate and absent from the accepted port."""
    return sorted((u, f) for u, feats in candidate.items() for f in feats - accepted.get(u, set()))


def load_acks() -> list[dict]:
    if not os.path.isfile(ACK_FILE):
        return []
    with open(ACK_FILE, encoding = "utf-8") as fh:
        return json.load(fh).get("acks", [])


def unacked(found: list[tuple[str, str]], acks: list[dict]) -> list[tuple[str, str]]:
    ok = {(a.get("unit"), a.get("finding")) for a in acks if str(a.get("reason", "")).strip()}
    return [f for f in found if f not in ok]


def append_acks(pending: list[tuple[str, str]], ref: str) -> None:
    acks = load_acks()
    have = {(a.get("unit"), a.get("finding")) for a in acks}
    acks += [{"unit": u, "finding": f, "ref": ref, "reason": ""} for u, f in pending if (u, f) not in have]
    with open(ACK_FILE, "w", encoding = "utf-8") as fh:
        json.dump({"acks": acks}, fh, indent = 2)
        fh.write("\n")


def check(up: str, ref: str, ack: bool = False, quiet: bool = False) -> list[tuple[str, str]]:
    """Unacked findings for *ref*; with ack, they are appended for a human to give reasons."""
    gen, ven = accepted_surface()
    cgen, cven = candidate_surface(up, ref)
    pending = unacked(findings(units(gen, ven), units(cgen, cven)), load_acks())
    if pending and ack:
        append_acks(pending, ref)
    if pending and not quiet:
        print(f"\nSAFETY  {len(pending)} new risky feature(s) upstream at {ref[:12]}, not yet acknowledged:")
        for unit, feat in pending:
            print(f"  {unit:<34} {feat}")
        if ack:
            print(f"\nappended to {os.path.relpath(ACK_FILE, ROOT)} — read each with align.py --diff, write a reason, commit.")
        else:
            print("  read each (align.py --diff), then: align.py --sync --ack and fill in every reason")
    return pending


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default = "origin/main")
    ap.add_argument("--upstream")
    ap.add_argument("--ack", action = "store_true")
    ap.add_argument("--inventory", action = "store_true")
    args = ap.parse_args()

    if args.inventory:
        gen, ven = accepted_surface()
        for unit, feats in sorted(units(gen, ven).items()):
            for f in sorted(feats):
                print(f"  {unit:<34} {f}")
        return 0

    up = build_port.find_root(args.upstream)
    if up is None:
        print("no verified upstream; pass --upstream <path>", file = sys.stderr)
        return 2
    ref = build_port.git("-C", up, "rev-parse", args.ref) or ""
    pending = check(up, ref, ack = args.ack)
    if not pending:
        print(f"safety: nothing new at {ref[:12] or '(working tree)'} beyond the accepted port.")
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
