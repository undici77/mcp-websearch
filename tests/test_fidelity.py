# SPDX-License-Identifier: AGPL-3.0-only
"""Fidelity gate. Offline: the full upstream check is scripts/align.py."""

import hashlib
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
UPSTREAM_REL = os.path.join("studio", "backend", "core", "inference", "tools.py")


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_generated_file_is_not_stale():
    """The verbatim surface must be exactly what the generator produces from upstream."""
    rc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "build_port.py"), "--check"],
        capture_output = True, text = True, cwd = ROOT,
    )
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_generated_file_is_not_hand_edited():
    """A hand-edit to the generated file is the failure mode this project exists to prevent."""
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "build_port.py"), "--check"],
        capture_output = True, text = True, cwd = ROOT,
    )
    assert "STALE" not in out.stdout, "upstream_verbatim.py was hand-edited; run build_port.py"


def test_vendored_files_are_byte_exact():
    rel = "studio/backend/core/inference"
    cands = (
        os.environ.get("UNSLOTH_UPSTREAM"),
        os.path.dirname(ROOT),
        os.path.join(ROOT, ".align", "upstream-stub"),
    )
    up = next(
        (c for c in cands if c and os.path.isfile(os.path.join(c, rel, "web_access_policy.py"))),
        None,
    )
    if up is None:
        pytest.skip("no upstream clone or stub holding the vendored files")
    for base in ("web_access_policy.py", "_html_to_md.py"):
        u, l = os.path.join(up, rel, base), os.path.join(ROOT, "unsloth_search", base)
        if not os.path.isfile(u):
            pytest.skip(f"no upstream clone at {up}")
        assert sha(u) == sha(l), f"{base} diverged from upstream"


def test_tool_schema_is_the_upstream_shape():
    from unsloth_search import WEB_SEARCH_TOOL

    fn = WEB_SEARCH_TOOL["function"]
    assert fn["name"] == "web_search"
    assert set(fn["parameters"]["properties"]) == {"query", "url"}
    assert fn["parameters"]["required"] == []
    assert "url parameter" in fn["description"]


def test_result_format_and_nudge_survive():
    from unsloth_search import EMPTY_SEARCH_RESULTS

    assert EMPTY_SEARCH_RESULTS == ("No results found.", "No results found within the website access limits.")


def test_failure_messages_are_verbatim():
    from ddgs.exceptions import DDGSException, TimeoutException

    from unsloth_search.upstream_verbatim import EMPTY_SEARCH_RESULTS, _search_failure_message

    assert _search_failure_message(TimeoutException("x"), 7) == (
        "Search failed: the search engines did not respond within 7s."
    )
    assert _search_failure_message(DDGSException("No results found"), 7) == EMPTY_SEARCH_RESULTS[0]
    assert _search_failure_message(RuntimeError("boom"), 7) == "Search failed: boom"


def test_yandex_and_bing_are_never_reachable():
    from ddgs.engines import ENGINES

    from unsloth_search.upstream_verbatim import _SEARCH_ENGINE_TIERS, _resolve_engine_tiers

    named = {n for t in _resolve_engine_tiers(ENGINES["text"]) for n in t.split(",")}
    assert "yandex" not in named and "bing" not in named
    flat = {n for tier in _SEARCH_ENGINE_TIERS for n in tier}
    assert "auto" not in flat, "backend=auto is the Yandex fan-out this exists to prevent"


def test_server_exposes_expected_tools():
    """web_search mirrors upstream; wayback is the one fork-owned addition (ALIGNMENT.md)."""
    import asyncio

    import server

    tools = asyncio.run(server.app.list_tools())
    assert [t.name for t in tools] == ["web_search", "wayback"]
    schema = getattr(tools[0], "input_schema", None) or tools[0].inputSchema
    web = schema["properties"]
    assert set(web) == {"query", "url", "max_results", "timeout", "allowed_domains", "blocked_domains"}
    for tool in tools:
        hints = tool.annotations.model_dump(by_alias = True)
        assert hints["readOnlyHint"] and hints["openWorldHint"], tool.name


def test_stub_is_pinned_to_the_anchor():
    """The stub stands in for the anchored ref offline, so it must BE that ref: a stale stub fails the
    correct fix, and a tampered one would make every fidelity test vouch for code nobody reviewed."""
    import hashlib
    import json

    manifest = json.load(open(os.path.join(ROOT, ".align", "manifest.json"), encoding = "utf-8"))
    pinned = manifest.get("stub_sha256")
    assert pinned, "manifest has no stub_sha256 — run scripts/align.py --sync"
    stub = os.path.join(ROOT, ".align", "upstream-stub", os.path.dirname(UPSTREAM_REL))
    if not os.path.isdir(stub):
        pytest.skip("no stub (fresh clone); align.py --sync recreates it")
    for name, digest in pinned.items():
        with open(os.path.join(stub, name), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == digest, f"stub {name} is not the anchored ref"


def test_generated_file_binds_every_name_it_uses():
    import build_port

    assert build_port.closure_gaps(os.path.join(ROOT, build_port.OUT_REL)) == []
