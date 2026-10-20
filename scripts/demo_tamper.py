#!/usr/bin/env python3
"""Demonstrate ledger tamper detection.

This script builds a chain, verifies it, then tampers with it in four distinct
ways and shows that each one is detected and named. It is the executable form of
the claim the Integrity tab makes.

It also demonstrates the limitation: a full rewrite of the chain verifies
perfectly. We show that too, because a security claim you cannot see the edges
of is not a claim worth making.

Usage:
    python scripts/demo_tamper.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from witness.engine.ledger import Ledger  # noqa: E402

BASE = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"


def build() -> Ledger:
    """A five-record chain, deterministic so the demo is reproducible."""
    ledger = Ledger()
    entries = [
        ("observation", "ring-simulated", {"device_count": 2}, None, "OBSERVED"),
        ("assessment", "sim-driveway", {"presence": "OBSERVED"}, "rule:inventory_present", "OBSERVED"),
        ("assessment", "sim-front-door", {"presence": "OBSERVED"}, "rule:inventory_present", "OBSERVED"),
        ("assessment", "sim-driveway", {"presence": "ABSENT"}, "rule:inventory_absent_below_threshold", "NOT_OBSERVED"),
        ("assessment", "sim-driveway", {"presence": "ABSENT"}, "rule:inventory_absent_at_or_above_threshold", "SOURCE_WITHDRAWN"),
    ]
    for i, (kind, subject, payload, rule, status) in enumerate(entries):
        ledger.append(
            kind=kind,
            subject_id=subject,
            payload=payload,
            rule_id=rule,
            status=status,
            scope=f"demo:{subject}",
            evidence_refs=[f"obs-{i}"],
            recorded_at=BASE + timedelta(seconds=i),
        )
    return ledger


def report(label: str, ledger: Ledger, expect_valid: bool) -> bool:
    result = ledger.verify()
    ok = result.valid == expect_valid
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}UNEXPECTED{RESET}"
    print(f"\n{BOLD}{label}{RESET}  [{mark}]")
    if result.valid:
        print(f"  {GREEN}chain valid{RESET} — {result.record_count} records, head {ledger.head_hash[:12]}…")
    else:
        print(f"  {RED}chain INVALID{RESET} — reason={result.reason}, "
              f"first bad index={result.first_bad_index}")
        if result.detail:
            print(f"  {DIM}{result.detail}{RESET}")
    return ok


def main() -> int:
    print(f"{BOLD}WITNESS — evidence ledger tamper detection{RESET}")
    print(f"{DIM}Five-record chain. SHA-256 hash links. Canonical JSON.{RESET}")

    failures = 0

    baseline = build()
    failures += not report("1. Baseline: untouched chain", baseline, expect_valid=True)

    # --- Modification -------------------------------------------------------
    modified = build()
    modified.records[2].payload = {"presence": "FORGED"}
    failures += not report("2. Modification: a record's payload is edited", modified, expect_valid=False)

    # --- Deletion -----------------------------------------------------------
    deleted = build()
    del deleted._records[2]
    failures += not report("3. Deletion: a middle record is removed", deleted, expect_valid=False)

    # --- Reordering ---------------------------------------------------------
    reordered = build()
    reordered._records[1], reordered._records[3] = reordered._records[3], reordered._records[1]
    failures += not report("4. Reordering: two records are swapped", reordered, expect_valid=False)

    # --- Link breakage ------------------------------------------------------
    relinked = build()
    relinked.records[3].prev_hash = "f" * 64
    failures += not report("5. Link breakage: prev_hash is rewritten", relinked, expect_valid=False)

    # --- Status laundering --------------------------------------------------
    laundered = build()
    laundered.records[4].status = "OBSERVED"
    failures += not report(
        "6. Status laundering: a withdrawal is relabelled as observed",
        laundered, expect_valid=False,
    )

    # --- The documented limitation -----------------------------------------
    print(f"\n{BOLD}7. The limitation we do not hide{RESET}")
    forged = Ledger()
    for i in range(2):
        forged.append(
            kind="assessment",
            subject_id=f"device-{i}",
            payload={"presence": "OBSERVED"},
            rule_id="rule:inventory_present",
            status="OBSERVED",
            recorded_at=BASE + timedelta(seconds=i),
        )
    forged_result = forged.verify()
    print(f"  A freshly generated, internally consistent chain: "
          f"{GREEN}valid={forged_result.valid}{RESET} ({forged_result.record_count} records)")
    print(f"  {YELLOW}This is the limit.{RESET} An operator with write access can discard the real chain")
    print(f"  and rebuild a clean one. A local verifier cannot tell the difference.")
    print(f"  Detecting it requires an independently anchored checkpoint — a digest held")
    print(f"  somewhere the operator does not control. WITNESS says 'tamper-evident',")
    print(f"  never 'immutable'.")

    # --- Export round trip --------------------------------------------------
    print(f"\n{BOLD}8. Export and re-verify{RESET}")
    exported = json.loads(baseline.export_json())
    restored = Ledger.from_list(exported)
    round_trip_ok = restored.verify().valid and restored.to_list() == baseline.to_list()
    print(f"  {'PASS' if round_trip_ok else 'FAIL'}: exported JSON restores to an identical, valid chain")
    failures += not round_trip_ok

    print()
    if failures:
        print(f"{RED}{BOLD}{failures} check(s) behaved unexpectedly.{RESET}")
        return 1
    print(f"{GREEN}{BOLD}All tamper-detection checks behaved as documented.{RESET}")
    print(f"{DIM}Six tamper classes detected. One limitation stated plainly.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
