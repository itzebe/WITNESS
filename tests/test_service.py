"""End-to-end service tests: observe -> persist -> reconcile -> ledger.

These exercise the real store and the real service, using the simulated adapter
as the source. They are labelled clearly: nothing here claims a live Ring call.
The point is to prove the *pipeline* is sound -- evidence is durable before
conclusions are drawn, and every assessment in the ledger cites the observation
it rests on.
"""

from __future__ import annotations

import pytest

from witness.adapters.simulated import DEFAULT_SCENARIO, SimulatedAdapter
from witness.engine.status import Status
from witness.service import WitnessService
from witness.store import MAX_OBSERVATIONS_PER_SOURCE, Store


async def _walk(service, adapter, steps: int):
    results = []
    for _ in range(steps):
        results.append(await service.observe(adapter.source_id))
        if not adapter.advance():
            break
    return results


@pytest.fixture
def service(store):
    svc = WitnessService(store)
    svc.register(SimulatedAdapter())
    return svc


async def test_first_observation_produces_observed_devices(service):
    result = await service.observe("ring-simulated")
    assert result.reconciliation.counts["observed"] == 2
    assert result.ledger_appended > 0


async def test_observation_is_persisted_before_assessment(service):
    await service.observe("ring-simulated")
    stored = service.store.observations_for("ring-simulated")
    assert len(stored) == 1
    assert stored[0].inventory.ok is True


async def test_full_scenario_walks_the_taxonomy(service):
    """Drive the scripted scenario and assert the whole status taxonomy appears."""
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, len(DEFAULT_SCENARIO))

    recon = service.reconciliation_for("ring-simulated")
    statuses = {v.presence.status for v in recon.devices}
    assert Status.OBSERVED in statuses
    assert Status.SOURCE_WITHDRAWN in statuses

    limitation_statuses = {l.status for l in recon.limitations}
    assert Status.CLASSIFICATION_UNAVAILABLE in limitation_statuses


async def test_source_failure_frame_does_not_become_a_withdrawal(service):
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, 3)  # frames 0,1,2 (frame 2 is the failure)
    recon = service.reconciliation_for("ring-simulated")
    assert recon.latest_inventory_ok is False
    assert recon.source_assessment is not None
    assert recon.source_assessment.status is Status.SOURCE_UNAVAILABLE
    # No device may be reported as withdrawn on the basis of a failed request.
    assert all(v.presence.status is not Status.SOURCE_WITHDRAWN for v in recon.devices)


async def test_duplicate_and_late_events_are_surfaced(service):
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, len(DEFAULT_SCENARIO))
    recon = service.reconciliation_for("ring-simulated")
    event_statuses = {e.status for e in recon.events}
    assert Status.EVENT_DUPLICATE in event_statuses
    assert Status.EVENT_LATE in event_statuses


async def test_ledger_grows_and_verifies_after_every_run(service):
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, len(DEFAULT_SCENARIO))
    ledger = service.ledger()
    assert len(ledger) > 0
    assert ledger.verify().valid is True


async def test_every_assessment_in_the_ledger_cites_an_observation(service):
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, 2)
    ledger = service.ledger()
    assessments = [r for r in ledger.records if r.kind in {"assessment", "limitation"}]
    assert assessments, "expected at least one assessment record"
    for record in assessments:
        assert record.evidence_refs, f"{record.record_id} cites no evidence"
        assert record.rule_id and record.rule_id.startswith("rule:")


async def test_ledger_records_the_observation_before_the_assessment(service):
    await service.observe("ring-simulated")
    ledger = service.ledger()
    kinds = [r.kind for r in ledger.records]
    assert kinds[0] == "observation"
    assert "assessment" in kinds


async def test_reconciliation_is_reproducible_from_stored_observations(service):
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, 3)
    first = service.reconciliation_for("ring-simulated").to_dict()
    second = service.reconciliation_for("ring-simulated").to_dict()
    assert first["devices"] == second["devices"]
    assert first["counts"] == second["counts"]


async def test_overview_reports_the_policy_and_ledger_state(service):
    await service.observe("ring-simulated")
    overview = service.overview()
    assert overview["policy"]["withdrawal_threshold"] == 2
    assert overview["ledger"]["verification"]["valid"] is True
    assert overview["ledger"]["record_count"] > 0
    assert len(overview["sources"]) == 1


