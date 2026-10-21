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
python -m pytest tests/ -q                  # 368 tests, ~4s
python -m uvicorn witness.api:app --port 8000
python scripts/demo_tamper.py               # ledger tamper demo
node scripts/render_check.js <overview.json> <spec.json> [devices.json|-] [verdicts.json]
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

7. **Capabilities are `DOCUMENTED` unless exercised live; only the three
   exercised endpoints are `VERIFIED`.** Pinned by
   `test_ring_capabilities_claim_only_what_was_actually_exercised`.

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

- **The live Ring path IS now verified.** On 2026-10-21 a Playground token
  (scope `ava.v1:read`) supplied through the environment produced, through the
  real `RingAdapter`, **three** HTTP 200 responses: `GET /v1/devices` (one
  synthetic device, `Playground Device`), `GET /v1/devices/{id}/configurations`
  (attributes `image_enhancements`, `audio`, `motion_detection`, `e2e_encryption`)
  and `GET /v1/history/devices/{id}/events` (zero events). `scripts/live_smoke.py`
  exits **0** (`ACCESS GATE: OPEN`), and `POST /api/observe/ring` returned
  `inventory_ok/configurations_ok/history_ok` all true. `device_discovery`,
  `device_configurations` and `event_history` are therefore reported `VERIFIED`;
  every other Ring capability stays `DOCUMENTED`. Pinned by
  `test_ring_capabilities_claim_only_what_was_actually_exercised`.
- **`RING_ACCESS_TOKEN` was registered in the approved settings secret store**
  (agent-server `PUT /api/settings/secrets`), persisted Fernet-encrypted in
  `~/.openhands/secrets.json` (mode 600, outside the repo) and never written to
  source, Git, or the demo transcript. **The token registered on 2026-10-21 has
  since expired** (~30-minute lifetime), so no valid Ring credential is present
  now and the live path cannot be re-run without a fresh one. Registration is
  not injection into an arbitrary shell — read the value over the API and pass
  it through the child environment (see the injection note below). A fresh run
  without a token exits 2 (not configured). Never commit a token; `.gitignore`
  covers `.env`, `*.pem`, `*.key`, `token*.txt`, `*.db`.
- **The approved secret store is the agent-server on port 60000, not 8001.**
  `PUT/GET/DELETE /api/settings/secrets` (auth: `X-Session-API-Key: $SESSION_API_KEY`)
  register secrets that persist, Fernet-encrypted, in `~/.openhands/secrets.json`
  (mode 600, outside the repo). `GET /api/settings/secrets` lists **names only**;
  `GET /api/settings/secrets/{name}` returns the value as `text/plain`. Earlier
  attempts failed because they targeted `localhost:8001` (connection refused —
  the agent server is on **60000**) and `app.all-hands.dev/api/settings/secrets`
  (an SPA HTML fallback, not an API). Registration alone is not injection — see
  the next bullet for what actually works. To register `RING_ACCESS_TOKEN`, the
  environment owner must supply a fresh, undisclosed Playground token — do not
  reuse a disclosed one or paste a token into chat.
- **Registration does not inject; fetch-then-pass does.** Verified by probe: a
  name registered through `/api/settings/secrets` is *not* present in a later
  shell even when the name appears in the command text. Conversation-scoped
  secrets (`POST /api/conversations/{id}/secrets`, body
  `{"secrets":{"NAME":{"kind":"StaticSecret","value":...}}}`) *are* injected into
  the running conversation's environment. For an arbitrary process such as
  `scripts/live_smoke.py`, the working path is: read the value over
  `GET /api/settings/secrets/{name}` and pass it through the child environment.
  That round-trip was confirmed byte-exact by SHA-256 comparison. Never print the
  value or a fingerprint of it in a transcript.
- The Ring Developer Playground is behind an Amazon Developer sign-in
  (`amazon.com/ap/signin`), and its token lasts **30 minutes**. Creating an
  account is prohibited by the task constraints, so tokens must be supplied
  externally. `scripts/live_smoke.py` is the gate: one read-only pass through the
  real adapter, no token printed (fingerprint only).
