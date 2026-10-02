# SPDX-License-Identifier: AGPL-3.0-only
"""The dependency set is the supply-chain boundary. Two things must stay true: nothing is
installed that the reviewed lock does not name, and no telemetry exporter ever appears."""

import importlib.metadata as md
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "requirements.lock")

DENY = re.compile(r"opentelemetry-.*exporter|opentelemetry-exporter|otlp", re.I)


def _locked():
    """name==version for every requirement, continuations joined."""
    text = open(LOCK, encoding="utf-8").read()
    text = "\n".join(line.split("#")[0] for line in text.split("\n"))
    text = text.replace("\\\n", " ")
    out = set()
    for line in text.split("\n"):
        tok = line.strip().split()
        if tok and "==" in tok[0]:
            out.add(tok[0].lower())
    return out


def test_lock_exists_and_is_hash_pinned():
    assert os.path.isfile(LOCK), "requirements.lock is missing — installs would be unpinned"
    text = open(LOCK, encoding="utf-8").read()
    assert "--hash=sha256:" in text, "lock carries no hashes"
    # Every pinned requirement must carry at least one hash, or --require-hashes won't gate it.
    joined = text.replace("\\\n", " ")
    for line in joined.split("\n"):
        spec = line.strip()
        if spec and "==" in spec.split()[0]:
            assert "--hash=sha256:" in spec, f"unhashed pin in lock: {spec[:60]}"


def test_no_telemetry_exporter_in_the_lock():
    hits = [p for p in _locked() if DENY.search(p)]
    assert not hits, f"telemetry exporter in dependency lock: {hits}"


def test_no_telemetry_exporter_installed():
    installed = [d.metadata["Name"] for d in md.distributions()]
    hits = [n for n in installed if DENY.search(n)]
    assert not hits, f"telemetry exporter installed: {hits}"


def test_every_installed_dist_is_named_by_the_lock():
    """An install that carries something the reviewed lock never named is the finding."""
    if not os.path.isdir(os.path.join(ROOT, ".venv")):
        return
    locked = _locked()
    skip = {"pip", "setuptools", "wheel", "mcp-websearch"}
    installed = {
        f"{d.metadata['Name']}=={d.version}".lower()
        for d in md.distributions()
        if d.metadata["Name"] and d.metadata["Name"].lower() not in skip
    }
    extra = sorted(installed - locked)
    assert not extra, f"installed but not pinned in lock: {extra}"
