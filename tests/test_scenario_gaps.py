"""Regression tests for the four simulation-only incident scenarios.

These four scenarios cannot be produced by the Ring Developer Playground, which
exposes one device and emits no events, so they are exercised through the
deterministic engine and the real ``collect_observation`` pipeline. Nothing here
claims a live Ring call: the live evidence (three HTTP 200 endpoints) is recorded
in ``docs/SUBMISSION.md`` under VERIFIED LIVE and is unaffected by this file.

Organised by scenario rather than by module, to match the four gaps named in the
task that produced it:

1. Device withdrawal and disappearance.
2. Encryption / configuration transitions.
3. Duplicate events and idempotency.
4. Late-arriving and out-of-order events.

Each ``Regression:`` test pins a defect that was demonstrated against the real
pipeline before the fix, so it cannot silently return.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import T0, device, event, make_observation
from witness.engine.models import (
    EventRecord,
    FetchResult,
    Observation,
)
from witness.engine.reconcile import reconcile
from witness.engine.status import Status


def _presence(recon, device_id):
    for view in recon.devices:
        if view.device_id == device_id:
            return view.presence
    raise AssertionError(f"device {device_id} not in reconciliation")


def _view(recon, device_id):
    for view in recon.devices:
        if view.device_id == device_id:
            return view
    raise AssertionError(f"device {device_id} not in reconciliation")


# ==========================================================================
# Scenario 1: device withdrawal and disappearance
# ==========================================================================


def test_device_reappearing_after_withdrawal_is_observed_again():
    """A withdrawn device that returns must be reported present, not withdrawn.

    Presence is the current finding; the withdrawal was a true statement about
    the earlier passes and does not persist once the device is exposed again.
    """
    seq = [
        make_observation("o1", devices=[device("cam", "CAM")], observed_at=T0),
        make_observation("o2", devices=[], observed_at=T0 + timedelta(minutes=1)),
        make_observation("o3", devices=[], observed_at=T0 + timedelta(minutes=2)),
        make_observation("o4", devices=[device("cam", "CAM")], observed_at=T0 + timedelta(minutes=3)),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=3))

    assert _presence(recon, "cam").status is Status.OBSERVED
    assert recon.counts["withdrawn"] == 0


def test_first_and_last_seen_survive_a_withdrawal_and_return():
    """The device's history is preserved across absence and reappearance."""
    seq = [
        make_observation("o1", devices=[device("cam", "CAM")], observed_at=T0),
        make_observation("o2", devices=[], observed_at=T0 + timedelta(minutes=1)),
        make_observation("o3", devices=[], observed_at=T0 + timedelta(minutes=2)),
        make_observation("o4", devices=[device("cam", "CAM")], observed_at=T0 + timedelta(minutes=3)),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=3))
    view = _view(recon, "cam")

    assert view.first_seen_at == T0
    assert view.last_seen_at == T0 + timedelta(minutes=3)
    # Three successful inventories carried the device: o1, o4 and... no others.
    assert view.observation_count == 2


def test_absence_streak_resets_when_the_device_returns():
    """A reappearance clears the streak; the count is consecutive, not total."""
    seq = [
        make_observation("o1", devices=[device("cam", "CAM")], observed_at=T0),
        make_observation("o2", devices=[], observed_at=T0 + timedelta(minutes=1)),
        make_observation("o3", devices=[device("cam", "CAM")], observed_at=T0 + timedelta(minutes=2)),
        make_observation("o4", devices=[], observed_at=T0 + timedelta(minutes=3)),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=3))
    presence = _presence(recon, "cam")

    assert presence.status is Status.NOT_OBSERVED
    assert presence.detail["consecutive_absences"] == 1


