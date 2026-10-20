"""Reconciliation tests -- the semantic core of WITNESS.

Every test here corresponds to one of the mandatory semantic rules. These are
the tests that would catch WITNESS lying to a user.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from witness.engine.reconcile import reconcile
from witness.engine.rules import Policy, deduplicate_events
from witness.engine.status import Status
from tests.conftest import T0, device, event, make_observation


def _presence(recon, device_id):
    for view in recon.devices:
        if view.device_id == device_id:
            return view.presence
    raise AssertionError(f"device {device_id} not in reconciliation")


# --------------------------------------------------------------------------
# First observation
# --------------------------------------------------------------------------


def test_first_successful_observation_is_observed():
    obs = make_observation("o1", devices=[device("d1", "Front Door")])
    recon = reconcile("ring", [obs], now=T0)
    assert _presence(recon, "d1").status is Status.OBSERVED


def test_empty_history_yields_no_devices_and_no_false_claims():
    obs = make_observation("o1", devices=[])
    recon = reconcile("ring", [obs], now=T0)
    assert recon.devices == ()
    assert recon.counts["observed"] == 0


def test_no_observations_at_all_is_unknown_not_a_finding():
    recon = reconcile("ring", [], now=T0)
    assert recon.devices == ()
    assert recon.latest_observation_at is None
    assert recon.counts["unknown"] == 0


# --------------------------------------------------------------------------
# Repeated identical inventory
# --------------------------------------------------------------------------


def test_repeated_identical_inventory_stays_observed():
    obs1 = make_observation("o1", devices=[device("d1", "Front Door")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "Front Door")])
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))
    assert _presence(recon, "d1").status is Status.OBSERVED


# --------------------------------------------------------------------------
# The single most important rule: one absence is NOT a withdrawal
# --------------------------------------------------------------------------


def test_single_absence_is_not_observed_and_not_withdrawn():
    obs1 = make_observation("o1", devices=[device("d1", "Front Door"), device("d2", "Driveway")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "Front Door")])
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))
    assert _presence(recon, "d2").status is Status.NOT_OBSERVED


def test_single_absence_explanation_forbids_removal_conclusion():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "Driveway")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))
    presence = _presence(recon, "d2")
    assert "not evidence of removal" in presence.explanation
    assert presence.detail["consecutive_absences"] == 1
    assert presence.detail["threshold"] == 2


def test_repeated_absences_reach_withdrawal_threshold():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "Driveway")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    obs3 = make_observation("o3", observed_at=T0 + timedelta(minutes=20), devices=[device("d1", "A")])
    recon = reconcile("ring", [obs1, obs2, obs3], now=T0 + timedelta(minutes=20))
    presence = _presence(recon, "d2")
    assert presence.status is Status.SOURCE_WITHDRAWN
    assert presence.detail["consecutive_absences"] == 2


def test_withdrawal_does_not_claim_tampering():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "Driveway")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    obs3 = make_observation("o3", observed_at=T0 + timedelta(minutes=20), devices=[device("d1", "A")])
    recon = reconcile("ring", [obs1, obs2, obs3], now=T0 + timedelta(minutes=20))
    text = _presence(recon, "d2").explanation.lower()
    assert "tamper" not in text
    assert "cannot determine why" in text


def test_device_returning_after_absence_is_observed_again():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "Driveway")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    obs3 = make_observation("o3", observed_at=T0 + timedelta(minutes=20), devices=[device("d1", "A"), device("d2", "Driveway")])
    recon = reconcile("ring", [obs1, obs2, obs3], now=T0 + timedelta(minutes=20))
    assert _presence(recon, "d2").status is Status.OBSERVED


# --------------------------------------------------------------------------
# Failed request vs successful empty response
# --------------------------------------------------------------------------


def test_failed_request_does_not_prove_device_offline():
    obs1 = make_observation("o1", devices=[device("d1", "Front Door")])
    obs2 = make_observation(
        "o2", observed_at=T0 + timedelta(minutes=10), inventory_ok=False, devices=[]
    )
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))
    presence = _presence(recon, "d1")
    assert presence.status is Status.SOURCE_UNAVAILABLE
    assert "not evidence about the device" in presence.explanation


def test_failed_request_does_not_advance_the_withdrawal_counter():
    """A failure is not an absence and must not push a device toward withdrawal."""
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "Driveway")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), inventory_ok=False, devices=[])
    obs3 = make_observation("o3", observed_at=T0 + timedelta(minutes=20), devices=[device("d1", "A")])
    recon = reconcile("ring", [obs1, obs2, obs3], now=T0 + timedelta(minutes=20))
    presence = _presence(recon, "d2")
    assert presence.status is Status.NOT_OBSERVED
    assert presence.detail["consecutive_absences"] == 1


def test_successful_empty_response_differs_from_failure():
    """The single most important distinction in the whole product."""
    empty = make_observation("o1", devices=[])
    failed = make_observation("o2", inventory_ok=False, devices=[])

    recon_empty = reconcile("ring", [empty], now=T0)
    recon_failed = reconcile("ring", [failed], now=T0)

    assert recon_empty.source_assessment is None
    assert recon_empty.latest_inventory_ok is True

    assert recon_failed.source_assessment is not None
    assert recon_failed.source_assessment.status is Status.SOURCE_UNAVAILABLE
    assert recon_failed.latest_inventory_ok is False


def test_source_level_failure_is_reported_once_not_per_device():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "B")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), inventory_ok=False)
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))
    assert recon.source_assessment is not None
    assert len(recon.devices) == 2
    assert all(v.presence.status is Status.SOURCE_UNAVAILABLE for v in recon.devices)
    assert recon.counts["unavailable"] == 2
    assert recon.counts["observed"] == 0
    assert recon.counts["withdrawn"] == 0


# --------------------------------------------------------------------------
# Encryption axis
# --------------------------------------------------------------------------


def test_encrypted_device_is_source_encrypted():
    obs = make_observation("o1", devices=[device("d1", "Front Door", encrypted=True)])
    recon = reconcile("ring", [obs], now=T0)
    view = recon.devices[0]
    assert view.content.status is Status.SOURCE_ENCRYPTED


def test_unencrypted_device_is_content_visible():
    obs = make_observation("o1", devices=[device("d1", "Front Door", encrypted=False)])
    recon = reconcile("ring", [obs], now=T0)
    assert recon.devices[0].content.status is Status.CONTENT_VISIBLE


def test_absent_encryption_field_is_unknown_not_false():
    """None and False are different findings and must not be conflated."""
    obs = make_observation("o1", devices=[device("d1", "Front Door", encrypted=None, stated=False)])
    recon = reconcile("ring", [obs], now=T0)
    assert recon.devices[0].content.status is Status.CONTENT_UNAVAILABLE


def test_failed_configurations_is_unknown_not_unencrypted():
    obs = make_observation(
        "o1", devices=[device("d1", "Front Door", encrypted=None, stated=False)], configurations_ok=False
    )
    recon = reconcile("ring", [obs], now=T0)
    assert recon.devices[0].content.status is Status.CONTENT_UNAVAILABLE


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


def test_event_on_time_is_event_observed():
    ev = event("e1", reported_at=T0, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev])
    recon = reconcile("ring", [obs], now=T0)
    assert recon.events[0].status is Status.EVENT_OBSERVED


def test_late_event_is_flagged_late():
    ev = event("e1", reported_at=T0 - timedelta(hours=2), retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev])
    recon = reconcile("ring", [obs], now=T0)
    assert recon.events[0].status is Status.EVENT_LATE


def test_lateness_threshold_is_respected():
    just_under = event("e1", reported_at=T0 - timedelta(minutes=4), retrieved_at=T0)
    just_over = event("e2", reported_at=T0 - timedelta(minutes=6), retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[just_under, just_over])
    recon = reconcile("ring", [obs], now=T0)
    by_id = {a.subject_id: a.status for a in recon.events}
    assert by_id["e1"] is Status.EVENT_OBSERVED
    assert by_id["e2"] is Status.EVENT_LATE


def test_duplicate_event_is_detected_and_counted_once():
    ev = event("e1")
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev, ev])
    recon = reconcile("ring", [obs], now=T0)
    assert len(recon.events) == 1
    assert recon.events[0].status is Status.EVENT_DUPLICATE
    assert recon.events[0].detail["duplicate_count"] == 2


def test_duplicate_does_not_claim_only_one_real_event_occurred():
    ev = event("e1")
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev, ev])
    recon = reconcile("ring", [obs], now=T0)
    assert "do not prove a single real-world event" in recon.events[0].explanation


def test_deduplication_key_rule_is_deterministic():
    a = event("", event_type="motion", reported_at=T0)
    b = event("", event_type="motion", reported_at=T0)
    c = event("", event_type="motion", reported_at=T0 + timedelta(minutes=1))
    unique, counts = deduplicate_events([a, b, c])
    assert len(unique) == 2
    assert counts[a.identity_key()] == 2


def test_event_with_source_id_deduplicates_on_id_not_time():
    a = event("same-id", reported_at=T0)
    b = event("same-id", reported_at=T0 + timedelta(hours=5))
    unique, counts = deduplicate_events([a, b])
    assert len(unique) == 1
    assert counts["id:same-id"] == 2


def test_no_history_is_not_reported_as_no_events():
    obs = make_observation("o1", devices=[device("d1", "A")], events=[], history_ok=False)
    recon = reconcile("ring", [obs], now=T0)
    assert recon.events == ()


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_missing_classification_produces_a_limitation_not_a_guess():
    ev = event("e1", classification=None)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev])
    recon = reconcile("ring", [obs], now=T0)
    statuses = {lim.status for lim in recon.limitations}
    assert Status.CLASSIFICATION_UNAVAILABLE in statuses


def test_classification_limitation_names_the_available_fields():
    ev = event("e1", classification=None)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev])
    recon = reconcile("ring", [obs], now=T0)
    lim = next(l for l in recon.limitations if l.status is Status.CLASSIFICATION_UNAVAILABLE)
    assert "event_type" in lim.explanation
    assert "will not infer a classification" in lim.explanation


def test_present_classification_suppresses_the_limitation():
    ev = event("e1", classification="human")
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev])
    recon = reconcile("ring", [obs], now=T0)
    assert not [l for l in recon.limitations if l.status is Status.CLASSIFICATION_UNAVAILABLE]


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


def test_old_observation_is_marked_stale():
    obs = make_observation("o1", devices=[device("d1", "A")])
    recon = reconcile("ring", [obs], now=T0 + timedelta(hours=3))
    assert recon.staleness["stale"] is True
    assert recon.devices[0].staleness["stale"] is True


def test_recent_observation_is_not_stale():
    obs = make_observation("o1", devices=[device("d1", "A")])
    recon = reconcile("ring", [obs], now=T0 + timedelta(minutes=1))
    assert recon.staleness["stale"] is False


def test_staleness_carries_the_age_and_the_threshold():
    obs = make_observation("o1", devices=[device("d1", "A")])
    recon = reconcile("ring", [obs], now=T0 + timedelta(minutes=45))
    assert recon.staleness["age_seconds"] == 45 * 60
    assert recon.staleness["staleness_threshold_seconds"] == 30 * 60


# --------------------------------------------------------------------------
# Determinism and policy
# --------------------------------------------------------------------------


def test_reconciliation_is_deterministic():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "B", encrypted=True)])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    a = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10)).to_dict()
    b = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10)).to_dict()
    assert a == b


def test_policy_threshold_is_configurable_and_reported():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "B")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    strict = Policy(withdrawal_threshold=1)
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10), policy=strict)
    assert _presence(recon, "d2").status is Status.SOURCE_WITHDRAWN


def test_counts_partition_every_device():
    """Every device must be accounted for in exactly one presence bucket."""
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "B", encrypted=True), device("d3", "C")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    obs3 = make_observation("o3", observed_at=T0 + timedelta(minutes=20), devices=[device("d1", "A")])
    recon = reconcile("ring", [obs1, obs2, obs3], now=T0 + timedelta(minutes=20))
    counted = (recon.counts["observed"] + recon.counts["not_observed"]
               + recon.counts["withdrawn"] + recon.counts["unavailable"]
               + recon.counts["unknown"])
    assert counted == recon.counts["total_devices"] == len(recon.devices) == 3
    assert recon.counts["encrypted"] == 1


def test_reconciliation_ignores_other_sources():
    obs_a = make_observation("o1", source_id="ring", devices=[device("d1", "A")])
    obs_b = make_observation("o2", source_id="other", devices=[device("x", "X")])
    recon = reconcile("ring", [obs_a, obs_b], now=T0)
    assert [v.device_id for v in recon.devices] == ["d1"]


def test_every_assessment_names_the_rule_that_produced_it():
    obs1 = make_observation("o1", devices=[device("d1", "A"), device("d2", "B")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")])
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))
    for view in recon.devices:
        assert view.presence.rule_id.startswith("rule:")
        assert view.content.rule_id.startswith("rule:")


def test_every_assessment_carries_evidence_references():
    obs = make_observation("o1", devices=[device("d1", "A")])
    recon = reconcile("ring", [obs], now=T0)
    for view in recon.devices:
        assert view.presence.evidence_refs
        assert view.presence.scope


# --------------------------------------------------------------------------
# Absence longer than the reconciliation window
# --------------------------------------------------------------------------


def test_withdrawal_survives_absence_longer_than_the_window():
    """A device absent for longer than the window is still a withdrawal.

    Regression: the streak scan was bounded by the window and did not look
    further back for the last sighting, so a device absent longer than the
    window reported last_seen=None. Reconcile reads that as "never observed"
    and downgrades a genuine withdrawal to UNKNOWN, with an explanation that
    is factually false ("has never successfully observed ..."). A device that
    went away and stayed away is the clearest possible withdrawal.
    """
    window = Policy().reconciliation_window
    observations = [make_observation("o0", devices=[device("cam", "CAM")])]
    for i in range(1, window + 5):
        observations.append(
            make_observation(f"o{i}", observed_at=T0 + timedelta(minutes=i), devices=[])
        )

    recon = reconcile("ring", observations, now=T0 + timedelta(minutes=window + 5))
    presence = _presence(recon, "cam")

    assert presence.status is Status.SOURCE_WITHDRAWN
    assert presence.rule_id == "rule:inventory_absent_at_or_above_threshold"
    # The explanation must not claim the device was never seen.
    assert "never" not in presence.explanation.lower()
    assert recon.counts["unknown"] == 0


def test_long_absence_reports_a_last_seen_time():
    """The withdrawal must be able to say when the device was last visible."""
    window = Policy().reconciliation_window
    observations = [make_observation("o0", devices=[device("cam", "CAM")])]
    for i in range(1, window + 3):
        observations.append(
            make_observation(f"o{i}", observed_at=T0 + timedelta(minutes=i), devices=[])
        )

    recon = reconcile("ring", observations, now=T0 + timedelta(minutes=window + 3))
    view = next(v for v in recon.devices if v.device_id == "cam")

    assert view.last_seen_at == T0
    assert view.presence.detail["last_seen_at"] is not None


def test_device_absent_only_within_the_window_is_not_a_withdrawal():
    """The window fix must not turn a genuinely short absence into a withdrawal."""
    obs1 = make_observation("o1", devices=[device("d1", "A")])
    obs2 = make_observation("o2", observed_at=T0 + timedelta(minutes=10), devices=[])
    recon = reconcile("ring", [obs1, obs2], now=T0 + timedelta(minutes=10))

    assert _presence(recon, "d1").status is Status.NOT_OBSERVED