async def test_timeline_marks_source_kind(service):
    await service.observe("ring-simulated")
    entries = service.timeline("ring-simulated")
    assert len(entries) == 1
    assert entries[0]["source_kind"] == "simulated", "the timeline must label simulated data"


async def test_timeline_records_failures(service):
    adapter = service.get("ring-simulated")
    await _walk(service, adapter, 3)
    entries = service.timeline("ring-simulated")
    assert any(e["inventory_ok"] is False for e in entries)


async def test_unknown_source_raises(service):
    with pytest.raises(KeyError):
        await service.observe("does-not-exist")


async def test_unconfigured_live_source_yields_source_unavailable(store):
    from witness.adapters.ring import RingAdapter

    adapter = RingAdapter()
    adapter._token = None
    svc = WitnessService(store)
    svc.register(adapter)
    result = await svc.observe("ring")
    assert result.observation.inventory.ok is False
    assert result.reconciliation.source_assessment.status is Status.SOURCE_UNAVAILABLE
    assert result.reconciliation.devices == ()


# --------------------------------------------------------------------------
# Store behaviour
# --------------------------------------------------------------------------


def test_store_round_trips_an_observation(store):
    from tests.conftest import T0, device, make_observation

    obs = make_observation("o1", devices=[device("d1", "Front Door", encrypted=True)], events=[])
    store.save_observation(obs)
    loaded = store.observations_for("ring")
    assert len(loaded) == 1
    assert loaded[0].observation_id == "o1"
    assert loaded[0].devices[0].device_id == "d1"
    assert loaded[0].devices[0].e2e_encrypted is True
    assert loaded[0].inventory.ok is True


def test_store_preserves_the_failure_kind(store):
    from witness.engine.models import FailureKind
    from tests.conftest import make_observation

    obs = make_observation("o1", inventory_ok=False, inventory_failure=FailureKind.RATE_LIMITED)
    store.save_observation(obs)
    loaded = store.observations_for("ring")
    assert loaded[0].inventory.ok is False
    assert loaded[0].inventory.failure.kind is FailureKind.RATE_LIMITED


def test_store_returns_observations_oldest_first(store):
    from datetime import timedelta
    from tests.conftest import T0, device, make_observation

    for i in range(3):
        store.save_observation(
            make_observation(f"o{i}", observed_at=T0 + timedelta(minutes=i), devices=[device("d1", "A")])
        )
    ids = [o.observation_id for o in store.observations_for("ring")]
    assert ids == ["o0", "o1", "o2"]


def test_store_prunes_beyond_the_retention_limit(store):
    from datetime import timedelta
    from tests.conftest import T0, device, make_observation

    for i in range(MAX_OBSERVATIONS_PER_SOURCE + 5):
        store.save_observation(
            make_observation(f"o{i}", observed_at=T0 + timedelta(seconds=i), devices=[device("d1", "A")])
        )
    kept = store.observations_for("ring", limit=10_000)
    assert len(kept) == MAX_OBSERVATIONS_PER_SOURCE
    # Oldest pruned, newest retained.
    assert kept[-1].observation_id == f"o{MAX_OBSERVATIONS_PER_SOURCE + 4}"


def test_store_separates_sources(store):
    from tests.conftest import make_observation

    store.save_observation(make_observation("o1", source_id="ring"))
    store.save_observation(make_observation("o2", source_id="other"))
    assert store.known_source_ids() == ["other", "ring"]
    assert len(store.observations_for("ring")) == 1


def test_store_ledger_round_trips(tmp_path):
    from witness.engine.ledger import Ledger

    store = Store(path=str(tmp_path / "l.db"))
    ledger = Ledger()
    ledger.append(kind="observation", subject_id="s", payload={"a": 1})
    ledger.append(kind="assessment", subject_id="s", payload={"a": 2})
    store.save_ledger_records(ledger.records)

    restored = store.load_ledger()
    assert len(restored) == 2
    assert restored.verify().valid is True
    assert restored.head_hash == ledger.head_hash
    store.close()