def test_failed_inventory_is_never_reported_as_a_removal():
    """A failed request must not be read as the device being removed.

    The latest pass is the failure, so no device-level conclusion is available
    now: the device is SOURCE_UNAVAILABLE, and the withdrawal count stays zero.
    The failure contributes nothing to the absence streak.
    """
    seq = [
        make_observation("o1", devices=[device("cam", "CAM")], observed_at=T0),
        make_observation("o2", devices=[], observed_at=T0 + timedelta(minutes=1)),
        make_observation(
            "o3", devices=[], observed_at=T0 + timedelta(minutes=2), inventory_ok=False
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=2))
    presence = _presence(recon, "cam")

    assert presence.status is Status.SOURCE_UNAVAILABLE
    assert recon.counts["withdrawn"] == 0
    assert "not evidence about the device" in presence.explanation


def test_a_device_never_seen_is_never_called_withdrawn():
    """Only a device with a prior sighting can be described as withdrawn."""
    seq = [make_observation(f"o{i}", devices=[], observed_at=T0 + timedelta(minutes=i)) for i in range(5)]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=5))

    assert recon.devices == ()
    assert recon.counts["withdrawn"] == 0
    assert any(l.status is Status.INSUFFICIENT_EVIDENCE for l in recon.limitations)


# ==========================================================================
# Scenario 2: encryption and configuration transitions
# ==========================================================================


def test_identical_consecutive_configurations_do_not_assert_a_transition():
    """Two identical readings produce a current state, never a transition."""
    seq = [
        make_observation("o1", devices=[device("cam", "CAM", encrypted=False)], observed_at=T0),
        make_observation(
            "o2",
            devices=[device("cam", "CAM", encrypted=False)],
            observed_at=T0 + timedelta(minutes=5),
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=5))
    content = _view(recon, "cam").content

    assert content.status is Status.CONTENT_VISIBLE
    assert "transition" not in content.rule_id.lower()
    assert "transition" not in content.status.value.lower()


def test_first_observation_with_no_baseline_reports_current_state_only():
    """With no prior reading there is nothing to compare, so none is claimed."""
    obs = make_observation("o1", devices=[device("cam", "CAM", encrypted=True)], observed_at=T0)
    recon = reconcile("ring", [obs], now=T0)
    content = _view(recon, "cam").content

    assert content.status is Status.SOURCE_ENCRYPTED
    assert content.evidence_refs == ("o1",)


def test_a_changed_configuration_reports_the_new_state_not_a_transition():
    """A true change is reported as the current reading, not as a delta."""
    seq = [
        make_observation("o1", devices=[device("cam", "CAM", encrypted=True)], observed_at=T0),
        make_observation(
            "o2",
            devices=[device("cam", "CAM", encrypted=False)],
            observed_at=T0 + timedelta(minutes=5),
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=5))
    content = _view(recon, "cam").content

    assert content.status is Status.CONTENT_VISIBLE
    assert content.detail["e2e_encrypted"] is False


def test_configuration_reading_survives_a_device_leaving_the_real_pipeline():
    """Regression: the reading must survive a device leaving the inventory.

    The earlier version of this guarantee only held for a fixture in which the
    later observation still carried a configurations response. The real
    ``collect_observation`` never requests configurations when the inventory is
    empty, so the latest observation has ``configurations=None`` -- and the
    content rule, reading that, reported CONTENT_UNAVAILABLE. A reading WITNESS
    took while the device was still inspected was discarded by the pass that
    could not see it. The rule now reads the newest pass that actually queried
    the device.
    """
    first = Observation(
        observation_id="o1",
        source_id="ring",
        source_kind="live",
        observed_at=T0,
        inventory=FetchResult.success("inv", [], observed_at=T0),
        history=FetchResult.success("hist", [], observed_at=T0),
        configurations=FetchResult.success(
            "cfg", {"e2e_encryption": {"enabled": True}}, observed_at=T0
        ),
        devices=(device("cam", "CAM", encrypted=True),),
        events=(),
        inspected_device_id="cam",
    )
    # Shape the real pipeline produces for an empty inventory: no configurations
    # request was made, so configurations is None and inspected_device_id is None.
    later = Observation(
        observation_id="o2",
        source_id="ring",
        source_kind="live",
        observed_at=T0 + timedelta(minutes=10),
        inventory=FetchResult.success("inv", [], observed_at=T0 + timedelta(minutes=10)),
        history=None,
        configurations=None,
        devices=(),
        events=(),
        inspected_device_id=None,
    )
    recon = reconcile("ring", [first, later], now=T0 + timedelta(minutes=10))
    content = _view(recon, "cam").content

    assert content.status is Status.SOURCE_ENCRYPTED
    assert content.detail["e2e_encrypted"] is True
    # Dated to the pass that took the reading, not the pass that could not see it.
    assert "2026-10-20T12:00:00Z" in content.explanation
    assert "2026-10-20T12:10:00Z" not in content.explanation