- The Playground exposes exactly **three** simulated streams (Package, Vehicle,
  Motion). It cannot produce a withdrawal, an encryption transition, a duplicate
  or a late arrival — those stay on the labelled simulated source.
- The Playground's device resource carries only `name` and `image_url`; it has
  **no** `device_type`/`kind` field, so WITNESS reports the kind as unknown.
- `gh` is not authenticated.
- `api.amazonvision.com` returns 401 for real routes and 404 for fake ones —
  use this to confirm routing without credentials.

## Verified competition facts (checked against primary sources, Oct 2026)

- **Deadline:** Friday 23 October 2026, 12:00 pm Pacific = **19:00 UTC**.
- **Ring demo rule (verbatim):** "Any Ring API, SDK, simulator, or device
  listed in Ring's developer documentation is acceptable. The demo video must
  show the project working through a simulator or an actual Ring device you
  have for testing."
- **Runtime rule (verbatim):** the repo "must demonstrate use of your track's
  required technology at runtime in your code — imported and actually called
  (a library import, an app/backend entry point, or a loaded agent/flow/MCP
  config), not just named in the README." WITNESS satisfies this: `api.py`
  registers `RingAdapter()` and runs it on `POST /api/observe/ring`.
- **The official simulator is the Playground** (`developer.amazon.com/ring/
  console/playground`), per Ring's release notes and the hackathon organizers'
  own Devpost answer. `ring-simulated` is **not** it and must never be
  presented as it.
- **The organizer sanctions a hybrid demo.** On the Devpost forum
  (`/forum_topics/45462-simulator-on-amazon-ring`), Emerson Sklar (Amazon Ring
  developer advocate) answered: "The simulator is the Ring Developer Playground"
  and, on combining sources, "combining them is fine. Use the Playground streams
  to show the integration working end to end, and your own videos to show
  scenarios the samples don't cover. Just make it clear in your video and README
  which footage comes from Ring and which is yours." The Playground's three
  sample streams (Package, Vehicle, Motion) cannot produce a withdrawal,
  encryption transition, duplicate or late arrival — so the demo is two
  labelled segments, and `DEMO_SCRIPT.md` says so.
- **Playground tokens last 30 minutes.** Ring's release notes: "one-click OAuth
  token generation (valid 30 minutes)". Record the live segment in one sitting.
- **Video rule:** under three minutes, public on YouTube or Vimeo, in English,
  and it "must show the Project functioning on the platform for which it is
  intended".
- **Friction logs are worth up to a 10% judging bonus** and are scored on six
  named fields: task attempted, steps taken, expected vs actual, severity,
  workaround used, actionable suggestion. Each `FRICTION_LOG.md` entry carries
  a **friction record** in exactly that shape.
- **Product feedback is a required submission field**, not optional: five
  questions answered in `FRICTION_LOG.md` under "Product feedback".
- **Judging criteria** are equally weighted: Tech Implementation, Design,
  Potential Impact, Quality of the Idea. For Ring, "obvious" is "motion
  detection alert, basic doorbell live view"; "creative" is "non-security use
  case (accessibility aid, care-taking monitoring, package/delivery
  management)". WITNESS aims at the creative end.
- **Repo requirements:** public, open source, with the license visible in the
  About section (MIT here). No git remote is configured in this environment —
  pushing is a submission step, not yet done.

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

### The seventh class: a warning that reports nothing to warn about

A summary line can be true value-by-value and still contradict itself. The
overview verdict chose its tone from `unresolved > 0 || limitations.length > 0`
but built its sentence only from `unresolved`. In frame 1 of the demo every
device is observed and one classification question is open, so the panel warned
in amber while reading "2 of 2 device(s) observed; **0** not fully accounted
for", directly above a sentence saying the rest was left unresolved. Every
number was correct; the combination was false, on the first screen a judge sees.

The two branches are now separate: unresolved devices produce the device-count
sentence, and an otherwise-clean source with an open question produces an
open-question-count sentence. `verdictFor` is a pure function so the wording can
be driven over the four demo frames and checked. Pinned by
`test_overview_verdict_never_contradicts_itself`, which fails if any frame warns
while reporting zero unresolved items.

When a branch decides both a tone and a sentence, derive both from the same
condition -- and when a summary is assembled from more than one count, check the
rendered sentence, not just the inputs.

