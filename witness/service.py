"""Application service: the one place that orchestrates a full WITNESS run.

Ordering matters and is fixed:

1. Collect one observation from the selected source (real I/O, failures contained).
2. Persist the observation (evidence before conclusions).
3. Append an evidence-ledger record for the observation itself.
4. Reconcile the full observation history into assessments.
5. Append a ledger record per assessment, citing the observation it rests on.

Because the ledger is appended after the observation is durable, every claim in
the ledger can be traced to stored evidence. Because reconciliation is pure, the
assessments in the ledger are reproducible from the stored observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .adapters.base import DEFAULT_HISTORY_LIMIT, SourceAdapter, collect_observation
from .engine.ledger import Ledger
from .engine.models import Observation, iso, utcnow
from .engine.reconcile import Reconciliation, reconcile
from .engine.rules import DEFAULT_POLICY, Policy
from .store import Store


@dataclass
class RunResult:
    """The outcome of one observe-and-reconcile run."""

    observation: Observation
    reconciliation: Reconciliation
    ledger_appended: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation.to_dict(),
            "reconciliation": self.reconciliation.to_dict(),
            "ledger_appended": self.ledger_appended,
        }


class WitnessService:
    """Owns the store, the ledger and the source registry."""

    def __init__(self, store: Store, policy: Policy = DEFAULT_POLICY) -> None:
        self.store = store
        self.policy = policy
        self._sources: dict[str, SourceAdapter] = {}

    # -- source registry ---------------------------------------------------

    def register(self, adapter: SourceAdapter) -> None:
        self._sources[adapter.source_id] = adapter

    def get(self, source_id: str) -> SourceAdapter | None:
        return self._sources.get(source_id)

    def list_sources(self) -> list[dict[str, Any]]:
        out = []
        for adapter in self._sources.values():
            configured = getattr(adapter, "configured", True)
            out.append(
                {
                    "source_id": adapter.source_id,
                    "display_name": adapter.display_name,
                    "source_kind": adapter.source_kind,
                    "configured": bool(configured),
                    "capabilities": adapter.capabilities(),
                    "is_simulated": adapter.source_kind == "simulated",
                }
            )
        return out

    # -- the run -----------------------------------------------------------

    async def observe(
        self, source_id: str, history_limit: int = DEFAULT_HISTORY_LIMIT
    ) -> RunResult:
        adapter = self._sources.get(source_id)
        if adapter is None:
            raise KeyError(f"unknown source: {source_id}")

        observation = await collect_observation(adapter, history_limit)
        self.store.save_observation(observation)

        ledger = self.store.load_ledger()
        before = len(ledger)

        # 1. Record the observation itself as evidence.
        ledger.append(
            kind="observation",
            subject_id=observation.source_id,
            payload=observation.to_dict(),
            rule_id=None,
            status=None,
            scope=f"observation:{observation.observation_id}",
            recorded_at=observation.observed_at,
        )

        # 2. Reconcile over the full history, oldest first.
        history = self.store.observations_for(source_id)
        reconciliation = reconcile(source_id, history, policy=self.policy)

        # 3. Record each assessment, citing the evidence it rests on.
        if reconciliation.source_assessment is not None:
            assessment = reconciliation.source_assessment
            ledger.append(
                kind="assessment",
                subject_id=assessment.subject_id,
                payload=assessment.to_dict(),
                rule_id=assessment.rule_id,
                status=assessment.status.value,
                scope=assessment.scope,
                evidence_refs=assessment.evidence_refs,
                recorded_at=observation.observed_at,
            )
        for view in reconciliation.devices:
            for assessment in (view.presence, view.content):
                ledger.append(
                    kind="assessment",
                    subject_id=assessment.subject_id,
                    payload=assessment.to_dict(),
                    rule_id=assessment.rule_id,
                    status=assessment.status.value,
                    scope=assessment.scope,
                    evidence_refs=assessment.evidence_refs,
                    recorded_at=observation.observed_at,
                )
        for assessment in reconciliation.events:
            ledger.append(
                kind="assessment",
                subject_id=assessment.subject_id,
                payload=assessment.to_dict(),
                rule_id=assessment.rule_id,
                status=assessment.status.value,
                scope=assessment.scope,
                evidence_refs=assessment.evidence_refs,
                recorded_at=observation.observed_at,
            )
        for assessment in reconciliation.limitations:
            ledger.append(
                kind="limitation",
                subject_id=assessment.subject_id,
                payload=assessment.to_dict(),
                rule_id=assessment.rule_id,
                status=assessment.status.value,
                scope=assessment.scope,
                evidence_refs=assessment.evidence_refs,
                recorded_at=observation.observed_at,
            )

        self.store.save_ledger_records(ledger.records[before:])

        return RunResult(
            observation=observation,
            reconciliation=reconciliation,
            ledger_appended=len(ledger) - before,
        )

    # -- read models -------------------------------------------------------

    def reconciliation_for(self, source_id: str) -> Reconciliation:
        return reconcile(source_id, self.store.observations_for(source_id), policy=self.policy)

    def ledger(self) -> Ledger:
        return self.store.load_ledger()

    def overview(self) -> dict[str, Any]:
        """The overview screen's data: what we can inspect, and what we know."""
        sources = self.list_sources()
        reconciliations = {s["source_id"]: self.reconciliation_for(s["source_id"]).to_dict() for s in sources}
        ledger = self.ledger()
        verification = ledger.verify()
        return {
            "generated_at": iso(utcnow()),
            "sources": sources,
            "reconciliations": reconciliations,
            "ledger": {
                "record_count": len(ledger),
                "head_hash": ledger.head_hash,
                "verification": verification.to_dict(),
            },
            "policy": {
                "withdrawal_threshold": self.policy.withdrawal_threshold,
                "event_lateness_threshold_seconds": int(self.policy.event_lateness_threshold.total_seconds()),
                "staleness_threshold_seconds": int(self.policy.staleness_threshold.total_seconds()),
                "reconciliation_window": self.policy.reconciliation_window,
            },
        }

    def timeline(self, source_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Chronological observations with their reconciliation outcome."""
        entries: list[dict[str, Any]] = []
        history = self.store.observations_for(source_id)
        for obs in history[-limit:]:
            entry: dict[str, Any] = {
                "observation_id": obs.observation_id,
                "observed_at": iso(obs.observed_at),
                "source_kind": obs.source_kind,
                "inventory_ok": obs.inventory.ok,
                "inventory_failure": obs.inventory.failure.to_dict() if obs.inventory.failure else None,
                "device_count": len(obs.devices),
                "event_count": len(obs.events),
                "device_ids": [d.device_id for d in obs.devices],
                "devices": [{"device_id": d.device_id, "device_label": d.device_label} for d in obs.devices],
            }
            entries.append(entry)
        return entries
