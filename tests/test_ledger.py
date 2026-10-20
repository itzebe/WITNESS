"""Evidence ledger tests.

These pin the *actual* security property. A hash chain detects modification,
insertion, deletion, and reordering within a chain. It does not detect a full
rewrite by a privileged operator. The tests assert both the detection AND the
documented limit, so WITNESS cannot drift into overclaiming.
"""

from __future__ import annotations

import json

import pytest

from witness.engine.ledger import GENESIS_PREV, HASHED_FIELDS, Ledger, LedgerRecord
from witness.engine.models import deterministic_hash


def _ledger(n: int = 4) -> Ledger:
    """Build a deterministic chain with explicit timestamps.

    ``recorded_at`` is supplied explicitly so that two independently built
    chains are byte-identical. Without this the wall clock would differ between
    calls and the determinism test would be testing the clock, not the hasher.
    """
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    ledger = Ledger()
    for i in range(n):
        ledger.append(
            kind="assessment",
            subject_id=f"device-{i}",
            payload={"status": "OBSERVED", "index": i},
            rule_id="rule:inventory_present",
            status="OBSERVED",
            scope=f"inventory:ring:{i}",
            evidence_refs=[f"obs-{i}"],
            recorded_at=base + timedelta(seconds=i),
        )
    return ledger


# --------------------------------------------------------------------------
# Hash generation
# --------------------------------------------------------------------------


def test_genesis_link_is_all_zeroes():
    ledger = _ledger(1)
    assert ledger.records[0].prev_hash == GENESIS_PREV


def test_each_record_links_to_the_previous_digest():
    ledger = _ledger(4)
    for i in range(1, 4):
        assert ledger.records[i].prev_hash == ledger.records[i - 1].hash


def test_hashes_are_sha256_hex():
    ledger = _ledger(2)
    for record in ledger.records:
        assert len(record.hash) == 64
        int(record.hash, 16)


def test_hashing_is_deterministic():
    a = _ledger(3).to_list()
    b = _ledger(3).to_list()
    assert a == b


def test_digest_covers_prev_hash_and_seq():
    """A record's digest must commit to its position and its predecessor."""
    record = _ledger(1).records[0]
    body = record.body()
    assert "seq" in body and "prev_hash" in body
    assert "hash" not in body, "the digest must not cover itself"
    assert set(body.keys()) == set(HASHED_FIELDS)


def test_changing_payload_changes_the_digest():
    record = _ledger(1).records[0]
    original = record.compute_hash()
    record.payload = {"status": "SOURCE_WITHDRAWN", "index": 0}
    assert record.compute_hash() != original


def test_deterministic_hash_is_key_order_independent():
    a = deterministic_hash({"b": 1, "a": 2})
    b = deterministic_hash({"a": 2, "b": 1})
    assert a == b


# --------------------------------------------------------------------------
# Valid chain
# --------------------------------------------------------------------------


def test_valid_chain_verifies():
    result = _ledger(5).verify()
    assert result.valid is True
    assert result.record_count == 5
    assert result.first_bad_index is None


def test_empty_chain_verifies():
    result = Ledger().verify()
    assert result.valid is True
    assert result.record_count == 0


def test_single_record_chain_verifies():
    assert _ledger(1).verify().valid is True


# --------------------------------------------------------------------------
# Tampering
# --------------------------------------------------------------------------


def test_modified_record_is_detected():
    ledger = _ledger(5)
    ledger.records[2].payload = {"status": "TAMPERED", "index": 2}
    result = ledger.verify()
    assert result.valid is False
    assert result.reason == "modified_record"
    assert result.first_bad_index == 2


def test_modified_status_field_is_detected():
    ledger = _ledger(5)
    ledger.records[1].status = "SOURCE_WITHDRAWN"
    assert ledger.verify().reason == "modified_record"


def test_modified_evidence_refs_are_detected():
    ledger = _ledger(5)
    ledger.records[3].evidence_refs = ["obs-forged"]
    assert ledger.verify().reason == "modified_record"


