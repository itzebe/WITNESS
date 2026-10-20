"""Shared test fixtures.

Two clearly separated kinds of test live in this suite:

* **Fixture-based** tests build synthetic observations directly. They are fast,
  deterministic, and cover the reconciliation and ledger logic exhaustively.
* **Adapter tests** exercise the real HTTP client against ``httpx.MockTransport``,
  which pins the request shape, the JSON:API parsing and the error
  classification without needing a live credential.

No test in this suite claims a live Ring call was made.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from witness.engine.models import (
    DeviceRecord,
    EventRecord,
    Failure,
    FailureKind,
    FetchResult,
    Observation,
)

T0 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_observation(
    observation_id: str,
    *,
    source_id: str = "ring",
    source_kind: str = "live",
    observed_at: datetime | None = None,
    inventory_ok: bool = True,
    inventory_failure: FailureKind | None = None,
    devices: list[DeviceRecord] | None = None,
    events: list[EventRecord] | None = None,
    history_ok: bool = True,
    configurations_ok: bool = True,
    configurations_attempted: bool = True,
) -> Observation:
    """Build one synthetic observation for reconciliation tests."""
    when = observed_at or T0
    endpoint = f"https://api.amazonvision.com/v1/devices"

    if inventory_ok:
        inventory = FetchResult.success(endpoint, [], observed_at=when, request_id="req-1", http_status=200)
    else:
        inventory = FetchResult.failed(
            endpoint,
            Failure(
                kind=inventory_failure or FailureKind.SERVER_ERROR,
                message="synthetic failure",
                http_status=500,
                endpoint=endpoint,
            ),
            observed_at=when,
        )

    history = None
    if history_ok:
        history = FetchResult.success(
            f"{endpoint}/history", [], observed_at=when, request_id="req-2", http_status=200
        )

    configurations = None
    if configurations_attempted:
        if configurations_ok:
            configurations = FetchResult.success(
                f"{endpoint}/configurations", {}, observed_at=when, request_id="req-3", http_status=200
            )
        else:
            configurations = FetchResult.failed(
                f"{endpoint}/configurations",
                Failure(FailureKind.SERVER_ERROR, "synthetic failure", http_status=500),
                observed_at=when,
            )

    return Observation(
        observation_id=observation_id,
        source_id=source_id,
        source_kind=source_kind,
        observed_at=when,
        inventory=inventory,
        history=history,
        configurations=configurations,
        devices=tuple(devices or []),
        events=tuple(events or []),
    )


def device(
    device_id: str,
    label: str,
    *,
    encrypted: bool | None = None,
    stated: bool = True,
    kind: str = "camera",
    online: bool | None = True,
) -> DeviceRecord:
    return DeviceRecord(
        device_id=device_id,
        device_label=label,
        device_kind=kind,
        online=online,
        e2e_encrypted=encrypted,
        encryption_stated=stated,
        raw_field_names=("id", "name"),
    )


def event(
    event_id: str,
    *,
    event_type: str = "motion",
    reported_at: datetime | None = None,
    retrieved_at: datetime | None = None,
    classification: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        event_type=event_type,
        reported_at=reported_at or T0,
        retrieved_at=retrieved_at or T0,
        classification=classification,
        raw_field_names=("event_type", "start", "end", "is_third_party_reviewed"),
    )


@pytest.fixture
def store(tmp_path):
    from witness.store import Store

    s = Store(path=str(tmp_path / "witness-test.db"))
    yield s
    s.close()