### The eighth class: a document that contradicts the platform it describes

The docs are shown to judges, so a stale claim there is read as our
understanding of the platform. The Phase 1 audit found three, all in the
submission artifacts rather than the code:

- `docs/SUBMISSION.md` named the track "**Bee / Ring**". WITNESS has no Bee
  hardware or data, and the rules say for Bee that "mentioning Bee without
  demonstrating a live data interaction does not count". A claimed track we
  cannot enter is a false claim on the form itself.
- `docs/FRICTION_LOG.md` said we "cannot demonstrate that a Ring MCP server
  exists". Ring documents one at
  `https://knowledge.appstore-mcp.ring.amazon.dev/mcp`; we had probed the API
  host and never read the page naming it. The correction is in the log, and the
  irony is the point: we turned an absent result into a conclusion, which is
  the exact failure the product exists to prevent.
- The same log said the rate limits were "not documented in enough detail to
  rely on". Ring publishes 100 TPS per partner and a full webhook retry
  schedule.

Before submission, re-read every artifact against the current official
documentation and the current competition rules, not against our memory of
them. `tests/test_docs_honesty.py` pins each correction.

### The ninth class: a claim read from the wrong pass

An explanation can be assembled from two passes that never coexisted. The
content rule read the encryption *value* from a device record (which persists
across observations) while testing whether the configurations *request* had been
made against the latest observation. Two defects came out of that one shape:

- A reading was discarded by a pass that could not see the device. A device's
  encryption reading is retained on its record after it leaves the inventory, but
  `collect_observation` never requests configurations when the inventory is
  empty, so the latest observation has `configurations=None`. The rule read that,
  reported `CONTENT_UNAVAILABLE`, and threw away a reading WITNESS had taken.
  The existing "reading survives" test passed only because its fixture gave the
  later observation a successful empty configurations response -- a shape the
  real adapter cannot produce.
- The assessment cited the latest observation regardless of which pass had taken
  the reading, so when the inspected device changed, the content assessment for
  the old device cited an observation that had inspected a *different* device.

The fix is to derive the whole assessment from the newest pass that actually
queried the device (`_encryption_reading` in `reconcile.py`): the value, the
field-presence check, the request that was made, the time and the evidence
reference all come from one observation. `rule_content_state` now takes that
observation and falls back to "not asked" -- reported against the latest pass --
when no pass ever queried the device.

The event rule had the same shape in the time dimension: lateness was measured
from the event's `retrieved_at`, which is re-stamped on every re-delivery, so an
event first seen on time was reported late by the polling interval. Lateness is
now measured from `_first_retrieval_times`, the earliest retrieval of that event
identity across the history.

When a rule combines a value that persists across observations with a check
about a single request, ask which pass each half came from -- and prefer deriving
both from the pass that actually made the request. When a rule subtracts two
timestamps, ask whether either can be re-stamped by a later pass. Pinned by
`tests/test_scenario_gaps.py`:
`test_configuration_reading_survives_a_device_leaving_the_real_pipeline`,
`test_content_assessment_cites_the_observation_that_took_the_reading`,
`test_content_assessment_never_cites_a_pass_that_did_not_query_the_device`,
`test_on_time_event_redelivered_later_is_not_reported_late`.

A fixture that builds a state the real adapter cannot produce will pass while the
real path is broken. When a test exists to pin a guarantee, check the fixture is
shaped like the pipeline it is standing in for.

## Submission artifacts

- `docs/FRICTION_LOG.md` — scored deliverable. Honest, specific, includes dead
  ends and unreproduced observations.
- `docs/DEMO_SCRIPT.md` — timed three-minute script with an explicit "what not
  to claim" section.
- `docs/SUBMISSION.md` — VERIFIED / DOCUMENTED / UNAVAILABLE / UNVERIFIED
  breakdown and the tests that pin each honesty claim.
- `docs/EVIDENCE_MATRIX.md` — per-incident-class evidence matrix: expected
  behaviour, required evidence, sources, limitations, false-positive risks,
  tests, and verification status. `tests/test_incidents.py` pins one negative
  guarantee per class; keep the two in step when behaviour changes.
