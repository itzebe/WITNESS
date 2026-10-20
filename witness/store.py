"""SQLite persistence for observations and the evidence ledger.

Design decisions, stated because they are privacy-relevant:

* WITNESS stores **normalised** observations, not raw API payloads. Raw payloads
  from a camera service can contain fields WITNESS does not need and cannot
  reason about. Storing the minimum is both a privacy decision and a
  correctness decision -- it makes the evidence set inspectable.
* No video, audio, images, addresses, owner names, or emails are stored.
* Device ids are stored because they are required to re-query the source.
* The ledger stores assessment payloads, which contain statuses, rule ids and
  source field *names*, never source field *values* beyond what the rule needs.
* Retention is bounded: at most ``MAX_OBSERVATIONS_PER_SOURCE`` observations per
  source are kept, oldest pruned first.

The store is intentionally small. It has no migration framework; if the schema
changes, delete the file. For a hackathon MVP that is the right trade-off, and
it is documented in the README rather than hidden.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .engine.ledger import Ledger
from .engine.models import (
    DeviceRecord,
    EventRecord,
    Failure,
    FailureKind,
    FetchResult,
    Observation,
    iso,
    parse_iso,
)

#: Maximum observations retained per source. Bounds both disk and the amount of
#: household history held.
MAX_OBSERVATIONS_PER_SOURCE = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL,
    source_kind    TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_source_time
    ON observations(source_id, observed_at);

CREATE TABLE IF NOT EXISTS ledger (
    seq         INTEGER PRIMARY KEY,
    record_id   TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""


@dataclass
class Store:
    """A small, thread-safe SQLite store."""

    path: str = "witness.db"

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._path = Path(self.path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- observations ------------------------------------------------------

    def save_observation(self, obs: Observation) -> None:
        payload = _observation_to_json(obs)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO observations "
                "(observation_id, source_id, source_kind, observed_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (obs.observation_id, obs.source_id, obs.source_kind, iso(obs.observed_at), payload),
            )
            self._prune(obs.source_id)
            self._conn.commit()

    def _prune(self, source_id: str) -> None:
        self._conn.execute(
            "DELETE FROM observations WHERE observation_id IN ("
            "  SELECT observation_id FROM observations WHERE source_id = ? "
            "  ORDER BY observed_at DESC LIMIT -1 OFFSET ?"
            ")",
            (source_id, MAX_OBSERVATIONS_PER_SOURCE),
        )

    def observations_for(self, source_id: str, limit: int = 200) -> list[Observation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM observations WHERE source_id = ? "
                "ORDER BY observed_at ASC LIMIT ?",
                (source_id, limit),
            ).fetchall()
        return [_observation_from_json(r[0]) for r in rows]

    def all_observations(self, limit: int = 500) -> list[Observation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM observations ORDER BY observed_at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [_observation_from_json(r[0]) for r in rows]

    def known_source_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT source_id FROM observations ORDER BY source_id"
            ).fetchall()
        return [r[0] for r in rows]

    # -- ledger ------------------------------------------------------------

    def load_ledger(self) -> Ledger:
        with self._lock:
            rows = self._conn.execute("SELECT payload FROM ledger ORDER BY seq ASC").fetchall()
        return Ledger.from_list([json.loads(r[0]) for r in rows])

    def save_ledger_records(self, records: Sequence[Any]) -> None:
        with self._lock:
            for record in records:
                self._conn.execute(
                    "INSERT OR REPLACE INTO ledger (seq, record_id, payload) VALUES (?, ?, ?)",
                    (record.seq, record.record_id, json.dumps(record.to_dict(), sort_keys=True)),
                )
            self._conn.commit()

    def reset_ledger(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ledger")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _failure_to_json(failure: Failure | None) -> dict[str, Any] | None:
    return failure.to_dict() if failure else None


def _failure_from_json(raw: dict[str, Any] | None) -> Failure | None:
    if not raw:
        return None
    return Failure(
        kind=FailureKind(raw["kind"]),
        message=raw.get("message", ""),
        http_status=raw.get("http_status"),
        endpoint=raw.get("endpoint"),
    )


def _fetch_to_json(result: FetchResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "ok": result.ok,
        "endpoint": result.endpoint,
        "observed_at": iso(result.observed_at),
        "failure": _failure_to_json(result.failure),
        "request_id": result.request_id,
        "http_status": result.http_status,
    }


def _fetch_from_json(raw: dict[str, Any] | None) -> FetchResult | None:
    if raw is None:
        return None
    observed_at = parse_iso(raw.get("observed_at")) or datetime.now().astimezone()
    if raw["ok"]:
        return FetchResult(
            ok=True, endpoint=raw["endpoint"], observed_at=observed_at,
            request_id=raw.get("request_id"), http_status=raw.get("http_status"),
        )
    return FetchResult(
        ok=False, endpoint=raw["endpoint"], observed_at=observed_at,
        failure=_failure_from_json(raw.get("failure")), http_status=raw.get("http_status"),
    )


def _observation_to_json(obs: Observation) -> str:
    return json.dumps(
        {
            "observation_id": obs.observation_id,
            "source_id": obs.source_id,
            "source_kind": obs.source_kind,
            "observed_at": iso(obs.observed_at),
            "inventory": _fetch_to_json(obs.inventory),
            "history": _fetch_to_json(obs.history),
            "configurations": _fetch_to_json(obs.configurations),
            "devices": [
                {
                    "device_id": d.device_id,
                    "device_label": d.device_label,
                    "device_kind": d.device_kind,
                    "online": d.online,
                    "reported_at": iso(d.reported_at),
                    "e2e_encrypted": d.e2e_encrypted,
                    "encryption_stated": d.encryption_stated,
                    "raw_field_names": list(d.raw_field_names),
                }
                for d in obs.devices
            ],
            "events": [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "reported_at": iso(e.reported_at),
                    "retrieved_at": iso(e.retrieved_at),
                    "classification": e.classification,
                    "raw_field_names": list(e.raw_field_names),
                }
                for e in obs.events
            ],
        },
        sort_keys=True,
    )


def _observation_from_json(raw: str) -> Observation:
    data = json.loads(raw)
    return Observation(
        observation_id=data["observation_id"],
        source_id=data["source_id"],
        source_kind=data["source_kind"],
        observed_at=parse_iso(data["observed_at"]),
        inventory=_fetch_from_json(data["inventory"]),
        history=_fetch_from_json(data.get("history")),
        configurations=_fetch_from_json(data.get("configurations")),
        devices=tuple(
            DeviceRecord(
                device_id=d["device_id"],
                device_label=d["device_label"],
                device_kind=d.get("device_kind"),
                online=d.get("online"),
                reported_at=parse_iso(d.get("reported_at")),
                e2e_encrypted=d.get("e2e_encrypted"),
                encryption_stated=bool(d.get("encryption_stated", False)),
                raw_field_names=tuple(d.get("raw_field_names") or ()),
            )
            for d in data.get("devices", [])
        ),
        events=tuple(
            EventRecord(
                event_id=e.get("event_id", ""),
                event_type=e.get("event_type", "unknown"),
                reported_at=parse_iso(e.get("reported_at")),
                retrieved_at=parse_iso(e.get("retrieved_at")),
                classification=e.get("classification"),
                raw_field_names=tuple(e.get("raw_field_names") or ()),
            )
            for e in data.get("events", [])
        ),
    )
