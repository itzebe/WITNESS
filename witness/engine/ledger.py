"""The tamper-evident evidence ledger.

Each record commits to the previous record's digest, forming a hash chain. The
guarantee this actually provides, stated precisely:

WHAT IT DOES PROVE
    Given a chain of N records and a digest you recorded earlier (or that you
    obtained from a trusted party), any modification, insertion, deletion, or
    reordering of the records in that chain is detectable. A local verifier
    will report the first index at which the chain stops reproducing.

WHAT IT DOES NOT PROVE
    It does not prove the ledger was never rewritten in its entirety. An
    operator with write access to the file and knowledge of the format can
    regenerate a fresh, internally consistent chain from scratch. Detecting
    that requires an independently anchored checkpoint -- a digest published or
    stored somewhere the operator does not control.

WITNESS therefore never describes this ledger as immutable, certified, or
externally trusted. It is described as tamper-evident, which is what it is.

Hash coverage: the digest covers the record's canonical JSON with the ``hash``
field excluded. Every other field, including ``prev_hash`` and ``seq``, is
covered. This is asserted by tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from .models import deterministic_hash, iso, utcnow

GENESIS_PREV = "0" * 64

#: The exact fields covered by the digest, in documentation order. The hash is
#: computed over the canonical JSON of the record with ``hash`` removed, so
#: this tuple is descriptive of the contract rather than the mechanism.
HASHED_FIELDS: tuple[str, ...] = (
    "seq",
    "record_id",
    "prev_hash",
    "recorded_at",
    "kind",
    "subject_id",
    "rule_id",
    "status",
    "scope",
    "evidence_refs",
    "payload",
)


@dataclass
class LedgerRecord:
    """One link in the chain."""

    seq: int
    record_id: str
    prev_hash: str
    recorded_at: str
    kind: str
    subject_id: str
    rule_id: str | None
    status: str | None
    scope: str | None
    evidence_refs: list[str]
    payload: Mapping[str, Any]
    hash: str = ""

    def body(self) -> dict[str, Any]:
        """The digestible body: every field except ``hash``."""
        return {
            "seq": self.seq,
            "record_id": self.record_id,
            "prev_hash": self.prev_hash,
            "recorded_at": self.recorded_at,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "rule_id": self.rule_id,
            "status": self.status,
            "scope": self.scope,
            "evidence_refs": list(self.evidence_refs),
            "payload": _jsonable(self.payload),
        }

    def compute_hash(self) -> str:
        return deterministic_hash(self.body())

    def to_dict(self) -> dict[str, Any]:
        body = self.body()
        body["hash"] = self.hash
        return body

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LedgerRecord":
        """Rebuild a record from a mapping, tolerating missing fields.

        A malformed entry must survive parsing so that ``verify`` can report it
        as ``malformed_entry``. Raising here would turn a detected integrity
        problem into a crash, which is worse.
        """
        return cls(
            seq=int(raw.get("seq") or 0),
            record_id=str(raw.get("record_id") or ""),
            prev_hash=str(raw.get("prev_hash") or ""),
            recorded_at=str(raw.get("recorded_at") or ""),
            kind=str(raw.get("kind") or ""),
            subject_id=str(raw.get("subject_id") or ""),
            rule_id=raw.get("rule_id"),
            status=raw.get("status"),
            scope=raw.get("scope"),
            evidence_refs=list(raw.get("evidence_refs") or []),
            payload=raw.get("payload") or {},
            hash=str(raw.get("hash") or ""),
        )


def _jsonable(value: Any) -> Any:
    """Recursively convert to JSON-safe primitives with stable key order."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of verifying a chain."""

    valid: bool
    record_count: int
    verified_at: str
    first_bad_index: int | None = None
    reason: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "record_count": self.record_count,
            "verified_at": self.verified_at,
            "first_bad_index": self.first_bad_index,
            "reason": self.reason,
            "detail": self.detail,
        }


class Ledger:
    """An append-only hash chain held in memory, persisted by the caller.

    The ledger deliberately exposes no update or delete operation. Records can
    only be appended. That is a design constraint, not a security guarantee:
    it constrains WITNESS's own code, while the persistence layer remains
    rewritable by anyone with file access.
    """

    def __init__(self, records: Sequence[LedgerRecord] | None = None) -> None:
        self._records: list[LedgerRecord] = list(records or [])

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[LedgerRecord, ...]:
        return tuple(self._records)

    @property
    def head_hash(self) -> str:
        return self._records[-1].hash if self._records else GENESIS_PREV

    def append(
        self,
        *,
        kind: str,
        subject_id: str,
        payload: Mapping[str, Any],
        rule_id: str | None = None,
        status: str | None = None,
        scope: str | None = None,
        evidence_refs: Sequence[str] = (),
        recorded_at: datetime | None = None,
    ) -> LedgerRecord:
        seq = len(self._records)
        record = LedgerRecord(
            seq=seq,
            record_id=f"lr-{seq:06d}",
            prev_hash=self.head_hash,
            recorded_at=iso(recorded_at or utcnow()) or "",
            kind=kind,
            subject_id=subject_id,
            rule_id=rule_id,
            status=status,
            scope=scope,
            evidence_refs=list(evidence_refs),
            payload=_jsonable(payload),
        )
        record.hash = record.compute_hash()
        self._records.append(record)
        return record

    def verify(self, verified_at: datetime | None = None) -> VerificationResult:
        """Verify structural integrity and hash linkage of the whole chain.

        Detects: modified records, broken ``prev_hash`` links, reordered
        records, duplicate or non-contiguous sequence numbers, and malformed
        entries.
        """
        stamp = iso(verified_at or utcnow()) or ""
        expected_prev = GENESIS_PREV
        for index, record in enumerate(self._records):
            if not isinstance(record.hash, str) or len(record.hash) != 64:
                return VerificationResult(
                    False, len(self._records), stamp, index, "malformed_entry",
                    f"record {index} has a missing or malformed digest",
                )
            if record.seq != index:
                return VerificationResult(
                    False, len(self._records), stamp, index, "sequence_error",
                    f"record at index {index} carries seq {record.seq}",
                )
            if record.prev_hash != expected_prev:
                return VerificationResult(
                    False, len(self._records), stamp, index, "broken_link",
                    f"record {index} links to {record.prev_hash[:12]}... but the chain expects {expected_prev[:12]}...",
                )
            recomputed = record.compute_hash()
            if recomputed != record.hash:
                return VerificationResult(
                    False, len(self._records), stamp, index, "modified_record",
                    f"record {index} does not reproduce its stored digest",
                )
            expected_prev = record.hash
        return VerificationResult(True, len(self._records), stamp, None, None, None)

    def to_list(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._records]

    def export_json(self) -> str:
        return json.dumps(self.to_list(), indent=2, sort_keys=True)

    @classmethod
    def from_list(cls, raw: Sequence[Mapping[str, Any]]) -> "Ledger":
        return cls([LedgerRecord.from_dict(r) for r in raw])
