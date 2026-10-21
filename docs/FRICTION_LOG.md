# Friction log

An honest record of what we actually hit while building WITNESS on the Ring
Partner API. Everything below is something we observed or failed to observe
ourselves. Nothing is written as if we had verified it when we had not.

Confidence labels used throughout:

- **VERIFIED** — we ran it and saw the result.
- **OBSERVED** — we saw it happen, but not in a controlled or repeatable way.
- **DOCUMENTED** — it is in Amazon's or Ring's own documentation; we did not
  exercise it.
- **UNVERIFIED** — we could not confirm it.

Each entry closes with a **friction record** covering the six fields the
hackathon asks for — the task attempted, the steps taken, the expected versus
actual result, the severity, the workaround used, and an actionable suggestion —
so a reviewer can read any entry in that shape.

---

## 1. Ring developer access is gated behind a login we could not satisfy

**What happened.** The Ring Partner API is documented at
`developer.amazon.com` and requires an Amazon Developer account to reach the
Playground, the API reference, and any credentials. This build environment has
no Amazon Developer account, so we could not create an app, generate a token, or
read the reference pages while authenticated.

**How we confirmed the endpoint exists without credentials.** We probed
`api.amazonvision.com` unauthenticated and compared responses:

| Request | Result |
|---|---|
| `GET /` | **404** |
| `GET /v1/devices` | **401** |
| `GET /definitely-not-a-real-path-xyz` | **404** |
| `GET /v1/devices` with `Authorization: Bearer not-a-real-token` | **401** |

The contrast is the point: an unauthenticated request to a real route returns
401 (the route exists and an auth layer is guarding it), while a request to a
nonexistent route returns 404. That shows the host is routing requests and
enforcing authentication, rather than being a dead or parked domain. It does
**not** show that any particular endpoint returns useful data, and it is not a
substitute for a real authenticated call.

**Impact on the build.** The live Ring path could not be exercised end to end in
this environment. We therefore built the live adapter from the published
interface, tested its full HTTP behaviour against a mock transport, and marked
every Ring capability `DOCUMENTED` rather than `VERIFIED`. This is why the
product ships with a clearly-labelled simulated source: without one, we could
demonstrate nothing about withdrawal, encryption transitions, duplicates or late
arrival, because the Playground does not let you manufacture those conditions.

**Friction record**

- **Task attempted:** Reach the Ring Partner API to obtain a credential and make
  one authenticated read-only call.
- **Steps taken:** Probed `api.amazonvision.com` unauthenticated, comparing a
  known route against a nonexistent one (table above); attempted to reach the
  Playground at `developer.amazon.com/ring/console/playground`; then, once a
  Playground token was supplied, ran the real adapter against the live host
  (`GET /v1/devices`, `GET /v1/history/devices/{id}/events`).
- **Expected:** A real route would answer `401` and a nonexistent route `404`,
  confirming the host is live; the Playground would issue a token; the live
  adapter would authenticate and parse the response.
- **Actual:** The host behaved as expected — `401` on `/v1/devices`, `404` on a
  bogus path. The Playground redirected to an Amazon sign-in, so no token could
  be obtained from this environment without creating an account. When a token was
  supplied, the live calls succeeded: **HTTP 200** on `/v1/devices` with one
  synthetic device, **HTTP 200** on the history endpoint with zero events.
- **Severity:** High while unblocked-and-unknown; resolved once a token was
  available. The residual limitation is that the Playground cannot produce a
  withdrawal, an encryption transition, a duplicate or a late arrival.
- **Workaround used:** Built the live adapter from the published interface and
  tested its full HTTP behaviour against `httpx.MockTransport`; when a token
  arrived, exercised the real host and promoted only the two endpoints actually
  called to `VERIFIED`, leaving the rest `DOCUMENTED`. Shipped a clearly-labelled
  simulated source for the conditions the Playground cannot manufacture.
- **Actionable suggestion:** The Playground is a good sandbox, but it is gated
  behind an Amazon Developer sign-in and its token lasts 30 minutes. A documented
  sandbox-token flow that works from a credential-less CI environment — or a
  longer-lived hackathon-scoped token — would let automated builds exercise the
  live path instead of only the mock transport, and would make a live demo
  segment reproducible without racing the clock.

**Friction rating: high, then resolved.** The gate was the single largest
constraint on the project until a token was supplied.

---

