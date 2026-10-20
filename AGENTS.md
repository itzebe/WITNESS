# AGENTS.md — WITNESS

Repository knowledge for future sessions. Read this before changing anything.

## What this is

WITNESS is a read-only evidence layer for connected-home sources, built on the
Ring Partner API, for the "Build, Ship, Shape" Amazon Developer Hackathon.

One sentence: it reports what a source actually said, what can be established
from it, and what remains unknown.

The founding observation is narrow and load-bearing: **an empty result and a
failed request are not the same thing**, and most connected-home tooling treats
them as identical. Almost every design decision traces back to that.

## Commands

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                  # 296 tests, ~4s
python -m uvicorn witness.api:app --port 8000
python scripts/demo_tamper.py               # ledger tamper demo
node scripts/render_check.js <overview.json> <spec.json> [devices.json]
node --check web/app.js                     # always run after editing app.js
```

## Architecture

```
witness/engine/status.py     Status taxonomy. The single source of truth for
                             what each status means and what it does NOT license.
witness/engine/models.py     Data types. FailureKind. deterministic_hash.
witness/engine/rules.py      Pure rule functions, one per rule id.
witness/engine/reconcile.py  Applies rules over observation history.
witness/engine/ledger.py     Hash-linked append-only chain. verify() returns a
                             VerificationResult, never raises.
