"""Tests for the status taxonomy and its documentation contract.

The central assertion here is that a status cannot exist without a documented
spec: trigger, required evidence, forbidden conclusions, explanation, and tone.
That is what stops WITNESS from growing a status nobody can explain.
"""

from __future__ import annotations

import pytest

from witness.engine.status import (
    CONTENT_STATUSES,
    EVENT_STATUSES,
    LIMITATION_STATUSES,
    PRESENCE_STATUSES,
    STATUS_SPEC,
    Status,
    spec_for,
)


def test_every_status_has_a_spec():
    """No status may exist without documentation."""
    missing = [s for s in Status if s not in STATUS_SPEC]
    assert missing == [], f"statuses without a spec: {missing}"


def test_every_spec_matches_its_key():
    for status, spec in STATUS_SPEC.items():
        assert spec.status is status


@pytest.mark.parametrize("status", list(Status))
def test_spec_is_complete(status):
    spec = spec_for(status)
    assert spec.label, f"{status} has no label"
    assert spec.definition, f"{status} has no definition"
    assert spec.trigger, f"{status} has no documented trigger"
    assert spec.explanation_template, f"{status} has no explanation template"
    assert spec.tone in {"ok", "warn", "unknown", "error"}, f"{status} has an unknown tone"


def test_statuses_that_assert_are_required_to_state_what_they_do_not_prove():
    """Any status asserting something must document what it does NOT establish."""
    asserting = {
        Status.OBSERVED,
        Status.NOT_OBSERVED,
        Status.SOURCE_WITHDRAWN,
        Status.SOURCE_ENCRYPTED,
        Status.CONTENT_VISIBLE,
        Status.EVENT_OBSERVED,
    }
    for status in asserting:
        spec = spec_for(status)
        assert spec.forbidden_conclusions, f"{status} asserts something but documents no limits"


def test_absence_is_never_documented_as_proof_of_removal():
    """The core principle: missing evidence is not proof that nothing happened."""
    for status in (Status.NOT_OBSERVED, Status.SOURCE_WITHDRAWN):
        spec = spec_for(status)
        joined = " ".join(spec.forbidden_conclusions).lower()
        assert "tamper" in joined or "removed" in joined or "offline" in joined, (
            f"{status} must explicitly forbid the removal/tamper conclusion"
        )


def test_source_unavailable_forbids_device_level_conclusions():
    spec = spec_for(Status.SOURCE_UNAVAILABLE)
    joined = " ".join(spec.forbidden_conclusions).lower()
    assert "offline" in joined
    assert "missing" in joined or "removed" in joined


def test_classification_unavailable_forbids_guessing():
    spec = spec_for(Status.CLASSIFICATION_UNAVAILABLE)
    joined = " ".join(spec.forbidden_conclusions).lower()
    assert "guess" in joined


def test_status_axes_are_disjoint():
    """An observation can carry one status per axis; the axes must not overlap."""
    axes = [PRESENCE_STATUSES, CONTENT_STATUSES, EVENT_STATUSES, LIMITATION_STATUSES]
    for i, a in enumerate(axes):
        for b in axes[i + 1 :]:
            assert not (a & b), f"axes overlap: {a & b}"


def test_axes_cover_the_enum():
    covered = PRESENCE_STATUSES | CONTENT_STATUSES | EVENT_STATUSES | LIMITATION_STATUSES
    assert set(Status) == covered


def test_required_evidence_is_a_tuple_of_strings():
    for status, spec in STATUS_SPEC.items():
        assert isinstance(spec.required_evidence, tuple), status
        for item in spec.required_evidence:
            assert isinstance(item, str)


# --------------------------------------------------------------------------
# The taxonomy must be honest about its own size and reachability
# --------------------------------------------------------------------------


def test_the_status_count_is_thirteen():
    """The README, the demo script and the UI all quote this number.

    It was previously stated as nine while the table listed thirteen. A product
    whose whole premise is not overstating what it knows cannot overstate the
    size of its own vocabulary.
    """
    assert len(Status) == 13
    assert len(STATUS_SPEC) == 13


def test_every_status_is_reachable_or_documented_as_defensive():
    """Every status must either be producible or explicitly labelled a fallback.

    A status that is documented as a real finding but can never be produced is a
    claim the product cannot honour. ``UNKNOWN`` is the single defensive
    fallback, and its spec must say so.
    """
    reachable = {
        Status.OBSERVED,
        Status.NOT_OBSERVED,
        Status.SOURCE_WITHDRAWN,
        Status.SOURCE_UNAVAILABLE,
        Status.CONTENT_VISIBLE,
        Status.SOURCE_ENCRYPTED,
        Status.CONTENT_UNAVAILABLE,
        Status.EVENT_OBSERVED,
        Status.EVENT_LATE,
        Status.EVENT_DUPLICATE,
        Status.CLASSIFICATION_UNAVAILABLE,
        Status.INSUFFICIENT_EVIDENCE,
    }
    defensive = {Status.UNKNOWN}

    assert reachable | defensive == set(Status), "a status is unaccounted for"

    spec = spec_for(Status.UNKNOWN)
    assert "fallback" in spec.trigger.lower(), (
        "UNKNOWN is a defensive fallback; its documented trigger must say so"
    )


def test_defensive_fallback_does_not_claim_the_device_was_never_observed():
    """The fallback's wording must be true whenever it can fire.

    It is only reachable for a device that appears in a successful inventory,
    so an explanation reading "has never successfully observed" would be false
    at the moment it was shown.
    """
    from witness.engine.rules import rule_unknown

    assessment = rule_unknown("cam-1", "Front Door")
    text = assessment.explanation.lower()
    assert "never" not in text
    assert "could not establish" in text


def test_readme_states_the_same_status_count_as_the_code():
    """Guard against the documentation drifting from the enum again."""
    import pathlib
    import re

    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    words = {9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen"}
    expected = words[len(Status)]
    assert re.search(rf"\b{expected} statuses\b", text, re.IGNORECASE), (
        f"README must state '{expected} statuses' to match the {len(Status)}-status enum"
    )

