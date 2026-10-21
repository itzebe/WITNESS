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

To confirm a token works before recording a demo, run the access-gate script. It
makes one read-only pass through the real adapter and reports exactly what the
call established, without printing the token:

```bash
RING_ACCESS_TOKEN="<token>" python scripts/live_smoke.py
```

## Tests

```bash
python -m pytest tests/ -q
```

368 tests. The suite covers the status taxonomy, the reconciliation rules, the
ledger's tamper detection *and its documented limits*, the Ring adapter's full
HTTP surface against a mock transport, the service pipeline, the HTTP API, and
the front end (including a headless harness that runs every render function
against real API payloads).

`tests/test_incidents.py` is organised by incident class — withdrawal,
encryption transition, duplicate delivery, late arrival, outage, and evidence
unavailability — and pins the conclusion WITNESS must *not* reach from
insufficient evidence. `docs/EVIDENCE_MATRIX.md` records the corresponding
expected behaviour, evidence, and verification status per class.

`tests/test_scenario_gaps.py` covers the same four scenarios the Ring Developer
Playground cannot produce — withdrawal, configuration transitions, duplicate
delivery, and late/out-of-order arrival — end to end through the deterministic
engine, including the behaviour that only appears across more than one
observation (a device returning after a withdrawal, a re-delivered event, a
reading retained after the device leaves the inventory). It also carries the
regression tests for the two defects described under "Two clocks, one claim"
below.

Notable tests:

- `test_full_rewrite_is_not_detectable_by_a_local_verifier` — asserts the
  limitation, so it cannot be quietly overclaimed later.
- `test_ring_capabilities_claim_only_what_was_actually_exercised` — the adapter
  may not report `VERIFIED` for anything it has not been exercised against.
- `test_successful_empty_response_is_a_success_not_a_failure` — the distinction
  the product is built on.
- `test_source_failure_frame_does_not_become_a_withdrawal` — a failed request
  must never be read as a device disappearing.
- `test_empty_event_history_does_not_produce_a_withdrawal` — an empty history is
  a successful response, not a device disappearing.
- `test_current_encryption_value_does_not_assert_a_transition` — WITNESS reports
  the current reading; it does not claim a change it cannot evidence.
- `test_configuration_reading_survives_a_device_leaving_the_real_pipeline` —
  a reading WITNESS took is not discarded by a later pass that could not see the
  device.
- `test_content_assessment_cites_the_observation_that_took_the_reading` — the
  evidence link points at the pass that took the reading, not the latest pass.
- `test_on_time_event_redelivered_later_is_not_reported_late` — lateness is
  measured from the first retrieval, not the most recent re-delivery.

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
tests/              368 tests
docs/               Friction log, demo script, submission notes, evidence matrix
```

## Honesty boundaries

These are enforced by tests, not just by intention.

- Simulated data is labelled `SIMULATED` in every payload, in the timeline, and
  in the UI. The simulated adapter never claims to be Ring.
- A live authenticated Ring call **has** been performed against the Ring
  Developer Playground: `GET /v1/devices`, `GET /v1/devices/{id}/configurations`
  and `GET /v1/history/devices/{id}/events` all returned HTTP 200. Those three
  capabilities are reported `VERIFIED`; everything else in the Ring interface
  stays `DOCUMENTED`. WITNESS never reports `VERIFIED` for anything it has not
  been exercised against, and the Playground cannot produce a withdrawal, an
  encryption transition, a duplicate or a late arrival.
- The ledger is described as **tamper-evident**, never immutable. A privileged
  operator can rewrite the whole chain; the UI says so.
- WITNESS makes no emergency, medical, safety or reliability claims. It reports
  evidence quality. It does not tell you someone is safe.
- `SOURCE_WITHDRAWN` means the source stopped exposing a device. It explicitly
  does not mean the device was removed, tampered with, or broken.

## Three claims that were wrong, and are now pinned

All were found by reading the real pipeline rather than the code that was meant
to test it. None crashed; each produced a confident statement the evidence did
not support. They are recorded here because the product's whole argument is that
it does not do this.

**A reading discarded by the pass that could not see it.** A device's encryption
reading is taken in one observation and retained on its record. The content rule
read the *latest* observation to decide whether the configurations request had
been made, but `collect_observation` never requests configurations when the
inventory is empty — so the latest observation had no configurations response,
and the rule reported `CONTENT_UNAVAILABLE`, discarding a reading WITNESS had
actually taken. The existing test passed only because its fixture gave the later
observation a successful empty configurations response, which the real adapter
never produces. The rule now derives the assessment from the newest pass that
actually queried the device, and cites that pass. Pinned by
`test_configuration_reading_survives_a_device_leaving_the_real_pipeline`.

**A citation to an observation that never queried the device.** The content
assessment cited the latest observation regardless of which pass had taken the
reading. When a device stopped being the inspected one, its assessment pointed at
an observation that had inspected a *different* device — a ledger trace that
could not support the claim it was attached to. Now pinned by
`test_content_assessment_cites_the_observation_that_took_the_reading` and
`test_content_assessment_never_cites_a_pass_that_did_not_query_the_device`.

**A lateness claim that grew with every poll.** An event's `retrieved_at` is
stamped when its response arrives, so a re-delivery on a later poll overwrote it
with a later time. Lateness was computed from that value, so an event first seen
on time and re-delivered thirty minutes later was reported `EVENT_LATE` with a
thirty-minute delay, and the explanation asserted a "first retrieved at" time
that was not the first retrieval. Lateness is now measured from the earliest
retrieval of that event identity across the observation history. Pinned by
`test_on_time_event_redelivered_later_is_not_reported_late`.

## License

MIT. See `LICENSE`.