def test_content_assessment_cites_the_observation_that_took_the_reading():
    """Regression: the evidence link must point at the reading, not the latest pass.

    When a device stops being the inspected one, its encryption reading is
    retained from an earlier pass. The content assessment used to cite the
    latest observation regardless -- which had inspected a *different* device
    and never queried this one. The ledger trace then pointed at evidence that
    could not support the claim.
    """
    first = make_observation(
        "o1",
        devices=[device("a", "Alpha", encrypted=True), device("b", "Beta")],
        observed_at=T0,
    )
    second = make_observation(
        "o2",
        devices=[device("b", "Beta", encrypted=False)],
        observed_at=T0 + timedelta(minutes=5),
    )
    recon = reconcile("ring", [first, second], now=T0 + timedelta(minutes=5))

    alpha = _view(recon, "a").content
    assert alpha.status is Status.SOURCE_ENCRYPTED
    # Alpha was queried in o1 only; the assessment must cite o1.
    assert alpha.evidence_refs == ("o1",)
    assert "2026-10-20T12:00:00Z" in alpha.explanation


def test_content_assessment_never_cites_a_pass_that_did_not_query_the_device():
    """Every content evidence ref must name an observation that inspected it."""
    seq = [
        make_observation(
            "o1",
            devices=[device("a", "Alpha", encrypted=True), device("b", "Beta")],
            observed_at=T0,
        ),
        make_observation(
            "o2",
            devices=[device("b", "Beta", encrypted=False), device("a", "Alpha")],
            observed_at=T0 + timedelta(minutes=5),
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=5))
    by_id = {o.observation_id: o for o in seq}

    for view in recon.devices:
        for ref in view.content.evidence_refs:
            assert by_id[ref].inspected_device_id == view.device_id, (
                f"{view.device_id} cites {ref}, which inspected "
                f"{by_id[ref].inspected_device_id}"
            )


def test_a_device_that_was_never_inspected_reports_not_asked():
    """A non-primary device is 'not asked', which is not 'asked and absent'."""
    obs = make_observation(
        "o1",
        devices=[
            device("a", "Alpha", encrypted=None, stated=False),
            device("b", "Beta", encrypted=None, stated=False),
        ],
    )
    recon = reconcile("ring", [obs], now=T0)
    beta = _view(recon, "b").content

    assert beta.status is Status.CONTENT_UNAVAILABLE
    assert beta.detail["configurations_attempted"] is False
    assert "did not request configurations" in beta.explanation.lower()


def test_failed_configurations_supports_no_configuration_claim():
    """A configurations outage yields unknown, in both directions."""
    obs = make_observation(
        "o1", devices=[device("cam", "CAM")], configurations_ok=False, observed_at=T0
    )
    recon = reconcile("ring", [obs], now=T0)
    content = _view(recon, "cam").content

    assert content.status is Status.CONTENT_UNAVAILABLE
    assert content.detail["configurations_ok"] is False


def test_partial_configurations_response_does_not_deny_the_field():
    """A response missing the encryption key is unknown, not 'unencrypted'."""
    obs = make_observation(
        "o1", devices=[device("cam", "CAM", encrypted=None, stated=False)], observed_at=T0
    )
    recon = reconcile("ring", [obs], now=T0)
    content = _view(recon, "cam").content

    assert content.status is Status.CONTENT_UNAVAILABLE
    assert content.detail["encryption_field_present"] is False


