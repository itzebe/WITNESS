# WITNESS — Evidence-based visibility for connected homes

**Know what your home can see, and what it can't.**

WITNESS is a read-only integration that reports what a connected-home source
actually said, what can be established from it, and what remains unknown. It is
built on the Ring Partner API.

The product exists because of one specific failure mode: **an empty result and a
failed request are not the same thing**, and most connected-home tooling treats
them as if they were.

---

## The problem

A camera stops appearing in a device list. What does that mean?

- The device is offline?
- The device was unplugged?
- The owner unlinked the integration?
- The owner enabled end-to-end encryption, which removes the device from the API?
- The device left the account?
- The request failed and returned nothing?

These are different facts. Most systems render them all as "no devices" or
"device offline" and move on. For a caregiver checking on an elderly relative,
or a household that relies on a camera to confirm something happened, the
difference matters — and the wrong answer is worse than no answer.

WITNESS keeps those cases separate, states which one applies, and records the
evidence behind every conclusion.

## What it does

Thirteen statuses across four axes, each with a documented trigger and an
explicit list of the conclusions it does *not* license:

| Axis | Status | Means |
|---|---|---|
| Presence | `OBSERVED` | The source returned this device in a successful response. |
| Presence | `NOT_OBSERVED` | Absent, but not yet corroborated across enough observations. |
| Presence | `SOURCE_WITHDRAWN` | Absent across enough successful responses to assert withdrawal. |
| Presence | `SOURCE_UNAVAILABLE` | The request failed. No device conclusion is possible. |
| Content | `CONTENT_VISIBLE` / `SOURCE_ENCRYPTED` / `CONTENT_UNAVAILABLE` | Whether content can be read, and why. |
| Events | `EVENT_OBSERVED` / `EVENT_LATE` / `EVENT_DUPLICATE` | Delivery anomalies, named rather than hidden. |
| Limitations | `CLASSIFICATION_UNAVAILABLE` / `INSUFFICIENT_EVIDENCE` / `UNKNOWN` | Questions WITNESS cannot answer, said out loud. |

Two supporting properties:

**Deterministic.** The engine is pure Python with no model calls. The same
observations always produce the same assessment. You can re-run it and check.

**Tamper-evident.** Every observation and assessment is appended to a
hash-linked ledger. Modification, insertion, deletion and reordering are
detectable. The limits of that guarantee are stated in the UI, not buried.

## Quick start

```bash
pip install -r requirements.txt
python -m uvicorn witness.api:app --port 8000
# open http://127.0.0.1:8000
```

Then, in the UI: keep the **Simulated Ring source** selected, and press
**Observe now** four times, pressing **Advance scenario** in the Integration tab
between each. The four frames walk the full taxonomy, ending with an asserted
withdrawal.

### Running the live Ring path

WITNESS calls the real Ring Partner API when a token is present:

```bash
export RING_ACCESS_TOKEN="<token from the Ring Developer Playground>"
python -m uvicorn witness.api:app --port 8000
```

Without a token, WITNESS **does not contact Ring at all**. It reports the source
as unconfigured rather than as an empty device list — which is the product
working as designed.

## Tests

```bash
python -m pytest tests/ -q
```

296 tests. The suite covers the status taxonomy, the reconciliation rules, the
ledger's tamper detection *and its documented limits*, the Ring adapter's full
HTTP surface against a mock transport, the service pipeline, the HTTP API, and
the front end (including a headless harness that runs every render function
against real API payloads).

Notable tests:

- `test_full_rewrite_is_not_detectable_by_a_local_verifier` — asserts the
  limitation, so it cannot be quietly overclaimed later.
- `test_ring_capabilities_do_not_claim_verified_status` — the adapter may not
  report `VERIFIED` for anything it has not been exercised against.
- `test_successful_empty_response_is_a_success_not_a_failure` — the distinction
  the product is built on.
- `test_source_failure_frame_does_not_become_a_withdrawal` — a failed request
  must never be read as a device disappearing.

## Project layout

```
witness/
  engine/
    status.py       Status taxonomy: definitions, triggers, forbidden conclusions
    models.py       Core data types; FailureKind; deterministic hashing
    rules.py        The pure rules engine (one function per rule)
    reconcile.py    Orchestrates rules over observation history
    ledger.py       Hash-linked, append-only evidence ledger
  adapters/
    base.py         Adapter interface; shared normalisation
    ring.py         Ring Partner API (real HTTP, read-only)
    simulated.py    Scripted source, always labelled simulated
  store.py          SQLite persistence
  service.py        Application service; wires observations to the ledger
  api.py            FastAPI read model
web/                Front end (no build step)
tests/              296 tests
docs/               Friction log, demo script, submission notes
```

## Honesty boundaries

These are enforced by tests, not just by intention.

- Simulated data is labelled `SIMULATED` in every payload, in the timeline, and
  in the UI. The simulated adapter never claims to be Ring.
- A live authenticated Ring call has **not** been performed in this environment.
  Ring capabilities are reported as `DOCUMENTED`, never `VERIFIED`.
- The ledger is described as **tamper-evident**, never immutable. A privileged
  operator can rewrite the whole chain; the UI says so.
- WITNESS makes no emergency, medical, safety or reliability claims. It reports
  evidence quality. It does not tell you someone is safe.
- `SOURCE_WITHDRAWN` means the source stopped exposing a device. It explicitly
  does not mean the device was removed, tampered with, or broken.

## License

MIT. See `LICENSE`.
