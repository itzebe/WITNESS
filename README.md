# WITNESS — Evidence-based visibility for connected homes

**Know what your home can see, and what it can't.**

WITNESS is a read-only integration that reports what a connected-home source
actually said, what can be established from it, and what remains unknown. It is
built on the Ring Partner API.

The product exists because of one specific failure mode: **an empty result and a
failed request are not the same thing**, and most connected-home tooling treats
them as if they were.

WITNESS is not a chatbot and not an ordinary event log. There is no model
anywhere in the reconciliation path: the same observations always produce the
same assessment, every conclusion carries the rule that produced it and the
evidence it rests on, and every question the evidence cannot answer is listed as
open rather than filled in.

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

## How it works

One observation is one read-only pass. The order of the steps is fixed, and it
is the reason every conclusion in the ledger can be traced back to stored
evidence.

1. **Fetch.** `RingAdapter` calls the Ring Partner API over real HTTP, read-only,
   with a bearer token (`witness/adapters/ring.py`). With no token configured it
   returns a typed `NOT_CONFIGURED` failure and contacts nothing at all.
2. **Validate and normalise at the boundary.** Each payload is parsed by a
   boundary function that returns `(parsed, problem)` and refuses a shape it
   cannot read rather than guessing — `_extract_data_list`
   (`witness/adapters/ring.py`) and `normalise_device_list`
   (`witness/adapters/base.py`) for inventory, `normalise_event_list`
   (`witness/adapters/base.py`) for history. An unreadable payload fails the
   request; it is never silently read as an empty one.
3. **Persist the observation first.** `WitnessService.observe` writes the
   observation to SQLite *before* any conclusion is drawn
   (`witness/service.py`).
4. **Reconcile deterministically.** `reconcile()` replays the stored observation
   history through pure rule functions — no I/O, no model call — producing an
   assessment for each device, each event and each open question
   (`witness/engine/reconcile.py`, `witness/engine/rules.py`).
5. **Append to the evidence ledger.** The observation itself, then every
   assessment, is appended as a hash-linked record citing the rule id and the
   observation it rests on (`witness/engine/ledger.py`).
6. **Serve.** `witness/api.py` is a read model with no logic of its own. The
   front end renders the taxonomy it fetches from `/api/status-spec` rather than
   hardcoding status meanings in the client (`web/app.js`).

## Quick start

