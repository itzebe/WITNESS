# Submission notes

Everything a reviewer needs to verify this submission, in one place.

## What to run

```bash
pip install -r requirements.txt
python -m pytest tests/ -q          # expect 304 passed
python -m uvicorn witness.api:app --port 8000
```

Open `http://127.0.0.1:8000`. The simulated source is selected by default.

## How to reproduce the core demonstration in 60 seconds

The four-frame scenario is driven from the Integration tab. Each frame is a
distinct condition:

| Frame | Condition | Expected result |
|---|---|---|
| 1 | Baseline: two cameras visible | Both `OBSERVED` |
| 2 | One camera absent from a successful response | `NOT_OBSERVED` — **not** a withdrawal |
| 3 | The source request fails | `SOURCE_UNAVAILABLE` — **no** device conclusion |
| 4 | Source recovers, camera still absent | `SOURCE_WITHDRAWN` asserted, with causes listed |

Or over the API:

```bash
curl -X POST localhost:8000/api/simulated/reset
for i in 1 2 3 4; do
  curl -s -X POST localhost:8000/api/observe/ring-simulated > /dev/null
  curl -s -X POST localhost:8000/api/simulated/advance > /dev/null
done
curl -s localhost:8000/api/reconcile/ring-simulated | python -m json.tool
```

## How to verify the ledger claim

```bash
python scripts/demo_tamper.py
```

This builds a chain, tampers with it five different ways, and shows each is
detected and named. It then demonstrates the limitation the product openly
admits: a full rewrite verifies cleanly.

## What is verified, and what is not

We are stating this plainly because the difference matters.

### VERIFIED — we ran it and saw the result

- The full status taxonomy, over the HTTP API and through the UI.
- Reconciliation over observation history, including the consecutive-absence
  threshold and the source-failure path.
- Ledger tamper detection across modification, deletion, reordering, link
  breakage and status laundering.
- The Ring adapter's complete HTTP behaviour — auth header, URL shape, query
  parameters, JSON:API unwrapping, and all error classifications — against
  `httpx.MockTransport`.
- Credential redaction: no token appears in any error message or API response.
- The front end renders every view against real payloads (headless harness,
  `scripts/render_check.js`).
- 304 automated tests pass.

### DOCUMENTED — in the published interface, not exercised by us

- Ring device discovery, device status, device configurations, event history,
  user profile, live video.
- End-to-end encryption removing a device from the API inventory.
- Webhook delivery carrying a classification that event history does not.

### UNAVAILABLE

- Motion webhooks (requires a registered, reachable integration).
- Event classification via the history endpoint (the field is not exposed).
- Device control and any write operation (read-only by design).

### UNVERIFIED — we could not confirm it

- **A live authenticated Ring API call.** No Amazon Developer credentials were
  available in this environment. We probed the host unauthenticated to confirm
  it is live and auth-guarded (`/v1/devices` → 401, nonexistent path → 404), but
  that is not a real call and we do not present it as one.

Two entries that were previously listed here are now **DOCUMENTED**, not
unverified, because we found them in Ring's own reference after the first draft
of this file:

- **Rate limits and retry behaviour.** Published: 100 requests/second per
  partner `client_id`, with `Retry-After` and `X-RateLimit-*` headers, and a
  documented webhook retry schedule (1s, 5s, 30s, 2m, 10m, 1h; six retries;
  dead-letter queue; 72-hour retention).
- **The Ring MCP server.** It exists and is documented — a *knowledge* server at
  `https://knowledge.appstore-mcp.ring.amazon.dev/mcp`, exposing `search_docs`
  and `get_doc` over Ring's docs. It is not a device-data server. Our earlier
  claim that we "could not confirm a Ring MCP server exists" was wrong; see
  `docs/FRICTION_LOG.md` entry 2 for the correction.

## The live path

Set `RING_ACCESS_TOKEN` to a token from the Ring Developer Playground and
restart. The same code path runs live:

```bash
export RING_ACCESS_TOKEN="<playground token>"
python -m uvicorn witness.api:app --port 8000
```

`GET /api/ring/connection` reports whether credentials are configured and
exposes only a 12-character fingerprint, never the token.

## Track

**Ring** (primary track). The submission is a read-only Ring Partner API
integration. It makes no device actions, requests no write scope, and issues no
emergency or safety claims.

Ring is the only track WITNESS can enter. The other three are not available to
this build:

| Track | Why not |
|---|---|
| Fire TV | Needs an app running on Fire OS/Vega OS, on device or in the Fire TV/Vega simulator. WITNESS has no Fire TV surface. |
| Alexa+ | Needs a working Agent Skill or a self-hosted MCP server at spec 2025-11-25+. WITNESS exposes a plain HTTP read model, not an MCP server. |
| Bee | Rules require live data from a Bee device or an Apple Watch running Bee software, and "mentioning Bee without demonstrating a live data interaction does not count." We have no Bee hardware or data. |
| Open Source (mini) | Requires a contribution made to a public repository during the submission window, with a contribution URL. Our own repo being open source is not itself a contribution. |
| AWS Builder (mini) | Requires a documented AWS service integration. WITNESS is deliberately model-free and calls no AWS service. |

Ring is also the correct choice on the merits: the rules name **caretaking**,
**accessibility**, and **IoT home automation** as priority categories, and the
judging guidance names "non-security use case (accessibility aid, care-taking
monitoring)" as the *creative* end of the Ring spectrum, against "motion
detection alert, basic doorbell live view" as the obvious end. WITNESS is aimed
squarely at the creative end.

### Ring track requirement: simulator or real device

The rules are explicit: for Ring, "The demo video must show the project working
through a simulator or an actual Ring device you have for testing."

This is the submission's single largest compliance risk, and it is stated here
rather than buried:

- WITNESS ships two sources. `ring` is a real HTTP client for the Ring Partner
  API. `ring-simulated` is a scenario table WITNESS generates itself; it does
  **not** contact Ring and does **not** replay the Ring Playground.
- Ring does provide an official simulator path. The Playground and sandbox are
  documented ("Sandbox Environment — A testing environment with synthetic Ring
  devices and events"), and a free Ring developer account reaches them.
- **We have not run the live path against the Playground or sandbox in this
  environment, because we hold no Amazon Developer credentials here.** So the
  video as currently scripted demonstrates WITNESS's own scenario, which is not
  the same thing as the Ring simulator.

**This must be resolved before submission.** The honest options are, in order of
preference: (1) obtain a free Ring developer account, point the `ring` adapter
at the Playground, and record the video against the real simulator; or (2) if
that is impossible, label the demo plainly as WITNESS's own scenario and state
in the submission text that the Ring simulator path was not exercised. Option 2
is a materially weaker submission. See `docs/FRICTION_LOG.md` for the probing we
did without credentials.

## Honesty boundaries enforced by tests

These are not aspirations; they are asserted in the suite.

| Claim | Test |
|---|---|
| The Ring adapter never reports `VERIFIED` | `test_ring_capabilities_do_not_claim_verified_status` |
| Simulated data is always labelled simulated | `test_simulated_source_is_labelled_simulated`, `test_simulated_capabilities_are_all_labelled_simulated` |
| A failed request is never read as a withdrawal | `test_source_failure_frame_does_not_become_a_withdrawal` |
| An empty success is not a failure | `test_successful_empty_response_is_a_success_not_a_failure` |
| An unreadable payload is not an empty one | `test_error_envelope_does_not_become_an_asserted_withdrawal`, `test_unreadable_history_does_not_fabricate_a_duplicate_finding` |
| The ledger's limitation is asserted, not hidden | `test_full_rewrite_is_not_detectable_by_a_local_verifier` |
| No token leaks through the API | `test_ring_connection_never_leaks_a_token` |
| The UI states the ledger limitation | `test_ui_states_the_ledger_limitation` |
| The overview verdict never contradicts itself | `test_overview_verdict_never_contradicts_itself` |

## What WITNESS does not claim

- It does not detect emergencies.
- It does not make anyone safer, and it does not say so.
- It does not tell you whether a person is present, or what an event was.
- It does not claim its ledger is immutable.
- It does not claim a live Ring call has been made in this environment.

It reports the quality, completeness and limitations of the evidence available
to it. Missing evidence is not proof that nothing happened.

## Project structure

```
witness/engine/      Deterministic core: statuses, rules, reconciliation, ledger
witness/adapters/    Ring (live, read-only) and simulated sources
witness/api.py       HTTP read model
web/                 Front end, no build step
tests/               304 tests
scripts/             render_check.js, demo_tamper.py
docs/                FRICTION_LOG.md, DEMO_SCRIPT.md, SUBMISSION.md
```