# ==========================================================================
# Scenario 3: duplicate events and idempotency
# ==========================================================================


def test_same_event_in_one_response_is_counted_once():
    ev = event("e1", reported_at=T0, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev, ev])
    recon = reconcile("ring", [obs], now=T0)

    assert len(recon.events) == 1
    assert recon.events[0].status is Status.EVENT_DUPLICATE
    assert recon.events[0].detail["duplicate_count"] == 2


def test_repeated_polling_does_not_create_duplicate_incidents():
    """The same event returned by two polls is one finding, not two.

    Reconciliation reports the latest pass's events, and the latest pass carried
    the event once, so no duplicate is claimed across passes. The audit trail of
    both passes remains in the observation history.
    """
    seq = []
    for i in range(3):
        t = T0 + timedelta(minutes=5 * i)
        ev = EventRecord(event_id="e1", event_type="motion", reported_at=T0, retrieved_at=t)
        seq.append(make_observation(f"o{i}", devices=[device("d1", "A")], events=[ev], observed_at=t))
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=10))

    assert len(recon.events) == 1
    assert recon.events[0].detail["duplicate_count"] == 1


def test_a_retry_after_a_failed_history_does_not_double_report():
    """A failed poll followed by a successful one yields one event finding."""
    failed = make_observation(
        "o1", devices=[device("d1", "A")], events=[], history_ok=False, observed_at=T0
    )
    ok = make_observation(
        "o2",
        devices=[device("d1", "A")],
        events=[event("e1", reported_at=T0, retrieved_at=T0 + timedelta(minutes=5))],
        observed_at=T0 + timedelta(minutes=5),
    )
    recon = reconcile("ring", [failed, ok], now=T0 + timedelta(minutes=5))

    assert len(recon.events) == 1
    assert recon.events[0].subject_id == "e1"


def test_distinct_ids_with_identical_content_are_not_collapsed():
    """Two ids are two events even when every other field matches."""
    a = event("e1", event_type="motion", reported_at=T0, retrieved_at=T0)
    b = event("e2", event_type="motion", reported_at=T0, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[a, b])
    recon = reconcile("ring", [obs], now=T0)

    assert {e.subject_id for e in recon.events} == {"e1", "e2"}
    assert all(e.status is Status.EVENT_OBSERVED for e in recon.events)


def test_records_without_ids_merge_only_on_type_and_time():
    """The documented fallback identity is (event type, source time)."""
    same = [
        EventRecord(event_id="", event_type="motion", reported_at=T0, retrieved_at=T0),
        EventRecord(event_id="", event_type="motion", reported_at=T0, retrieved_at=T0),
    ]
    different_time = EventRecord(
        event_id="", event_type="motion", reported_at=T0 + timedelta(minutes=1), retrieved_at=T0
    )
    obs = make_observation("o1", devices=[device("d1", "A")], events=same + [different_time])
    recon = reconcile("ring", [obs], now=T0)

    statuses = sorted(e.status.value for e in recon.events)
    assert statuses == ["EVENT_DUPLICATE", "EVENT_OBSERVED"]


def test_duplicate_does_not_claim_only_one_real_world_event_occurred():
    """The duplicate wording must not assert a count of real-world events."""
    ev = event("e1", reported_at=T0, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev, ev])
    recon = reconcile("ring", [obs], now=T0)

    text = recon.events[0].explanation.lower()
    assert "do not prove a single real-world event" in text


# ==========================================================================
# Scenario 4: late-arriving and out-of-order events
# ==========================================================================


