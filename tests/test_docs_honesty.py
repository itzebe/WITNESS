"""Checks that the submission documents do not carry claims we know to be false.

The docs are shown to judges. A stale claim in them is worse than a stale claim
in code, because a judge reads it as our understanding of the platform. These
tests pin three corrections we made after reading Ring's own reference, so a
later edit cannot quietly reintroduce the old wording.
"""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _text(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_submission_does_not_claim_the_bee_track():
    """WITNESS has no Bee hardware or data, so it cannot enter the Bee track.

    The first draft named the track "Bee / Ring". The rules require live Bee
    data ("mentioning Bee without demonstrating a live data interaction does not
    count"), which this build cannot produce.
    """
    text = _text("SUBMISSION.md")
    assert "Bee / Ring" not in text
    assert "**Ring** (primary track)" in text


def test_friction_log_does_not_deny_the_ring_mcp_server_exists():
    """Ring documents an MCP server at knowledge.appstore-mcp.ring.amazon.dev.

    Our first draft said we could not confirm one exists. That was wrong -- we
    had probed the API host instead of reading the page that names it.
    """
    text = _text("FRICTION_LOG.md")
    assert "We cannot demonstrate that a Ring MCP server exists" not in text
    assert "knowledge.appstore-mcp.ring.amazon.dev/mcp" in text


def test_friction_log_does_not_claim_rate_limits_are_undocumented():
    """Ring publishes 100 TPS per partner and a webhook retry schedule."""
    text = _text("FRICTION_LOG.md")
    assert "not documented in enough detail to rely on" not in text
    assert "100 requests per second" in text


def test_submission_no_longer_lists_rate_limits_as_unverified():
    text = _text("SUBMISSION.md")
    assert "- Exact rate limits and retry semantics." not in text


def test_demo_script_states_the_ring_simulator_requirement():
    """The Ring track rule requires a simulator or a real device in the video.

    Our scenario runs on WITNESS's own simulated source, which is not the Ring
    simulator. The script must say so rather than let the recording imply
    otherwise.
    """
    text = _text("DEMO_SCRIPT.md")
    assert "through a simulator or an actual Ring" in text
    assert "does **not** contact Ring" in text