## 2. A Ring MCP server exists and is documented; we probed for it the wrong way

**What happened.** MCP looked like an attractive integration route, because it
would let an agent reason over Ring data directly without us writing a bespoke
client. We went looking for a Ring MCP endpoint and probed a set of conventional
paths on the *API* host (`api.amazonvision.com`):

| Request | Result |
|---|---|
| `GET /mcp`, `/mcp/`, `/mcp/v1`, `/mcp/sse` | **404** |
| `GET /v1/mcp`, `/v1/mcp/` | **404** |
| `GET /api/mcp`, `/sse`, `/message` | **404** |
| `POST /mcp` (JSON body) | **404** |

Every one returned 404, and we originally recorded this as "we cannot
demonstrate that a Ring MCP server exists." **That conclusion was wrong, and we
are correcting it here.** Ring's own documentation describes a Ring Appstore
Knowledge MCP Server, and the docs give its exact URL — a host we never probed:

    https://knowledge.appstore-mcp.ring.amazon.dev/mcp

We re-probed that URL unauthenticated and got `GET /mcp → 405` (method not
allowed) and `POST /mcp → 200`, which is consistent with a live Streamable HTTP
MCP endpoint. The earlier 404s were our error: we assumed the MCP server, if it
existed, would be mounted on the API host, and we never read the page that names
it.

**What the MCP server actually is.** It is a *knowledge* server, not a device
server: it is "a remote Model Context Protocol server backed by an Amazon
Bedrock Knowledge Base" that exposes `search_docs` and `get_doc` over Ring's
documentation. It helps an AI coding assistant answer Ring integration
questions. It does **not** expose a user's devices or events.

**Impact.** Correcting this changes nothing about the build, and we are
recording it because our first version of this log overstated a negative.
WITNESS still does not use MCP, and the MCP server would not have given WITNESS
device data even if we had found it. The lesson is the one the product is about:
*an absent result is not evidence of absence.* We probed the wrong host, saw
404, and wrote down a conclusion the evidence did not support. That is exactly
the failure mode WITNESS exists to prevent, and we made it ourselves.

**Friction record**

- **Task attempted:** Find a Ring MCP server, to let an agent reason over Ring
  data without writing a bespoke client.
- **Steps taken:** Probed a set of conventional MCP paths on the API host
  (`/mcp`, `/mcp/sse`, `/v1/mcp`, `/api/mcp`, `POST /mcp`; table above).
- **Expected:** If a Ring MCP server existed, it would be mounted on the API host
  at a conventional path.
- **Actual:** Every probe returned `404`. The assumption was wrong: Ring
  documents the server at a different host entirely
  (`knowledge.appstore-mcp.ring.amazon.dev/mcp`), which we had not read.
- **Severity:** Medium — a documented feature was misread as a dead end.
- **Workaround used:** Read the page naming the server and re-probed the correct
  host (`GET → 405`, `POST → 200`), then corrected this entry.
- **Actionable suggestion:** The MCP server host is not discoverable by
  convention from the API reference. Linking it from the API documentation
  landing page, next to the other quick-start links, would have prevented the
  wrong-host probe.

**Friction rating: medium.** We misread a documented feature as a dead end. The
correction costs nothing, but the original entry would have been a false claim in
front of judges who know the Ring docs better than we do.

---

## 3. Event classification is not available on the endpoint we can read

This is the friction that shaped the product.

**What happened.** While designing around the documented Ring interfaces, we hit
a genuine capability gap. Ring's **motion webhook** carries a classification
inside its payload attributes — the webhook tells you whether the motion was a
person, a vehicle, an animal, or a package. Ring's **event history** endpoint
does not carry that field.

The two are not interchangeable. A webhook is a push, delivered at the moment of
the event, and it is only available if your integration is registered to receive
it and is online to receive it. Event history is a pull, available after the
fact, and it is the only one of the two you can query retrospectively.

**Why this matters more than it first appears.** A reasonable product would take
the webhook's classification and attach it to the history record. We decided not
to, because there is no field linking them. Matching a webhook to a history
record requires inferring identity from a timestamp and a device id, and that
inference can be wrong — two motion events seconds apart on the same camera are
not distinguishable by timestamp alone. Attaching the wrong classification to an
event is worse than attaching none.

