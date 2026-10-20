"""A clearly-labelled simulated source.

PURPOSE
    Ring's Developer Playground exposes exactly one device and cannot emit
    motion webhooks, so it cannot exercise inventory withdrawal, encryption
    transitions, duplicate delivery, or late arrival. Those are precisely the
    conditions WITNESS exists to describe. This adapter makes them
    reproducible.

WHAT THIS IS NOT
    This is not Ring. It does not contact Ring. It does not replay a recorded
    Ring session. Every value it returns is generated from the scenario table
    below. WITNESS labels it ``simulated`` everywhere it appears -- in the API,
    in the ledger, in the UI, and in the demo -- and the interface renders a
    persistent banner whenever this source is selected.

The scenarios are scripted as an ordered list of "frames". Each frame is one
observation. Driving the engine through a script is how the reconciliation
logic is tested without depending on a live device.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..engine.models import (
    Failure,
    FailureKind,
    FetchResult,
    utcnow,
)
from .base import DEFAULT_HISTORY_LIMIT, SourceAdapter

#: Clock offset between successive frames. Frames recede from the current
#: moment, so the whole scripted session must fit inside the staleness window
#: (30 minutes): otherwise the demo would flag its own fresh evidence as stale.
FRAME_INTERVAL = timedelta(minutes=5)


@dataclass
class Frame:
    """One scripted observation."""

    devices: list[dict[str, Any]]
    events: list[dict[str, Any]] | None = None
    configurations: dict[str, Any] | None = None
    inventory_failure: FailureKind | None = None
    configurations_failure: FailureKind | None = None
    note: str = ""


def _device(device_id: str, label: str, kind: str = "camera", online: bool = True) -> dict[str, Any]:
    return {"device_id": device_id, "device_label": label, "device_kind": kind, "online": online}


#: The default scenario. Each frame is deliberately a *different* kind of
#: ambiguity, so a single run demonstrates the whole taxonomy.
DEFAULT_SCENARIO: list[Frame] = [
    Frame(
        note="Baseline. Two cameras visible, content readable.",
        devices=[
            _device("sim-front-door", "Front Door", "doorbell"),
            _device("sim-driveway", "Driveway", "camera"),
        ],
        events=[
            {"event_id": "ev-1001", "event_type": "motion", "reported_at": -2, "classification": None},
        ],
        configurations={"e2e_encryption": {"enabled": False}},
    ),
    Frame(
        note="Second camera is no longer returned. One absence only -- must NOT be called a withdrawal.",
        devices=[
            _device("sim-front-door", "Front Door", "doorbell"),
        ],
        events=[
            {"event_id": "ev-1002", "event_type": "motion", "reported_at": -3, "classification": None},
        ],
        configurations={"e2e_encryption": {"enabled": False}},
    ),
    Frame(
        note="The source errors. This must NOT be read as the camera being offline.",
        devices=[],
        inventory_failure=FailureKind.SERVER_ERROR,
    ),
    Frame(
        note="Source recovers; second camera still absent, threshold now met -> withdrawal asserted.",
        devices=[
            _device("sim-front-door", "Front Door", "doorbell"),
        ],
        events=[
            {"event_id": "ev-1003", "event_type": "motion", "reported_at": -1, "classification": None},
            # A retried delivery of the same event.
            {"event_id": "ev-1003", "event_type": "motion", "reported_at": -1, "classification": None},
            # An event whose source time is well before its retrieval time.
            {"event_id": "ev-0900", "event_type": "motion", "reported_at": -90, "classification": None},
        ],
        configurations={"e2e_encryption": {"enabled": True}},
    ),
]


class SimulatedAdapter(SourceAdapter):
    """Replays a scripted scenario as if it were a source."""

    source_id = "ring-simulated"
    source_kind = "simulated"
    display_name = "Simulated Ring source (scripted)"

    def __init__(self, scenario: list[Frame] | None = None) -> None:
        self._scenario = list(scenario or DEFAULT_SCENARIO)
        self._cursor = 0

    def reset(self) -> None:
        self._cursor = 0

    @property
    def frame_count(self) -> int:
        return len(self._scenario)

    @property
    def cursor(self) -> int:
        return self._cursor

    def current_frame(self) -> Frame | None:
        if 0 <= self._cursor < len(self._scenario):
            return self._scenario[self._cursor]
        return None

    def advance(self) -> bool:
        """Move to the next frame. Returns False when the scenario is exhausted."""
        if self._cursor + 1 >= len(self._scenario):
            return False
        self._cursor += 1
        return True

    def _now(self):
        """The simulated clock for this frame.

        Frames model a session that has already run, so they recede into the
        past: the last frame is the current moment and earlier frames are one
        interval older each. The clock must not run ahead of wall time. An
        observation stamped in the future would make its own events look
        future-dated relative to their retrieval, and would make any delay
        computed against them too small by the accumulated offset.
        """
        remaining = len(self._scenario) - 1 - self._cursor
        return utcnow() - FRAME_INTERVAL * remaining

    def _frame(self) -> Frame:
        if not self._scenario:
            return Frame(devices=[])
        index = min(self._cursor, len(self._scenario) - 1)
        return self._scenario[index]

    def capabilities(self) -> dict[str, str]:
        return {
            "device_discovery": "SIMULATED",
            "device_status": "SIMULATED",
            "device_configurations": "SIMULATED",
            "event_history": "SIMULATED",
            "inventory_withdrawal": "SIMULATED",
            "encryption_transition": "SIMULATED",
            "duplicate_delivery": "SIMULATED",
            "late_arrival": "SIMULATED",
            "source_failure": "SIMULATED",
        }

    async def fetch_inventory(self) -> FetchResult:
        frame = self._frame()
        endpoint = f"simulated://{self.source_id}/v1/devices"
        now = self._now()
        if frame.inventory_failure is not None:
            return FetchResult.failed(
                endpoint,
                Failure(
                    kind=frame.inventory_failure,
                    message="simulated inventory failure",
                    http_status=500 if frame.inventory_failure == FailureKind.SERVER_ERROR else None,
                    endpoint=endpoint,
                ),
                observed_at=now,
            )
        return FetchResult.success(endpoint, list(frame.devices), observed_at=now, request_id=f"sim-{self._cursor}")

    async def fetch_history(
        self, device_id: str, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> FetchResult:
        frame = self._frame()
        endpoint = f"simulated://{self.source_id}/v1/history/devices/{device_id}/events"
        now = self._now()
        rows: list[dict[str, Any]] = []
        for raw in frame.events or []:
            reported = now + timedelta(minutes=int(raw.get("reported_at", 0)))
            rows.append(
                {
                    "event_id": raw["event_id"],
                    "event_type": raw.get("event_type", "motion"),
                    "reported_at": reported,
                    "classification": raw.get("classification"),
                    "note": "simulated",
                }
            )
        return FetchResult.success(endpoint, rows[:limit], observed_at=now, request_id=f"sim-{self._cursor}")

    async def fetch_configurations(self, device_id: str) -> FetchResult:
        frame = self._frame()
        endpoint = f"simulated://{self.source_id}/v1/devices/{device_id}/configurations"
        now = self._now()
        if frame.configurations_failure is not None:
            return FetchResult.failed(
                endpoint,
                Failure(
                    kind=frame.configurations_failure,
                    message="simulated configurations failure",
                    endpoint=endpoint,
                ),
                observed_at=now,
            )
        return FetchResult.success(
            endpoint, dict(frame.configurations or {}), observed_at=now, request_id=f"sim-{self._cursor}"
        )
