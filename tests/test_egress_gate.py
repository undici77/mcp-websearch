# SPDX-License-Identifier: AGPL-3.0-only
"""Offline guards for the egress gate. The live run is scripts/check_egress_mcp.py; these
ensure the gate cannot silently fall out of step with the tier list it is supposed to police."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_egress_mcp import ENGINE_HOSTS, approved_hosts  # noqa: E402
from unsloth_search.upstream_verbatim import _SEARCH_ENGINE_TIERS  # noqa: E402


def _tiers():
    return {str(e).strip() for tier in _SEARCH_ENGINE_TIERS for e in tier}


def test_every_tier_engine_has_a_host_mapping():
    missing = _tiers() - set(ENGINE_HOSTS)
    assert not missing, f"tier engine(s) unmapped, the gate would miss them: {sorted(missing)}"


def test_gate_covers_exactly_the_tiers_plus_serpapi():
    expected = {ENGINE_HOSTS[e] for e in _tiers()} | {"serpapi.com"}
    assert approved_hosts() == expected


def test_yandex_is_never_approved():
    # It is in ddgs' live registry; the tier list exists to keep it unnamed. Same here.
    assert not any("yandex" in h for h in approved_hosts())
    assert "yandex" not in _tiers()


def test_url_mode_host_is_not_pre_approved():
    # url mode reaches a host the model names; nothing in the gate may pre-authorise that.
    assert "example.com" not in approved_hosts()