**What we did instead.** WITNESS reports `CLASSIFICATION_UNAVAILABLE` for
history-derived events, and says exactly why: the endpoint it can read does not
expose the field, and it will not infer one. This became one of the product's
clearest demonstrations — a system that says "I cannot tell you what this was,
and here is the specific reason" is more trustworthy than one that guesses.

**Friction record**

- **Task attempted:** Attach an event classification (person / vehicle / animal /
  package) to events retrieved from the history endpoint.
- **Steps taken:** Compared the fields on the motion webhook payload against the
  fields on the event-history response.
- **Expected:** The classification available on the webhook would be present on,
  or linkable to, the history record for the same event.
- **Actual:** The history endpoint does not carry the field, and there is no
  identifier linking a webhook to a history record. Matching on timestamp and
  device id is ambiguous — two motion events seconds apart on one camera are
  indistinguishable.
- **Severity:** High — it removes a capability a reasonable product would offer.
- **Workaround used:** Report `CLASSIFICATION_UNAVAILABLE` with the specific
  reason, and refuse to infer a classification from timing.
- **Actionable suggestion:** Expose the classification (or a stable event
  identifier shared with the webhook payload) on the history endpoint. Without
  it, retrospective classification is not safely derivable by any consumer.

**Friction rating: high, and productive.** This friction is the product.

---

## 4. Absence is not withdrawal, and the API gives you no help distinguishing them

**What happened.** A device that stops appearing in the inventory response looks
identical, in the response payload, whether the owner unlinked the integration,
enabled end-to-end encryption, removed the device, or the request simply failed.
The API returns an empty or shortened list and no explanation.

**Why the naive handling is dangerous.** The obvious implementation is: "device
was there yesterday, not there today, therefore offline." For a caregiver
checking a camera, that is a false alarm at best. And a *failed* request —
timeout, rate limit, 500 — produces the same empty result as a successful
response that genuinely has no devices. Treating those as equivalent means a
transient network blip reads as "your camera is gone."

**What we did instead.** WITNESS separates the two at the transport layer before
any rule runs. A failed request becomes `SOURCE_UNAVAILABLE` and produces **no**
device conclusion at all. A successful response missing a device increments a
consecutive-absence counter, and only at or above the threshold does it become
`SOURCE_WITHDRAWN` — and even then the explanation names the several causes it
cannot distinguish between.

**Friction record**

- **Task attempted:** Determine, from the API alone, why a device stopped
  appearing in the inventory.
- **Steps taken:** Compared the inventory response for a device that is present,
  absent, and absent-because-the-request-failed.
- **Expected:** Some field or status code would distinguish "unlinked",
  "encrypted", "removed", and "request failed".
- **Actual:** All four look alike in the payload — a shortened or empty list with
  no explanation. A transport failure is distinguishable only by the absence of
  a success status, which a naive consumer ignores.
- **Severity:** High — the core design problem.
- **Workaround used:** Separate the failure at the transport layer before any
  rule runs (`SOURCE_UNAVAILABLE` produces no device conclusion), and require a
  consecutive-absence threshold before asserting `SOURCE_WITHDRAWN`, naming the
  causes it cannot distinguish.
- **Actionable suggestion:** A per-device reason field on the inventory response
  (for example "not shared with this integration") would let consumers report
  the real cause instead of an enumeration of possibilities.

**Friction rating: high.** This is the core design problem, and it is not solved
by the API. It has to be solved by the consumer.

---

## 5. End-to-end encryption silently removes devices from the API

**What happened.** When a Ring owner enables end-to-end encryption on a device,
that device stops appearing in the API's device inventory. From the client's
point of view this is indistinguishable from a device being removed or the
integration being unlinked — see friction 4.

We could not test this transition ourselves without a device and an account. It
is **DOCUMENTED** behaviour — and we have since confirmed the exact wording in
Ring's own developer FAQ, which distinguishes two encryption modes:

- **E2EE.** "E2EE devices are not returned by any Ring Partner API, as users
  can't grant partner apps access to E2EE devices." And, on a device that was
  already connected: "An E2EE device drops out of your app's view entirely."
- **TAKE** (Throw Away the Key). "A TAKE device is visible and callable via the
  Ring Partner API, but video requests return encrypted content."