Requires **Python 3.10 or newer** — the pinned dependencies declare
`requires_python >= 3.10`. **Node.js** is optional and only needed for the
front-end checks (see [Tests](#tests)).

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

Never commit the token or paste it into a document: it is a credential. Pass it
through the environment only, as above.

To confirm a token works before recording a demo, run the access-gate script. It
makes one read-only pass through the real adapter and reports exactly what the
call established, without printing the token:

```bash
RING_ACCESS_TOKEN="<token>" python scripts/live_smoke.py
```

## Tests

Requires **no Ring credential and no network**. The Ring adapter is exercised
against `httpx.MockTransport`, so the suite runs offline.

```bash
python -m pytest tests/ -q          # canonical full-suite command
```

368 tests. The suite covers the status taxonomy, the reconciliation rules, the
ledger's tamper detection *and its documented limits*, the Ring adapter's full
HTTP surface against a mock transport, the service pipeline, the HTTP API, and
the front end (including a headless harness that runs every render function
against real API payloads).

Three tests are skipped when Node.js is not installed — they shell out to
`node --check` and the headless render harness. Node.js is optional; without it
you lose only those front-end checks:

```bash
node --check web/app.js             # a syntax error here blanks the whole UI
```

The count above is the last recorded full-suite result and has not been re-run
in every environment; `python -m pytest tests/ -q` prints the current number.

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

## The tamper demonstration

`scripts/demo_tamper.py` is the executable form of the Integrity tab's claim. It
is self-contained, offline, and needs only the Python standard library — it
imports the engine directly, touches no database, makes no network call and
reads no credential.

```bash
python scripts/demo_tamper.py
```

It builds a five-record hash chain, then tampers with it five distinct ways —
payload modification, record deletion, reordering, `prev_hash` breakage and
status laundering — and shows each is detected and named with the first bad
index. It then demonstrates the limitation WITNESS admits: a freshly generated,
internally consistent chain verifies `valid=True`, because a local verifier
cannot distinguish a rewrite from the original.

**What it proves:** that the hash chain detects modification, insertion,
deletion and reordering, and that WITNESS describes that guarantee as
*tamper-evident*, never immutable.

**What it does not prove:** that the ledger was never rewritten as a whole, and
nothing about live Ring data — the chain is built from a fixed synthetic
scenario.

## Documentation

The README is the entry point; the depth lives in `docs/`. Recommended reading
order:

1. **This README** — what WITNESS is, how it works, how to run it.
2. **[docs/SUBMISSION.md](docs/SUBMISSION.md)** — the submission overview: what
   to run, how to reproduce the core demonstration in 60 seconds, and the
   VERIFIED / VERIFIED LIVE / DOCUMENTED / UNAVAILABLE / UNVERIFIED breakdown of
   every claim.
3. **[docs/EVIDENCE_MATRIX.md](docs/EVIDENCE_MATRIX.md)** — the per-incident-class
   companion: for each incident class, the expected behaviour, the required
   evidence, the false-positive risks, the exact rules and tests that back it,
   and the verification status.
4. **[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)** — the timed three-minute demo
   script, including a two-segment split that labels which footage comes from
   Ring and which is WITNESS's own, and a "what not to claim" section.
5. **[docs/FRICTION_LOG.md](docs/FRICTION_LOG.md)** — the honest build log: what
   we hit on the Ring Partner API, each entry in the six-field friction-record
   shape the hackathon scores, plus the required product feedback.

`AGENTS.md` is internal engineering notes for contributors and agents, not a
judge-facing document.

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
tests/              368 tests (last recorded full-suite count)
scripts/            live_smoke.py, render_check.js, demo_tamper.py
docs/               Friction log, demo script, submission notes, evidence matrix
```

## Known limitations

These are known and stated deliberately. They are not all equally severe, and
they are not all the same kind of gap: some are design decisions, one is a
missing capability, and some are simply untested ground.

**Verified, but only in a dated and narrow way.** A live authenticated Ring call
was performed on 2026-10-21 against the Ring Developer Playground and three
endpoints answered HTTP 200 — `GET /v1/devices`, `GET /v1/devices/{id}/configurations`
and `GET /v1/history/devices/{id}/events`. The Playground returned **one synthetic
device and no events**. That token was short-lived (~30 minutes) and has expired,
so no live run is possible here without a fresh one. See
[docs/SUBMISSION.md](docs/SUBMISSION.md) for the full record.

**Real-account behaviour is unconfirmed.** Only the Playground's single synthetic
device has been observed. Whether real devices populate a `device_type`/`kind`
field or an online flag is unknown, and WITNESS reports those as unknown rather
than guessing.

**The interesting incident scenarios remain simulation-only.** The Playground
cannot produce a withdrawal, an encryption transition, a duplicate delivery or a
late arrival. Their behaviour is demonstrated on the labelled simulated source
and covered by the deterministic engine's tests — which proves the
*interpretation*, not that the live service emits the condition.

**No configuration history, so no encryption transition.** The API exposes only
the current configuration. WITNESS reports the current reading and explicitly
declines to assert that it changed; a genuine before-and-after is not
implementable from the data available.

**Live error behaviour is unexercised.** `429` and `5xx` responses are classified
and tested against a mock transport, but have not been observed from the live
service.

**The ledger is tamper-evident, not immutable.** A privileged operator can
rewrite the whole chain and it will verify cleanly. Detecting that needs an
externally anchored checkpoint, which is out of scope for this build.

**Retention is bounded at 200 observations per source.** `witness/store.py`
keeps at most `MAX_OBSERVATIONS_PER_SOURCE = 200` observations per source,
pruning oldest first. A device is only visible in the *current* view while an
observation that carried it is still inside that window; if every observation
that reported it is pruned before it reappears, the device drops out of the
current view entirely — not observed, not withdrawn, and with no limitation
entry raised for it. That is silence, not a false claim: WITNESS does not assert
removal from evidence it no longer holds. The ledger is **not** pruned, so the
full audit trail survives. `docs/EVIDENCE_MATRIX.md` characterises this boundary
and notes it is documented rather than pinned by a test.

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
