"""The Visibility Reconciliation Engine.

This is the product's core. It takes the full ordered history of observations
for one source and produces, for every device it has ever seen, a current
evidence-based assessment plus the supporting timeline.

It is deterministic: same observations in, same assessments out. It performs no
I/O. That property is what the test suite pins and what lets WITNESS describe
its output as reproducible rather than generated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from .models import Assessment, DeviceRecord, Observation, iso, utcnow
from .rules import (
    DEFAULT_POLICY,
    Policy,
    rule_classification_availability,
    rule_content_state,
    rule_event_assessments,
    rule_inventory_absent,
    rule_inventory_present,
    rule_insufficient_evidence,
    rule_source_request_failed,
    rule_staleness,
    rule_unknown,
)
from .status import Status


@dataclass(frozen=True)
class DeviceView:
    """Everything WITNESS knows and does not know about one device."""

    device_id: str
    device_label: str
    presence: Assessment
    content: Assessment
    staleness: dict[str, Any]
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    observation_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_label": self.device_label,
            "presence": self.presence.to_dict(),
            "content": self.content.to_dict(),
            "staleness": self.staleness,
            "first_seen_at": iso(self.first_seen_at),
            "last_seen_at": iso(self.last_seen_at),
            "observation_count": self.observation_count,
        }


@dataclass(frozen=True)
class Reconciliation:
    """The complete reconciled state of one source."""

    source_id: str
    source_kind: str
    generated_at: datetime
    latest_observation_at: datetime | None
    latest_inventory_ok: bool
    devices: tuple[DeviceView, ...]
    events: tuple[Assessment, ...]
    limitations: tuple[Assessment, ...]
    source_assessment: Assessment | None
    staleness: dict[str, Any]
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "generated_at": iso(self.generated_at),
            "latest_observation_at": iso(self.latest_observation_at),
            "latest_inventory_ok": self.latest_inventory_ok,
            "devices": [d.to_dict() for d in self.devices],
            "events": [e.to_dict() for e in self.events],
            "limitations": [l.to_dict() for l in self.limitations],
            "source_assessment": self.source_assessment.to_dict() if self.source_assessment else None,
            "staleness": self.staleness,
            "counts": self.counts,
        }


def _all_device_ids(observations: Sequence[Observation]) -> dict[str, DeviceRecord]:
    """Every device id ever returned by a successful inventory, with its latest record."""
    found: dict[str, DeviceRecord] = {}
    for obs in observations:
        if not obs.inventory.ok:
            continue
        for device in obs.devices:
            found[device.device_id] = device
    return found


def _encryption_reading(
    observations: Sequence[Observation],
    device_id: str,
    fallback_record: DeviceRecord,
) -> tuple[Observation | None, DeviceRecord]:
    """The newest pass that actually queried this device's configurations.

    A content claim has to come from one pass: the request that was made, the
    response that carried it, the device record read in it, and the time it was
    taken. Reading the encryption *value* from one observation while testing
    whether the request was *made* against another produces sentences that are
    true of neither.

    The record for a device outlives the observation that read its
    configurations -- a device that has left the inventory keeps the reading
    taken while it was still inspected -- so the newest inspection is not
    necessarily the latest observation. Returns ``(obs, record)`` from that
    pass, or ``(None, fallback_record)`` when this device was never inspected,
    which the content rule reports as "not asked".
    """
    for obs in reversed(observations):
        if obs.inspected_device_id != device_id:
            continue
        record = next((d for d in obs.devices if d.device_id == device_id), None)
        if record is not None:
            return obs, record
    return None, fallback_record


def _first_retrieval_times(observations: Sequence[Observation]) -> dict[str, datetime]:
    """Earliest retrieval time seen for each event identity, across all passes.

    An event's ``retrieved_at`` is stamped when its response was received, so a
    re-delivery on a later poll overwrites it with a later time. Lateness is a
    claim about the first time WITNESS saw the event, so it has to be computed
    against the earliest retrieval, not the most recent one.
    """
    first: dict[str, datetime] = {}
    for obs in observations:
        if obs.history is None or not obs.history.ok:
            continue
        for event in obs.events:
            key = event.identity_key()
            prev = first.get(key)
            if prev is None or event.retrieved_at < prev:
                first[key] = event.retrieved_at
    return first


def reconcile(
    source_id: str,
    observations: Sequence[Observation],
    now: datetime | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> Reconciliation:
    """Reconcile an ordered observation history into current assessments.

    Args:
        source_id: the source these observations belong to.
        observations: ordered oldest-first. Order matters and is not inferred.
        now: injectable clock, for deterministic tests.
        policy: explicit thresholds.

    Returns:
        A Reconciliation covering every device ever observed, plus the latest
        events, plus every limitation WITNESS is currently aware of.
    """
    now = now or utcnow()
    obs_list = [o for o in observations if o.source_id == source_id]
    source_kind = obs_list[-1].source_kind if obs_list else "unknown"

    if not obs_list:
        empty = Reconciliation(
            source_id=source_id,
            source_kind=source_kind,
            generated_at=now,
            latest_observation_at=None,
            latest_inventory_ok=False,
            devices=(),
            events=(),
            limitations=(),
            source_assessment=None,
            staleness={"age_seconds": None, "stale": True, "staleness_threshold_seconds": int(policy.staleness_threshold.total_seconds())},
            counts={"observed": 0, "not_observed": 0, "withdrawn": 0, "unavailable": 0,
                    "unknown": 0, "encrypted": 0, "limitations": 0, "total_devices": 0},
        )
        return empty

    latest = obs_list[-1]
    source_assessment = rule_source_request_failed(latest, now)

    known_devices = _all_device_ids(obs_list)
    views: list[DeviceView] = []
    seen_first: dict[str, datetime] = {}
    seen_last: dict[str, datetime] = {}
    seen_count: dict[str, int] = {}
    for obs in obs_list:
        if not obs.inventory.ok:
            continue
        for device in obs.devices:
            seen_first.setdefault(device.device_id, obs.observed_at)
            seen_last[device.device_id] = obs.observed_at
            seen_count[device.device_id] = seen_count.get(device.device_id, 0) + 1

    for device_id, record in sorted(known_devices.items(), key=lambda kv: kv[1].device_label.lower()):
        # Pass the full history including the latest observation: the current
        # absence is itself one of the consecutive absences being counted.
        prior = obs_list
        presence = (
            rule_inventory_present(latest, record)
            or rule_inventory_absent(latest, device_id, record.device_label, prior, policy)
        )
        if presence is None:
            # The latest observation was a failure, or the device was absent and
            # never previously seen. Fall back honestly.
            if source_assessment is not None:
                presence = Assessment(
                    status=Status.SOURCE_UNAVAILABLE,
                    subject_type="device",
                    subject_id=device_id,
                    subject_label=record.device_label,
                    rule_id="rule:source_request_failed",
                    explanation=(
                        f"WITNESS could not inspect {record.device_label} at "
                        f"{iso(latest.observed_at)} because the inventory request failed. "
                        f"Last successful sighting: {iso(seen_last.get(device_id)) or 'never'}. "
                        "A failed request is not evidence about the device."
                    ),
                    observed_at=latest.observed_at,
                    scope=f"inventory:{source_id}",
                    evidence_refs=(latest.observation_id,),
                    detail={"failure": latest.inventory.failure.to_dict() if latest.inventory.failure else None},
                    axis="presence",
                )
            else:
                presence = rule_unknown(device_id, record.device_label)

        reading_obs, reading_record = _encryption_reading(obs_list, device_id, record)
        views.append(
            DeviceView(
                device_id=device_id,
                device_label=record.device_label,
                presence=presence,
                content=rule_content_state(reading_obs, reading_record, latest),
                staleness=rule_staleness(presence, now, policy),
                first_seen_at=seen_first.get(device_id),
                last_seen_at=seen_last.get(device_id),
                observation_count=seen_count.get(device_id, 0),
            )
        )

    events = (
        tuple(rule_event_assessments(latest, policy, _first_retrieval_times(obs_list)))
        if latest.history and latest.history.ok
        else ()
    )

    limitations: list[Assessment] = []
    if latest.history is not None and latest.history.ok and latest.events:
        cls = rule_classification_availability(latest, "what was the event?")
        if cls is not None:
            limitations.append(cls)
    if latest.inventory.ok and not known_devices:
        limitations.append(
            rule_insufficient_evidence(
                latest,
                "what is connected to this source?",
                "The inventory request succeeded but returned no devices, and WITNESS has "
                "no earlier successful inventory to compare against.",
            )
        )

    counts = {
        "observed": sum(1 for v in views if v.presence.status == Status.OBSERVED),
        "not_observed": sum(1 for v in views if v.presence.status == Status.NOT_OBSERVED),
        "withdrawn": sum(1 for v in views if v.presence.status == Status.SOURCE_WITHDRAWN),
        "unavailable": sum(1 for v in views if v.presence.status == Status.SOURCE_UNAVAILABLE),
        "unknown": sum(1 for v in views if v.presence.status == Status.UNKNOWN),
        "encrypted": sum(1 for v in views if v.content.status == Status.SOURCE_ENCRYPTED),
        "limitations": len(limitations),
        "total_devices": len(views),
    }

    return Reconciliation(
        source_id=source_id,
        source_kind=source_kind,
        generated_at=now,
        latest_observation_at=latest.observed_at,
        latest_inventory_ok=latest.inventory.ok,
        devices=tuple(views),
        events=events,
        limitations=tuple(limitations),
        source_assessment=source_assessment,
        staleness=rule_staleness(
            Assessment(
                status=Status.OBSERVED,
                subject_type="source",
                subject_id=source_id,
                subject_label=source_id,
                rule_id="rule:staleness_probe",
                explanation="",
                observed_at=latest.observed_at,
                scope="",
            ),
            now,
            policy,
        ),
        counts=counts,
    )
