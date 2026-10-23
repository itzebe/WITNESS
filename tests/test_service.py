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


def test_store_all_observations_spans_every_source(tmp_path):
    """The cross-source view must be ordered oldest-first, like the per-source one."""
    from datetime import timedelta

    from tests.conftest import T0, device, make_observation

    store = Store(path=str(tmp_path / "all.db"))
    store.save_observation(
        make_observation("o1", source_id="ring", observed_at=T0, devices=[device("d1", "A")])
    )
    store.save_observation(
        make_observation(
            "o2", source_id="other", observed_at=T0 + timedelta(minutes=5), devices=[device("x", "X")]
        )
    )
    store.save_observation(
        make_observation(
            "o3", source_id="ring", observed_at=T0 + timedelta(minutes=10), devices=[device("d1", "A")]
        )
    )

    everything = store.all_observations()
    assert [o.observation_id for o in everything] == ["o1", "o2", "o3"]
    assert store.all_observations(limit=2) and len(store.all_observations(limit=2)) == 2
    store.close()


def test_store_all_observations_is_empty_before_anything_is_saved(tmp_path):
    store = Store(path=str(tmp_path / "empty.db"))
    assert store.all_observations() == []
    assert store.known_source_ids() == []
    store.close()


def test_store_reset_ledger_clears_records_but_keeps_observations(tmp_path):
    """Resetting the ledger must not silently discard the evidence it attests to."""
    from witness.engine.ledger import Ledger

    from tests.conftest import make_observation

    store = Store(path=str(tmp_path / "reset.db"))
    store.save_observation(make_observation("o1"))
    ledger = Ledger()
    ledger.append(kind="observation", subject_id="o1", payload={"a": 1})
    store.save_ledger_records(ledger.records)
    assert len(store.load_ledger()) == 1

    store.reset_ledger()
    assert store.load_ledger().records == ()
    # The observations are untouched: the ledger is the attestation, not the data.
    assert len(store.observations_for("ring")) == 1
    store.close()


def test_store_ledger_restores_after_a_reopen(tmp_path):
    """Evidence must survive a process restart, or the ledger proves nothing."""
    from witness.engine.ledger import Ledger

    path = str(tmp_path / "persist.db")
    store = Store(path=path)
    ledger = Ledger()
    ledger.append(kind="observation", subject_id="s", payload={"a": 1})
    ledger.append(kind="assessment", subject_id="s", payload={"a": 2})
    store.save_ledger_records(ledger.records)
    head = ledger.head_hash
    store.close()

    reopened = Store(path=path)
    restored = reopened.load_ledger()
    assert restored.verify().valid is True
    assert restored.head_hash == head
    reopened.close()


def test_observation_retention_keeps_the_newest_per_source(tmp_path):
    """Retention is bounded per source: the newest 200 survive, oldest pruned.

    The README and EVIDENCE_MATRIX describe this boundary; this pins it. It also
    checks the two properties that are easy to get wrong when pruning is
    implemented as a single global query: one source's overflow must not delete
    another source's observations, and the ledger -- the attestation, not the
    data -- must not be treated as subject to the observation bound.
    """
    from datetime import timedelta

    from tests.conftest import T0, make_observation

    store = Store(path=str(tmp_path / "retention.db"))

    # 225 observations for "ring", strictly increasing in time, and 3 for a
    # second source that must be left alone.
    total = MAX_OBSERVATIONS_PER_SOURCE + 25
    for i in range(total):
        store.save_observation(
            make_observation(f"o{i:03d}", source_id="ring", observed_at=T0 + timedelta(minutes=i))
        )
    for i in range(3):
        store.save_observation(
            make_observation(f"sim{i}", source_id="ring-simulated", observed_at=T0 + timedelta(minutes=i))
        )

    retained = store.observations_for("ring", limit=total + 10)
    assert len(retained) == MAX_OBSERVATIONS_PER_SOURCE == 200

    # The newest 200 remain: the 25 oldest are gone, and the surviving window
    # starts exactly where the bound says it should.
    ids = [o.observation_id for o in retained]
    assert ids[0] == "o025"
    assert ids[-1] == f"o{total - 1:03d}"
    assert "o000" not in ids and "o024" not in ids

    # Retention is scoped to the source: the other source is untouched.
    assert len(store.observations_for("ring-simulated", limit=10)) == 3

    # The ledger is not the observation history and is not pruned with it.
    from witness.engine.ledger import Ledger

    ledger = Ledger()
    for i in range(5):
        ledger.append(kind="observation", subject_id="ring", payload={"i": i})
    store.save_ledger_records(ledger.records)
    assert len(store.load_ledger().records) == 5
    # Another overflow on "ring" must not touch the ledger either.
    store.save_observation(
        make_observation("extra", source_id="ring", observed_at=T0 + timedelta(minutes=total + 1))
    )
    assert len(store.load_ledger().records) == 5
    store.close()