def test_on_time_event_redelivered_later_is_not_reported_late():
    """Regression: lateness is measured from the first retrieval, not the latest.

    An event's ``retrieved_at`` is stamped when its response arrives, so a
    re-delivery on a later poll overwrites it with a later time. Lateness was
    computed from that value, so an event first seen on time was reported
    EVENT_LATE with a delay equal to the polling interval -- and the explanation
    asserted a "first retrieved at" time that was not the first retrieval.
    """
    on_time = EventRecord(event_id="e1", event_type="motion", reported_at=T0, retrieved_at=T0)
    redelivered = EventRecord(
        event_id="e1",
        event_type="motion",
        reported_at=T0,
        retrieved_at=T0 + timedelta(minutes=30),
    )
    seq = [
        make_observation("o1", devices=[device("d1", "A")], events=[on_time], observed_at=T0),
        make_observation(
            "o2",
            devices=[device("d1", "A")],
            events=[redelivered],
            observed_at=T0 + timedelta(minutes=30),
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=30))

    assert recon.events[0].status is Status.EVENT_OBSERVED
    assert recon.events[0].detail["first_retrieved_at"] == "2026-10-20T12:00:00Z"


def test_truly_late_event_keeps_its_first_retrieval_delay():
    """A late event re-delivered later keeps the delay from when it was first seen."""
    late = EventRecord(
        event_id="e9", event_type="motion", reported_at=T0 - timedelta(hours=2), retrieved_at=T0
    )
    redelivered = EventRecord(
        event_id="e9",
        event_type="motion",
        reported_at=T0 - timedelta(hours=2),
        retrieved_at=T0 + timedelta(minutes=30),
    )
    seq = [
        make_observation("o1", devices=[device("d1", "A")], events=[late], observed_at=T0),
        make_observation(
            "o2",
            devices=[device("d1", "A")],
            events=[redelivered],
            observed_at=T0 + timedelta(minutes=30),
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=30))
    assessment = recon.events[0]

    assert assessment.status is Status.EVENT_LATE
    assert assessment.detail["first_retrieved_at"] == "2026-10-20T12:00:00Z"
    # 2h, measured from the first retrieval -- not 2h30m from the re-delivery.
    assert "120m 0s" in assessment.explanation


def test_late_event_arriving_after_a_newer_event_is_still_flagged():
    """Arrival order does not decide lateness; the source time does."""
    newer = event("e2", reported_at=T0, retrieved_at=T0)
    older = event("e1", reported_at=T0 - timedelta(hours=3), retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[newer, older])
    recon = reconcile("ring", [obs], now=T0)

    by_id = {a.subject_id: a.status for a in recon.events}
    assert by_id["e2"] is Status.EVENT_OBSERVED
    assert by_id["e1"] is Status.EVENT_LATE


def test_late_event_arriving_in_a_later_poll_is_flagged():
    """An event delivered in a later poll than a newer one is judged on its time."""
    newer = event("e2", reported_at=T0, retrieved_at=T0)
    older = event(
        "e1", reported_at=T0 - timedelta(hours=3), retrieved_at=T0 + timedelta(minutes=5)
    )
    seq = [
        make_observation("o1", devices=[device("d1", "A")], events=[newer], observed_at=T0),
        make_observation(
            "o2",
            devices=[device("d1", "A")],
            events=[older],
            observed_at=T0 + timedelta(minutes=5),
        ),
    ]
    recon = reconcile("ring", seq, now=T0 + timedelta(minutes=5))

    assert recon.events[0].subject_id == "e1"
    assert recon.events[0].status is Status.EVENT_LATE


def test_equal_timestamps_are_not_late():
    """Two events at the same instant are neither late nor out of order."""
    a = event("e1", reported_at=T0, retrieved_at=T0)
    b = event("e2", reported_at=T0, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[a, b])
    recon = reconcile("ring", [obs], now=T0)

    assert all(e.status is Status.EVENT_OBSERVED for e in recon.events)


def test_missing_timestamp_is_never_claimed_late():
    """With no source time there is no interval, so no lateness claim."""
    unknown = EventRecord(event_id="e1", event_type="motion", reported_at=None, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[unknown])
    recon = reconcile("ring", [obs], now=T0)

    assert recon.events[0].status is Status.EVENT_OBSERVED
    assert recon.events[0].detail["reported_at"] is None


