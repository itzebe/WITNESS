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
    normalized = " ".join(text.replace(">", " ").split())
    assert "through a simulator or an actual Ring" in normalized
    # The simulated source must be disclaimed as not contacting Ring.
    assert "does **not** contact Ring" in normalized


def test_submission_records_the_live_call_without_overclaiming():
    """The live call happened; the docs must state it precisely, not broadly.

    A Playground token exercised GET /v1/devices and the history endpoint. What
    must never appear is a claim that the Playground produced a withdrawal, an
    encryption transition, a duplicate or a late arrival - it cannot.
    """
    text = _text("SUBMISSION.md")
    normalized = " ".join(text.replace(">", " ").split())
    # The verified call is stated, with its actual result.
    assert "VERIFIED LIVE" in text
    assert "GET /v1/devices" in text
    assert "HTTP 200" in text
    # The limits of that call are stated in the same document.
    assert "does **not** establish" in normalized
    assert "cannot produce" in normalized


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


def test_demo_script_separates_the_live_segment_from_the_local_one():
    """The organizer permits combining sources, but only if we label which is which.

    Emerson Sklar (Amazon Ring developer advocate) answered on the Devpost
    forum: "combining them is fine. Use the Playground streams to show the
    integration working end to end, and your own videos to show scenarios the
    samples don't cover. Just make it clear in your video and README which
    footage comes from Ring and which is yours". The script must therefore have
    a distinct live segment and label the local one, not blur the two.
    """
    text = _text("DEMO_SCRIPT.md")
    assert "Segment A (live" in text
    assert "Segment B (local" in text
    # The local segment must never be called the simulator.
    normalized = " ".join(text.replace(">", " ").split())
    assert "must never be presented as the Ring simulator" in normalized


def test_demo_script_records_the_thirty_minute_token_expiry():
    """Ring's release notes say the Playground token is valid 30 minutes.

    A recording session that outlives the token would produce a failing live
    segment. The script has to warn about it.
    """
    text = _text("DEMO_SCRIPT.md")
    assert "valid for 30 minutes" in text


def test_demo_script_does_not_claim_no_credentials_exist():
    """The script must not say we have no credentials, or no call was made.

    A token was supplied on 2026-10-21 and three endpoints answered HTTP 200.
    That a *fresh* token is needed to re-record is a different statement from
    "we have no credentials" and "it has not made an authenticated call".
    """
    text = _text("DEMO_SCRIPT.md")
    assert "We have no Amazon Developer credentials in this environment" not in text
    assert "so it has not made an authenticated call" not in text
    # The accurate replacement must be present.
    assert "short-lived and has expired" in text


def test_submission_quotes_the_organizer_on_combining_sources():
    """The hybrid approach is only defensible with the organizer's own words."""
    normalized = " ".join(_text("SUBMISSION.md").replace(">", " ").split())
    assert "The simulator is the Ring Developer Playground" in normalized
    assert "combining them is fine" in normalized


def test_submission_does_not_deny_the_live_call_it_records():
    """The same document cannot both record a live call and deny one happened.

    A later edit left the UNVERIFIED section saying no credentials were
    available and "that is not a real call", directly contradicting the
    VERIFIED LIVE section above it. The dated verification stands; only the
    credential's current validity is in question.
    """
    text = _text("SUBMISSION.md")
    assert "No Amazon Developer credentials were" not in text
    assert "that is not a real call and we do not present it as one" not in text
    # The correction must be present, not just the removal.
    assert "has since **expired**" in text


def test_submission_separates_past_live_evidence_from_current_credentials():
    """Historical live results must not be read as present credential access."""
    normalized = " ".join(_text("SUBMISSION.md").split())
    assert "Credential availability (current state)" in normalized
    assert "no valid Ring credential is configured in this environment now" in normalized
    assert "exits 2" in normalized


def test_submission_documented_list_excludes_exercised_endpoints():
    """Device configurations was exercised live, so it is not DOCUMENTED.

    Listing it under "not exercised by us" would contradict the VERIFIED LIVE
    section that records its HTTP 200.
    """
    text = _text("SUBMISSION.md")
    documented = text.split("### DOCUMENTED", 1)[1].split("### UNAVAILABLE", 1)[0]
    assert "device configurations" not in documented.lower()


def test_evidence_matrix_marks_the_live_verification_as_dated():
    """The matrix must not read as if live access is currently reproducible.

    The live evidence used a short-lived token that has since expired. The
    matrix records the result as dated so a reader does not expect to re-run it
    without a fresh credential.
    """
    text = _text("EVIDENCE_MATRIX.md")
    assert "verification is **dated**" in text
    assert "has since expired" in text