So there are two distinct encrypted states with different observable behaviour:
one removes the device from the inventory (E2EE), the other keeps it visible but
unreadable (TAKE). That is precisely why our simulated scenario includes an
encryption transition, and why the status taxonomy keeps content readability
(`SOURCE_ENCRYPTED`, `CONTENT_VISIBLE`, `CONTENT_UNAVAILABLE`) separate from
presence (`OBSERVED`, `SOURCE_WITHDRAWN`).

**Impact.** It is the reason `SOURCE_ENCRYPTED` and `SOURCE_WITHDRAWN` are
separate statuses rather than one "gone" state, and the reason the withdrawal
explanation explicitly lists encryption as a possible cause it cannot rule out.

**Friction record**

- **Task attempted:** Represent the state of an encrypted device accurately.
- **Steps taken:** Read Ring's developer FAQ on TAKE and E2EE, and compared the
  two modes' observable behaviour against the API responses.
- **Expected:** One "encrypted" state with one behaviour.
- **Actual:** Two modes with different behaviour — E2EE removes the device from
  the inventory entirely; TAKE keeps it visible but returns content the app
  cannot read. The configurations payload exposes a single boolean
  (`e2e_encryption.enabled`) that does not say which mode is in force.
- **Severity:** Medium — a real constraint we designed around without being able
  to reproduce it.
- **Workaround used:** Split content readability (`SOURCE_ENCRYPTED`,
  `CONTENT_VISIBLE`, `CONTENT_UNAVAILABLE`) from presence (`OBSERVED`,
  `SOURCE_WITHDRAWN`), and list encryption among the causes a withdrawal cannot
  rule out.
- **Actionable suggestion:** If the API can report which encryption mode is
  active, exposing it would let consumers distinguish "visible but unreadable"
  from "gone", which today require different user-facing messages.

**Friction rating: medium.** A real constraint we designed around without being
able to reproduce it. The behaviour itself is documented, not inferred.

---

## 6. We could not distinguish "no events" from "events we cannot see"

**What happened.** The event history endpoint returns records for a device over
a window. If it returns none, that is consistent with both "nothing happened" and
"events happened but were not returned to this integration" — for example
because of encryption, retention limits, or a filter we do not control.

**Impact.** WITNESS never renders an empty event list as "nothing happened." It
reports the successful retrieval and states what it does not establish. This is
the principle the whole product rests on: *missing evidence is not proof that
nothing happened.*

**Friction record**

- **Task attempted:** Report whether anything happened at a device during a
  window, using the history endpoint.
- **Steps taken:** Requested event history for a device and considered what an
  empty result establishes.
- **Expected:** An empty result would mean no events occurred.
- **Actual:** An empty result is consistent with both "nothing happened" and
  "events happened but were not returned to this integration" — through
  encryption, retention limits, or a filter outside our control.
- **Severity:** Medium — no API change would fix it; it is inherent in reading a
  filtered view of someone else's data.
- **Workaround used:** Never render an empty event list as "nothing happened".
  Report the successful retrieval and state explicitly what it does not
  establish.
- **Actionable suggestion:** Where a device's events are withheld from an
  integration rather than genuinely absent, a distinguishing marker on the
  response would let consumers say so instead of hedging.

**Friction rating: medium.** No API change would fix this; it is inherent in
reading a filtered view of someone else's data.

---

## 7. Rate limits and retry behaviour are documented; we did not need to guess

**What happened.** Early in the build we were unsure of the exact rate limits and
retry semantics, and we did not want to invent a backoff policy against an
imagined contract. We have since read the published numbers.