def test_broken_prev_hash_link_is_detected():
    ledger = _ledger(5)
    ledger.records[3].prev_hash = "f" * 64
    result = ledger.verify()
    assert result.valid is False
    assert result.reason == "broken_link"
    assert result.first_bad_index == 3


def test_reordered_records_are_detected():
    ledger = _ledger(5)
    ledger._records[1], ledger._records[3] = ledger._records[3], ledger._records[1]
    result = ledger.verify()
    assert result.valid is False
    assert result.reason in {"sequence_error", "broken_link"}


def test_deleted_middle_record_is_detected():
    ledger = _ledger(5)
    del ledger._records[2]
    result = ledger.verify()
    assert result.valid is False


def test_duplicate_sequence_identifier_is_detected():
    ledger = _ledger(5)
    ledger.records[4].seq = 3
    result = ledger.verify()
    assert result.valid is False
    assert result.reason == "sequence_error"


def test_truncated_chain_still_verifies_internally_but_head_changes():
    """Documented limit: dropping the tail leaves a valid chain with a new head."""
    ledger = _ledger(5)
    head_before = ledger.head_hash
    del ledger._records[-1]
    result = ledger.verify()
    assert result.valid is True
    assert ledger.head_hash != head_before


def test_malformed_digest_is_detected():
    ledger = _ledger(3)
    ledger.records[1].hash = ""
    result = ledger.verify()
    assert result.valid is False
    assert result.reason == "malformed_entry"


def test_malformed_record_from_dict_does_not_crash_verify():
    ledger = Ledger([LedgerRecord.from_dict({"seq": 0, "record_id": "x"})])
    result = ledger.verify()
    assert result.valid is False


# --------------------------------------------------------------------------
# Documented limits
# --------------------------------------------------------------------------


def test_ledger_has_no_mutation_api():
    """The ledger exposes append only. This is a design constraint, not a guarantee."""
    ledger = _ledger(2)
    for forbidden in ("update", "delete", "remove", "insert", "replace"):
        assert not hasattr(ledger, forbidden), f"ledger must not expose {forbidden}()"


def test_full_rewrite_is_not_detectable_by_a_local_verifier():
    """The honest limitation, asserted so it cannot be forgotten or overclaimed.

    An operator who regenerates a fresh chain from scratch produces a chain that
    verifies perfectly. Only an externally anchored checkpoint would catch this.
    """
    from datetime import datetime, timezone

    stamp = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    original = _ledger(4)
    forged = Ledger()
    forged.append(kind="assessment", subject_id="device-0", payload={"status": "OBSERVED", "index": 0}, recorded_at=stamp)
    forged.append(kind="assessment", subject_id="device-1", payload={"status": "FORGED", "index": 1}, recorded_at=stamp)

    assert original.verify().valid is True
    assert forged.verify().valid is True
    assert original.head_hash != forged.head_hash


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def test_export_round_trips_and_still_verifies():
    ledger = _ledger(4)
    exported = ledger.export_json()
    restored = Ledger.from_list(json.loads(exported))
    assert restored.verify().valid is True
    assert restored.to_list() == ledger.to_list()


def test_export_contains_the_documented_fields():
    exported = json.loads(_ledger(1).export_json())
    assert set(exported[0].keys()) == set(HASHED_FIELDS) | {"hash"}


def test_export_of_a_tampered_chain_does_not_verify():
    ledger = _ledger(4)
    ledger.records[2].payload = {"forged": True}
    restored = Ledger.from_list(json.loads(ledger.export_json()))
    assert restored.verify().valid is False


def test_appending_to_a_restored_ledger_continues_the_chain():
    ledger = _ledger(3)
    restored = Ledger.from_list(json.loads(ledger.export_json()))
    restored.append(kind="observation", subject_id="s", payload={"n": 1})
    assert restored.verify().valid is True
    assert len(restored) == 4


@pytest.mark.parametrize("count", [1, 2, 7, 20])
def test_chains_of_various_lengths_verify(count):
    assert _ledger(count).verify().valid is True
