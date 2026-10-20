#!/usr/bin/env python3
"""Live Ring access gate: one read-only call through the real WITNESS adapter.

This is the smallest honest check that the live path works. It runs the real
``RingAdapter`` against the real Ring Partner API, using a token supplied via
``RING_ACCESS_TOKEN``, and reports what the call established.

It never prints the token. The adapter's own redaction is relied on, and the
fingerprint it prints is a truncated SHA-256, not the token.

Exit codes:
    0  the source answered successfully (the call was authenticated)
    2  no token is configured -- the gate cannot be opened from here
    3  the source answered with an auth/forbidden failure (token rejected)
    4  the source did not answer (network, timeout, or server error)

Run:
    RING_ACCESS_TOKEN=... python scripts/live_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from witness.adapters.base import collect_observation  # noqa: E402
from witness.adapters.ring import RingAdapter  # noqa: E402
from witness.engine.models import FailureKind  # noqa: E402
from witness.engine.reconcile import reconcile  # noqa: E402

# Failure kinds where the source answered and refused. These mean the gate is
# reachable but the credential was not accepted.
_REFUSED = {FailureKind.AUTH_FAILED, FailureKind.FORBIDDEN}


async def main() -> int:
    adapter = RingAdapter()

    if not adapter.configured:
        print("ACCESS GATE: NOT CONFIGURED")
        print("  No RING_ACCESS_TOKEN is present in this environment.")
        print("  Generate a short-lived token at the Ring Developer Playground")
        print("  (https://developer.amazon.com/ring/console/playground), then")
        print("  expose it to this process as RING_ACCESS_TOKEN.")
        return 2

    print("ACCESS GATE: token present")
    print(f"  token fingerprint: {adapter.token_fingerprint()}  (truncated SHA-256)")
    print(f"  base URL: {adapter._base_url}")
    print("  running one read-only pass through the real adapter...")
    print()

    observation = await collect_observation(adapter)
    inventory = observation.inventory

    print(f"  endpoint:  {inventory.endpoint}")
    print(f"  observed:  {observation.observed_at.isoformat()}")
    print(f"  http:      {inventory.http_status}")
    print(f"  ok:        {inventory.ok}")

    if not inventory.ok:
        failure = inventory.failure
        kind = failure.kind if failure else None
        print(f"  failure:   {kind.value if kind else 'UNKNOWN'}")
        print(f"  message:   {failure.message if failure else ''}")
        print()
        if kind in _REFUSED:
            print("ACCESS GATE: REACHED, CREDENTIAL REFUSED")
            print("  The API host answered. The token was not accepted -- it may")
            print("  have expired (Playground tokens last ~30 minutes).")
            return 3
        print("ACCESS GATE: NO ANSWER")
        print("  The source did not return a usable response. This is not evidence")
        print("  that any device is missing; it is evidence that we could not look.")
        return 4

    devices = observation.devices
    print(f"  devices:   {len(devices)}")
    for device in devices:
        print(f"    - {device.device_label} ({device.device_kind or 'unknown kind'})")

    reconciliation = reconcile("ring", [observation])
    counts = reconciliation.counts
    print()
    print("  reconciliation over this single observation:")
    for key in sorted(counts):
        print(f"    {key}: {counts[key]}")

    print()
    print("ACCESS GATE: OPEN — the source answered successfully.")
    if not devices:
        print("  The account returned zero devices. That is a successful empty")
        print("  response, not a failure: the source answered and had nothing to")
        print("  report. WITNESS keeps those two findings distinct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