**What the documentation actually says.** The API reference states a default
rate of **100 requests per second (TPS) per partner**, scoped per
`client_id` across all endpoints, with `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `Retry-After` headers, and instructs clients to
"use exponential backoff starting at 1 second and respect the `Retry-After`
header." For webhooks, Ring publishes a full retry schedule — 1s, 5s, 30s, 2m,
10m, 1h, six retries after the initial attempt — with a dead-letter queue and
72-hour event retention.

**What we did instead.** The adapter still classifies `429` as `RATE_LIMITED`
and does not retry on its own; it reports the condition and lets the caller
decide. That is a deliberate product choice, not a gap in the docs: a read-only
evidence tool should surface a rate limit as a `SOURCE_UNAVAILABLE` fact rather
than silently retry and blur when the observation was actually taken.

**Correction.** Our first version of this entry said the limits were "not
documented in enough detail to rely on." That was wrong, and we are correcting
it. The numbers are published; we simply had not read them when we wrote the
entry.

**Friction record**

- **Task attempted:** Choose a backoff and retry policy we could rely on.
- **Steps taken:** Read the published rate-limit and webhook-retry documentation
  instead of guessing.
- **Expected:** We would have to invent a policy against an imagined contract.
- **Actual:** The numbers are published (100 TPS per `client_id`;
  `X-RateLimit-*` and `Retry-After` headers; a six-step webhook retry schedule
  with a dead-letter queue and 72-hour retention). The documentation was
  sufficient; our first draft of this entry simply had not read it.
- **Severity:** Low — a documentation-reading miss on our side, corrected before
  submission.
- **Workaround used:** None needed. The adapter still classifies `429` as
  `RATE_LIMITED` and does not retry silently — a deliberate product choice, so a
  rate limit is surfaced as a fact rather than blurring when the observation was
  actually taken.
- **Actionable suggestion:** None. Recorded because our first version overstated
  a negative.

**Friction rating: low.** A documentation-reading miss on our side, corrected
before submission. The adapter's no-retry behaviour stands on its own merits.

---

## 8. What we deliberately did not do

- We did **not** create an Amazon Developer account, because the task
  constraints prohibit creating accounts.
- We did **not** purchase anything, provision any AWS resource, or incur any
  cost.
- We did **not** print, log, commit or transmit any credential.
- We did **not** perform a live authenticated Ring call, and we have not
  presented one as having happened anywhere in the product, the UI, the README
  or this log.
- We did **not** record a Ring session and replay it as if it were live. The
  simulated source is generated from a scenario table, and it says so in every
  payload it produces.

---

## Product feedback (required by the submission form)

The submission form asks five questions about each tool, API or SDK we used. We
used one: the **Ring Partner API**, via the official `ring-api-helloworld`
sample and Ring's developer documentation.

**Which developer tools, APIs and SDKs did you use, and for what?**
The Ring Partner API, read-only. We used the documented endpoints
`GET /v1/devices`, `GET /v1/devices/{id}/status`,
`GET /v1/devices/{id}/configurations` and
`GET /v1/history/devices/{id}/events`. We read Ring's developer documentation
and the official Python sample scripts (`scripts/` in `ring-api-helloworld`) to
confirm endpoint paths and the JSON:API envelope. We did **not** use the Ring
MCP server (it is a documentation-knowledge server, not a device-data server)
and did **not** register an app or use OAuth.

**What worked well?** The API surface is small and consistently shaped: every
response uses the same JSON:API envelope, and the endpoint paths are unambiguous
once found. The documentation's honesty about its own gaps is unusually good —
the developer FAQ states plainly that E2EE devices "are not returned by any Ring
Partner API", which let us design the presence/content split correctly rather
than guessing. The published rate limits and webhook retry schedule meant we did
not have to invent a backoff policy.

**What needs work?** Three things, in order of impact on us:
1. The Playground, the documented sandbox, is gated behind an Amazon Developer
   sign-in, so a credential-less build cannot exercise the live path. See
   friction 1.
2. The event-history endpoint does not carry the classification the webhook
   payload has, and nothing links a webhook to a history record. Retrospective
   classification is therefore not safely derivable. See friction 3.
3. A device that stops appearing in the inventory gives no reason — unlinked,
   encrypted, removed and request-failed are indistinguishable in the payload.
   See friction 4.

**How was your onboarding experience (zero to hello world)?** Documentation-led
rather than interactive. The endpoint list and the sample scripts got us to a
correctly-shaped request quickly, but "hello world" — a first authenticated
response — was not reachable without an account, so our hello world was a mock
transport rather than a live call. The gap is not conceptual, it is access.

**Would you build with these devices and services again? Yes.** The API is
read-only by design and well-documented, and the constraints it imposes (what it
will not tell you) are stated rather than hidden. That is the property we most
want in a source we have to reason about. The one change that would make it
substantially easier is an access path for unauthenticated or CI environments,
as noted in friction 1.

---

## Summary

The Ring Partner API is a read-only window onto a system that is deliberately
withholding information: devices disappear when encrypted, events arrive without
classification, and a failed request looks exactly like an empty one. Building on
it well is not a matter of calling more endpoints. It is a matter of being
precise about which of those situations you are actually in.

That is what WITNESS does, and it is why the product is not a wrapper around a
model call: the model is optional, and the value is in the reconciliation logic
that the API does not provide.
