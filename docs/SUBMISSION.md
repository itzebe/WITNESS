# Submission notes

Everything a reviewer needs to verify this submission, in one place.

## What to run

```bash
pip install -r requirements.txt
python -m pytest tests/ -q          # expect 281 passed
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

This builds a chain, tampers with it six different ways, and shows each is
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
- 281 automated tests pass.

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
- Exact rate limits and retry semantics.
- Whether a Ring MCP server exists. We probed conventional paths; all returned
  404. One 405 was seen once and not reproduced, so we do not treat it as
  evidence.

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

**Bee / Ring.** The submission is a read-only Ring Partner API integration. It
makes no device actions, requests no write scope, and issues no emergency or
safety claims.

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
tests/               281 tests
scripts/             render_check.js, demo_tamper.py
docs/                FRICTION_LOG.md, DEMO_SCRIPT.md, SUBMISSION.md
```