def test_repeated_delivery_of_a_late_event_reports_it_once():
    """A late event delivered twice is one finding, and duplicate outranks late."""
    late = EventRecord(
        event_id="e9", event_type="motion", reported_at=T0 - timedelta(hours=2), retrieved_at=T0
    )
    obs = make_observation("o1", devices=[device("d1", "A")], events=[late, late])
    recon = reconcile("ring", [obs], now=T0)

    assert len(recon.events) == 1
    assert recon.events[0].status is Status.EVENT_DUPLICATE
    # The lateness information is still in the payload even though the status
    # reports the duplicate, so no observation is lost.
    assert recon.events[0].detail["reported_at"] == "2026-10-20T10:00:00Z"
    assert recon.events[0].detail["first_retrieved_at"] == "2026-10-20T12:00:00Z"


def test_timeline_reconstruction_after_late_evidence_arrives():
    """Reconciling a longer history must not lose the earlier event findings."""
    first = make_observation(
        "o1",
        devices=[device("d1", "A")],
        events=[event("e1", reported_at=T0, retrieved_at=T0)],
        observed_at=T0,
    )
    early = reconcile("ring", [first], now=T0)
    assert [a.subject_id for a in early.events] == ["e1"]

    second = make_observation(
        "o2",
        devices=[device("d1", "A")],
        events=[event("e2", reported_at=T0 - timedelta(hours=2), retrieved_at=T0 + timedelta(minutes=5))],
        observed_at=T0 + timedelta(minutes=5),
    )
    later = reconcile("ring", [first, second], now=T0 + timedelta(minutes=5))

    # The later reconciliation reports the newest pass's events; the earlier
    # event remains in the observation history, which is what the timeline shows.
    assert [a.subject_id for a in later.events] == ["e2"]
    assert later.events[0].status is Status.EVENT_LATE


def test_event_assessment_cites_the_observation_that_carried_it():
    """An event finding must cite the history response it was read from."""
    ev = event("e1", reported_at=T0, retrieved_at=T0)
    obs = make_observation("o1", devices=[device("d1", "A")], events=[ev])
    recon = reconcile("ring", [obs], now=T0)

    assert recon.events[0].evidence_refs == ("o1",)


# ==========================================================================
# Persistence: the fields the fixes depend on must survive a store round-trip
# ==========================================================================


def test_inspected_device_and_reading_survive_the_store(store):
    """Regression guard: the content fix depends on two stored fields.

    ``_encryption_reading`` selects the pass that inspected the device by
    ``inspected_device_id`` and reads ``encryption_stated``/``e2e_encrypted``
    from that pass's device record. If the store dropped any of them, the
    reconciliation would silently fall back to "not asked" after a restart and
    the defect would reappear only in the persisted path.
    """
    obs = make_observation(
        "o1", devices=[device("cam", "CAM", encrypted=True)], observed_at=T0
    )
    store.save_observation(obs)
    restored = store.observations_for("ring")[0]

    assert restored.inspected_device_id == "cam"
    assert restored.devices[0].encryption_stated is True
    assert restored.devices[0].e2e_encrypted is True

    recon = reconcile("ring", [restored], now=T0)
    assert _view(recon, "cam").content.status is Status.SOURCE_ENCRYPTED
    assert _view(recon, "cam").content.evidence_refs == ("o1",)


def test_first_retrieval_survives_the_store(store):
    """Regression guard: lateness depends on the stored retrieval time."""
    on_time = event("e1", reported_at=T0, retrieved_at=T0)
    redelivered = event("e1", reported_at=T0, retrieved_at=T0 + timedelta(minutes=30))
    store.save_observation(
        make_observation("o1", devices=[device("d1", "A")], events=[on_time], observed_at=T0)
    )
    store.save_observation(
        make_observation(
            "o2",
            devices=[device("d1", "A")],
            events=[redelivered],
            observed_at=T0 + timedelta(minutes=30),
        )
    )
    restored = store.observations_for("ring")
    recon = reconcile("ring", restored, now=T0 + timedelta(minutes=30))

    assert recon.events[0].status is Status.EVENT_OBSERVED
    assert recon.events[0].detail["first_retrieved_at"] == "2026-10-20T12:00:00Z"
