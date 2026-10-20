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
