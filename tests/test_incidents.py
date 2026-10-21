"""Incident-class tests.

Each test here corresponds to one incident class in ``docs/EVIDENCE_MATRIX.md``
and pins a specific negative guarantee: the conclusion WITNESS must *not* reach
from the available evidence. They are grouped by incident class rather than by
module because the matrix is organised that way.

These tests exercise the deterministic engine and the adapter normalisers. None
of them claims a live Ring call: the conditions they cover -- withdrawal,
encryption transition, duplicate delivery, late arrival, outage -- cannot be
produced by the Playground, which exposes one device and no events. Live
evidence for the three endpoints that *were* exercised is recorded in
``docs/SUBMISSION.md`` under VERIFIED LIVE.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import T0, device, event, make_observation
from witness.engine.models import (
    EventRecord,
    FailureKind,
    classify_http_status,
)
from witness.engine.reconcile import reconcile
from witness.engine.rules import deduplicate_events
from witness.engine.status import Status


def _presence(recon, device_id):
    for view in recon.devices:
        if view.device_id == device_id:
            return view.presence
    raise AssertionError(f"device {device_id} not in reconciliation")


# ==========================================================================
# Incident class 1: withdrawal
# ==========================================================================


def test_empty_event_history_does_not_produce_a_withdrawal():
    """A device with a successful, empty history is still present.

    This is the distinction the product exists to make: the source answered and
    reported no events. That is not the device disappearing, and it is not
    evidence that nothing happened at the device either.
    """
    obs = make_observation("o1", devices=[device("d1", "Front Door")], events=[])
    recon = reconcile("ring", [obs], now=T0)

    assert _presence(recon, "d1").status is Status.OBSERVED
    assert recon.counts["withdrawn"] == 0
    assert recon.events == ()


def test_empty_inventory_with_no_prior_history_is_not_a_withdrawal():
    """A successful inventory that has never listed a device asserts nothing.

    There is no prior observation to compare against, so a withdrawal claim
    would be unsupported. WITNESS reports the limitation instead.
    """
    obs = make_observation("o1", devices=[])
    recon = reconcile("ring", [obs], now=T0)

    assert recon.counts["withdrawn"] == 0
    assert recon.devices == ()
    assert any(l.status is Status.INSUFFICIENT_EVIDENCE for l in recon.limitations)


def test_failed_inventory_never_becomes_a_withdrawal():
    """An outage must not be read as the device being removed."""
    seen = make_observation("o1", devices=[device("d1", "Front Door")], observed_at=T0)
    failed = make_observation(
        "o2",
        observed_at=T0 + timedelta(minutes=5),
        inventory_ok=False,
        inventory_failure=FailureKind.SERVER_ERROR,
    )
    recon = reconcile("ring", [seen, failed], now=T0 + timedelta(minutes=5))

    assert _presence(recon, "d1").status is Status.SOURCE_UNAVAILABLE
    assert recon.counts["withdrawn"] == 0


def test_withdrawal_requires_consecutive_successes_not_elapsed_time():
    """Three absences separated by an outage still reach the threshold.

    A failure neither extends nor resets the streak, so the count reflects
    successful observations only.
    """
    seen = make_observation("o1", devices=[device("d1", "A")], observed_at=T0)
    absent1 = make_observation("o2", devices=[], observed_at=T0 + timedelta(minutes=1))
    outage = make_observation(
        "o3",
        observed_at=T0 + timedelta(minutes=2),
        inventory_ok=False,
        inventory_failure=FailureKind.SERVER_ERROR,
    )
    absent2 = make_observation("o4", devices=[], observed_at=T0 + timedelta(minutes=3))

    recon = reconcile("ring", [seen, absent1, outage, absent2], now=T0 + timedelta(minutes=3))
    assert _presence(recon, "d1").status is Status.SOURCE_WITHDRAWN
    assert recon.counts["withdrawn"] == 1


# ==========================================================================
# Incident class 2: encryption transition
# ==========================================================================


def test_current_encryption_value_does_not_assert_a_transition():
    """A transition is not a status; WITNESS reports the current state only.

    Given two observations where encryption changed from on to off, WITNESS
    reports CONTENT_VISIBLE for the latest observation. It does not emit a
    "transition" conclusion, because a transition is a claim about the
    relationship between two readings and the product asserts only what the
    most recent reading stated. This test pins that boundary rather than
    inventing a transition capability.
    """
    encrypted = make_observation(
        "o1", devices=[device("d1", "A", encrypted=True)], observed_at=T0
    )
    readable = make_observation(
        "o2",
        devices=[device("d1", "A", encrypted=False)],
        observed_at=T0 + timedelta(minutes=5),
    )
    recon = reconcile("ring", [encrypted, readable], now=T0 + timedelta(minutes=5))

    content = recon.devices[0].content
    assert content.status is Status.CONTENT_VISIBLE
    assert content.detail["e2e_encrypted"] is False
    # No status in the taxonomy asserts a transition, so none can appear.
    assert "transition" not in content.status.value.lower()
    assert "transition" not in content.rule_id.lower()


def test_encryption_field_absent_is_unknown_not_unencrypted():
    """The absence of the field must not be read as encryption being off.

    This is the field-level version of the empty-history mistake: not reported
    is not the same as reported false.
    """
    obs = make_observation("o1", devices=[device("d1", "A", stated=False)])
    recon = reconcile("ring", [obs], now=T0)

    assert recon.devices[0].content.status is Status.CONTENT_UNAVAILABLE
    assert recon.counts["encrypted"] == 0


def test_failed_configurations_is_unknown_not_encrypted_and_not_plaintext():
    """A configurations outage supports no claim in either direction."""
    obs = make_observation("o1", devices=[device("d1", "A")], configurations_ok=False)
    recon = reconcile("ring", [obs], now=T0)

    content = recon.devices[0].content
    assert content.status is Status.CONTENT_UNAVAILABLE
    assert content.detail["configurations_ok"] is False


def test_encryption_reading_survives_the_device_leaving_the_inventory():
    """A reading taken while a device was inspected is not discarded later.

    The encryption value is evidence WITNESS holds about the device. When the
    device stops being returned, the reading remains and is still labelled with
    the observation that took it. The dating contract itself is pinned by
    ``test_encryption_reading_keeps_the_time_it_was_taken``.
    """
    encrypted = make_observation(
        "o1", devices=[device("d1", "A", encrypted=True)], observed_at=T0
    )
    absent = make_observation("o2", devices=[], observed_at=T0 + timedelta(minutes=10))
    recon = reconcile("ring", [encrypted, absent], now=T0 + timedelta(minutes=10))

    content = recon.devices[0].content
    assert content.status is Status.SOURCE_ENCRYPTED
    assert content.detail["e2e_encrypted"] is True
    # The reading is dated to the pass that took it, not the latest pass.
    from witness.engine.models import iso

    assert iso(T0) in content.explanation
    assert iso(T0 + timedelta(minutes=10)) not in content.explanation


# ==========================================================================
# Incident class 3: duplicate event
# ==========================================================================


def test_distinct_event_ids_are_not_merged_even_when_content_is_identical():
    """Two different ids are two events, whatever else matches.

    This is the false-positive boundary for deduplication: merging by content
    would silently drop a real event.
    """
    a = event("e1", event_type="motion", reported_at=T0)
    b = event("e2", event_type="motion", reported_at=T0)
    unique, counts = deduplicate_events([a, b])

    assert len(unique) == 2
    assert all(n == 1 for n in counts.values())


def test_records_without_ids_merge_only_on_type_and_time():
    """Without an id, the documented rule is (event type, source time)."""
    same = [event("", event_type="motion", reported_at=T0), event("", event_type="motion", reported_at=T0)]
    different_type = event("", event_type="ding", reported_at=T0)
    different_time = event("", event_type="motion", reported_at=T0 + timedelta(minutes=1))

    unique, counts = deduplicate_events(same + [different_type, different_time])
    assert len(unique) == 3
    assert counts[same[0].identity_key()] == 2


def test_duplicate_detection_is_reported_and_counted_once():
    ev = event("e1")
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev, ev])
    recon = reconcile("ring", [obs], now=T0)

    assert len(recon.events) == 1
    assert recon.events[0].status is Status.EVENT_DUPLICATE
    assert recon.events[0].detail["duplicate_count"] == 2
    assert recon.events[0].detail["identity_key"] == "id:e1"


# ==========================================================================
# Incident class 4: late-arriving event
# ==========================================================================


def test_event_with_no_source_time_is_not_claimed_to_be_late():
    """An unknown time cannot support a lateness claim.

    Lateness is a claim about the interval between two times. With one of them
    unknown there is no interval, so WITNESS must not annotate the event.
    """
    unknown = EventRecord(event_id="e1", event_type="motion", reported_at=None, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[unknown])
    recon = reconcile("ring", [obs], now=T0)

    assert recon.events[0].status is Status.EVENT_OBSERVED
    assert recon.events[0].detail["reported_at"] is None


def test_out_of_order_events_in_one_response_are_judged_individually():
    """Delivery order does not decide lateness; the source time does."""
    newest = event("e2", reported_at=T0, retrieved_at=T0)
    oldest = event("e1", reported_at=T0 - timedelta(hours=2), retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[newest, oldest])
    recon = reconcile("ring", [obs], now=T0)

    by_id = {a.subject_id: a.status for a in recon.events}
    assert by_id["e2"] is Status.EVENT_OBSERVED
    assert by_id["e1"] is Status.EVENT_LATE


def test_lateness_boundary_is_exclusive_at_the_threshold():
    """At exactly the threshold an event is not late; just over it, it is."""
    at_threshold = event("e1", reported_at=T0 - timedelta(minutes=5), retrieved_at=T0)
    over_threshold = event("e2", reported_at=T0 - timedelta(minutes=5, seconds=1), retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[at_threshold, over_threshold])
    recon = reconcile("ring", [obs], now=T0)

    by_id = {a.subject_id: a.status for a in recon.events}
    assert by_id["e1"] is Status.EVENT_OBSERVED
    assert by_id["e2"] is Status.EVENT_LATE


# ==========================================================================
# Incident class 5: outage and evidence unavailability
# ==========================================================================


def test_auth_failure_is_a_source_failure_not_an_empty_home():
    """A 401 must never be reported as "no devices"."""
    obs = make_observation("o1", inventory_ok=False, inventory_failure=FailureKind.AUTH_FAILED)
    recon = reconcile("ring", [obs], now=T0)

    assert recon.source_assessment is not None
    assert recon.source_assessment.status is Status.SOURCE_UNAVAILABLE
    assert recon.source_assessment.detail["failure"]["kind"] == "AUTH_FAILED"
    assert recon.devices == ()


def test_http_status_maps_to_the_documented_failure_kinds():
    """Each status code licenses a specific, distinct conclusion."""
    assert classify_http_status(401) is FailureKind.AUTH_FAILED
    assert classify_http_status(403) is FailureKind.FORBIDDEN
    assert classify_http_status(404) is FailureKind.NOT_FOUND
    assert classify_http_status(429) is FailureKind.RATE_LIMITED
    assert classify_http_status(500) is FailureKind.SERVER_ERROR
    assert classify_http_status(503) is FailureKind.SERVER_ERROR
    # A redirect to an unconfigured host is treated as transport, not success.
    assert classify_http_status(302) is FailureKind.NETWORK_ERROR


def test_partial_response_keeps_the_readable_axis_and_flags_the_other():
    """Inventory readable, configurations unavailable: report each honestly."""
    obs = make_observation("o1", devices=[device("d1", "A")], configurations_ok=False)
    recon = reconcile("ring", [obs], now=T0)

    assert _presence(recon, "d1").status is Status.OBSERVED
    assert recon.devices[0].content.status is Status.CONTENT_UNAVAILABLE


def test_missing_history_is_not_reported_as_no_events_occurred():
    """A failed history request yields no event claims at all."""
    obs = make_observation("o1", devices=[device("d1", "A")], history_ok=False)
    recon = reconcile("ring", [obs], now=T0)

    assert recon.events == ()
    assert recon.counts["withdrawn"] == 0


def test_stale_evidence_is_flagged_and_not_presented_as_current():
    obs = make_observation("o1", devices=[device("d1", "A")], observed_at=T0 - timedelta(hours=2))
    recon = reconcile("ring", [obs], now=T0)

    assert recon.devices[0].staleness["stale"] is True
    assert recon.staleness["stale"] is True