witness/adapters/base.py     Adapter protocol + shared normalisation.
witness/adapters/ring.py     Live Ring API. Real HTTP. Read-only.
witness/adapters/simulated.py Scripted source. Always labelled simulated.
witness/store.py             SQLite persistence.
witness/service.py           Wires observation -> store -> ledger.
witness/api.py               FastAPI read model. No logic of its own.
web/                         Front end. No build step, no framework.
```

Data flow: `observe()` fetches from an adapter, persists the observation to the
store **first**, then reconciles over the stored history, then appends every
assessment to the ledger. Evidence is durable before any conclusion is drawn.
Do not reorder this.

## Invariants — do not break these

1. **The engine is deterministic and makes no model calls.** If you add an LLM
   anywhere near the reconciliation path, you have broken the product's central
   claim. Natural-language generation is optional and separate; it must never
   produce a status, an evidence reference, or a conclusion.

2. **A failed request never produces a device conclusion.** If the inventory
   request fails, every device is `SOURCE_UNAVAILABLE` and no withdrawal logic
   runs. Pinned by `test_source_failure_frame_does_not_become_a_withdrawal`.

3. **An empty success is not a failure.** `FetchResult(ok=True, data=[])` is a
   successful response with no devices. Pinned by
   `test_successful_empty_response_is_a_success_not_a_failure`.

3b. **An unreadable payload is not an empty payload.** The converse of #3, and
   the harder one to hold: an HTTP 200 whose body WITNESS cannot interpret — an
   error envelope, a `data` member of the wrong type, an entry with no usable
   identity — must fail the request, never be read as "successfully zero
   items". Reading it as empty lets sustained unreadable responses assert a
   device withdrawal that the data does not support. Both the inventory and the
   history path apply this guard (`_extract_data_list`,
   `normalise_event_list`). Pinned by
   `test_error_envelope_does_not_become_an_asserted_withdrawal` and
   `test_unreadable_history_does_not_fabricate_a_duplicate_finding`.

4. **The ledger is tamper-evident, not immutable.** Never write "immutable",
   "tamper-proof", or "certified" in code, UI, or docs. A full rewrite verifies
   cleanly; this is asserted in
   `test_full_rewrite_is_not_detectable_by_a_local_verifier` so it cannot be
   quietly overclaimed later.

5. **Simulated data is labelled simulated everywhere.** In every payload
   (`source_kind: "simulated"`), in the timeline, in the UI, in the capability
   table. The simulated adapter must never claim to be Ring.

6. **No credential ever appears in output.** `token_fingerprint()` is 12 chars
   of a digest. Error messages pass through `_redact()`.

7. **Capabilities are `DOCUMENTED`, never `VERIFIED`, unless exercised live.**
   Pinned by `test_ring_capabilities_do_not_claim_verified_status`.

8. **No safety, medical, or emergency claims.** WITNESS reports evidence
   quality. It does not tell anyone they are safe.

## Adding a status

1. Add to `Status` in `engine/status.py`.
2. Add a `StatusSpec` entry with `definition`, `trigger`, `required_evidence`,
   and **`forbidden_conclusions`** — the latter is mandatory and is surfaced in
   the UI and the API.
3. Add the rule to `engine/rules.py`.
4. Wire it in `engine/reconcile.py`.
5. Add the group to `renderMethod()` in `web/app.js`.
6. Add tests, including one asserting what the status does *not* establish.

## Testing notes

- `tests/conftest.py` provides `store`, `service`, `T0`, `device()`,
  `make_observation()`.
- The Ring adapter is tested against `httpx.MockTransport` — no network, no
  credentials. Test both success and every failure classification.
- `pytest.ini` sets `asyncio_mode = auto`. Async tests need no marker.
- `tests/test_frontend.py` includes a headless render harness that runs every
  render function against real API payloads. A syntax check alone will not catch
  a render path that throws at runtime — this will.
- Run `node --check web/app.js` after any JS edit. A syntax error blanks the
  entire UI and every Python test still passes without it.

## Front-end gotchas

- `el(tag, props, ...children)` coalesces `props ?? {}` because call sites
  routinely pass an explicit `null`. Removing that breaks many render paths.
- The UI reads the taxonomy from `/api/status-spec`. Do not hardcode status
  labels or meanings in the client.
- Colour is never the only signal: every pill carries a glyph via CSS
  `::before`. Pinned by `test_no_inline_colour_only_signalling_in_status_pills`.

## Environment constraints

- No Amazon Developer credentials in this environment. The live Ring path is
  implemented and mock-tested but has **not** been exercised authenticated.
  Do not claim otherwise in any artifact.
- `gh` is not authenticated.
- `api.amazonvision.com` returns 401 for real routes and 404 for fake ones —
  use this to confirm routing without credentials.

## The defect class to hunt for

WITNESS's failure mode is not crashing, it is *fabricating*: turning something
it could not read into a confident finding. Every defect found in the Phase 1
audit was of this class, and each was reachable from a plain HTTP 200:

| Shape | Was read as | Now |
|---|---|---|
| `{"error": "..."}` with HTTP 200 | empty inventory -> withdrawal | MALFORMED_RESPONSE |
| `{"data": "oops"}` | empty inventory -> withdrawal | MALFORMED_RESPONSE |
| history entry `42` | phantom event -> EVENT_DUPLICATE | MALFORMED_RESPONSE |

When adding a parse path, ask: *if this input is garbage, does anything
downstream assert a conclusion from it?* The guard belongs at the boundary,
where the payload shape is still visible. `_extract_data_list`,
`normalise_device_list` and `normalise_event_list` are the three boundary
functions; all three return `(parsed, problem)` and all three refuse rather
than guess. Do not add a fourth that returns a bare list.

### The second class: text that contradicts itself

A separate class, found by reading the rendered UI rather than the code. An
explanation can be built from two true statements that do not agree with each
other. The one caught so far: the classification limitation printed the field
list (which included `classification`, because the source returned the key with
a null value) and then said the records "do not carry a classification field".
Both halves came from real data; the sentence was false.

The fix is to choose a claim that holds in every case the branch can fire --
"carries a populated classification value" is true whether the key is absent or
present-and-null. When a message is assembled from more than one derived value,
read the rendered sentence, not just the inputs.

### The third class: describing a request that was never made

An explanation can name an interaction that did not happen. One observation
requests history and configurations for *one* device -- the first in inventory
-- but the content rule is evaluated for every device ever seen. A device that
was never queried therefore produced an explanation beginning "The
configurations response for Driveway ... did not include an encryption field",
asserting a response that does not exist.

The honest finding for that device is not "asked and absent" but "not asked",
and those are different. `Observation.inspected_device_id` records which device
the requests were actually made against; `rule_content_state` compares it to
the device under assessment before it describes a response. "Not asked" and
"asked and the field was absent" both map to CONTENT_UNAVAILABLE, so the status
alone never revealed the difference -- the wording did, and only to a reader
checking it against what was fetched.

When an explanation names a request, an endpoint, or a response, verify that
request was made for that subject in that observation.

### The fourth class: two clocks in one pass

Timestamps can disagree with each other even when every value is individually
plausible. `collect_observation` stamped the observation from its own
wall-clock read while each adapter stamped its own responses from its own
clock. Nothing crashed and no status changed, but the simulation produced
events dated in the future relative to the retrieval that was supposed to have
produced them, and a delay the scenario scripts as 90 minutes was reported as
59m 59s -- the offset between the two clocks.

One pass has one clock. An observation takes its time from the response it is
built around, and each record takes its retrieval time from the response that
carried it. When a rule subtracts two timestamps, check they were read from
the same source.

### The fifth class: a clock that runs ahead of the reader

A source's clock can be ahead of the wall clock, which makes fresh evidence
look old, or stale evidence look current. The simulated frames used to advance
into the future, so the newest frame was dated ahead of "now". After the clock
was re-anchored to recede from the present, the frame spacing had to shrink:
at ten minutes per frame the earliest observation landed past the 30-minute
staleness window, and a single observation made the demo flag its own fresh
evidence as stale. Check that a scenario's whole span fits inside the
thresholds it is meant to demonstrate.

### The sixth class: a value WITNESS manufactured at the boundary

An explanation can be built from a field that was present but that WITNESS
could not actually read, and the coercion happens silently at a boundary far
from the sentence that states it. Two instances were found in the same sweep:

- `_read_encryption` returned `bool(node.get("enabled"))`. A stringified
  `"false"` became `True` ("this device is end-to-end encrypted") and an
  explicit `null` became `False` ("this device is not encrypted"). Both are
  claims the source never made. Only a real boolean states a state; anything
  else is "not stated".
- `RingAdapter.fetch_configurations` returned the raw JSON:API envelope while
  `_read_encryption` expected the unwrapped attributes object. On the live
  path the encryption field was therefore never found, and the device was
  reported as CONTENT_UNAVAILABLE with an explanation saying the response "did
  not include an encryption field" -- about a response that did. The envelope
  is now unwrapped in the adapter, where the payload shape is still visible.

The same rule applies to the normalised-device branch: "stated" and "is a
boolean" must agree, or a non-boolean value slips through and the content rule
prints "reported e2e_encryption.enabled = false" from it.

The wording also had to split. A missing field and a present-but-unreadable
field are different findings and now produce different sentences, with
`encryption_field_present` / `encryption_value_readable` in the detail to keep
them distinguishable in the payload.

When a value is coerced, decoded, or unwrapped at a boundary, test what the
boundary does with a wrong-typed input -- not only with a well-formed one.

## Submission artifacts

- `docs/FRICTION_LOG.md` — scored deliverable. Honest, specific, includes dead
  ends and unreproduced observations.
- `docs/DEMO_SCRIPT.md` — timed three-minute script with an explicit "what not
  to claim" section.
- `docs/SUBMISSION.md` — VERIFIED / DOCUMENTED / UNAVAILABLE / UNVERIFIED
  breakdown and the tests that pin each honesty claim.
