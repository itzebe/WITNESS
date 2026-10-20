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
    # The simulated source must be disclaimed as not contacting Ring. The
    # wording sits inside a blockquote, so assert on the parts, not the layout.
    normalized = " ".join(text.replace(">", " ").split())
    assert "does **not** contact Ring" in normalized


def test_submission_does_not_claim_playground_access():
    """We did not obtain a Playground token; the docs must not imply we did.

    The Playground is Ring's official simulator and is behind an Amazon
    Developer sign-in. No credential exists in this environment, so any wording
    that reads as "we used the Playground" would be false.
    """
    text = _text("SUBMISSION.md")
    assert "We have not run the live path against the Playground" in text
    # The two escape hatches the rules actually offer must both be stated.
    assert "obtain a free Ring developer account" in text
    assert "was not exercised" in text


def test_submission_states_the_runtime_requirement_is_met_by_the_live_adapter():
    """The rules require the track technology be *called* at runtime, not named.

    WITNESS registers RingAdapter as a source and runs it on
    POST /api/observe/ring. The submission must point at that, so a reviewer
    does not have to take the runtime claim on trust.
    """
    text = _text("SUBMISSION.md")
    assert "witness/api.py" in text
    assert "POST /api/observe/ring" in text


def test_friction_log_entries_carry_the_six_scored_fields():
    """The rules score friction logs on six named fields, worth up to 10%.

    Each entry must expose all six, or a reviewer scoring the bonus has to
    reconstruct them from prose.
    """
    text = _text("FRICTION_LOG.md")
    for field in (
        "**Task attempted:**",
        "**Steps taken:**",
        "**Expected:**",
        "**Actual:**",
        "**Severity:**",
        "**Workaround used:**",
        "**Actionable suggestion:**",
    ):
        assert text.count(field) >= 5, f"friction record field missing: {field}"


def test_friction_log_answers_the_required_product_feedback_questions():
    """The submission form requires five specific product-feedback answers."""
    text = _text("FRICTION_LOG.md")
    assert "Which developer tools, APIs and SDKs did you use, and for what?" in text
    assert "What worked well?" in text
    assert "What needs work?" in text
    assert "How was your onboarding experience" in text
    assert "Would you build with these devices and services again?" in text


def test_submission_does_not_claim_a_mini_challenge_entry():
    """WITNESS enters Ring only. Claiming a mini challenge would be false.

    AWS Builder needs an AWS service integration (WITNESS calls none), and Open
    Source needs a contribution to another public repo during the window.
    """
    text = _text("SUBMISSION.md")
    assert "| Mini challenges participated in | None" in text
