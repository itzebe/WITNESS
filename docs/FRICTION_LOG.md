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

**Friction rating: high.** This is the single largest constraint on the project.

---

## 2. An MCP route for Ring is plausible but we could not confirm one

**What happened.** MCP looked like an attractive integration route, because it
would let an agent reason over Ring data directly without us writing a bespoke
client. We went looking for a Ring MCP endpoint and probed a set of conventional
paths on the same host:

| Request | Result |
|---|---|
| `GET /mcp`, `/mcp/`, `/mcp/v1`, `/mcp/sse` | **404** |
| `GET /v1/mcp`, `/v1/mcp/` | **404** |
| `GET /api/mcp`, `/sse`, `/message` | **404** |
| `POST /mcp` (JSON body) | **404** |

Every one returned 404. We also saw a **405 (Method Not Allowed)** on one probe
earlier in the build, which would have indicated a live route expecting a
different verb — but **we were unable to reproduce that result** when we went
back to confirm it, and we cannot now say which path produced it. We are
recording it as an observation we could not reproduce rather than as a finding.

**Confidence: UNVERIFIED.** We cannot demonstrate that a Ring MCP server exists
at any path we tried. A 405 seen once and never reproduced is not evidence, and
we are not going to present it as one.

**Impact.** MCP is not part of this build. We do not depend on it, we do not
claim it, and nothing in the product would change if it were confirmed tomorrow.
If a team with Amazon Developer credentials wants to pursue it, the right first
step is to read the authenticated reference rather than to probe paths.

**Friction rating: low.** Time spent on a dead end, but no effect on the
product. Included because an honest log records the dead ends too.

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

**Friction rating: high.** This is the core design problem, and it is not solved
by the API. It has to be solved by the consumer.

---

## 5. End-to-end encryption silently removes devices from the API

**What happened.** When a Ring owner enables end-to-end encryption on a device,
that device stops appearing in the API's device inventory. From the client's
point of view this is indistinguishable from a device being removed or the
integration being unlinked — see friction 4.

We could not test this transition ourselves without a device and an account. It
is **DOCUMENTED** behaviour, and it is precisely why our simulated scenario
includes an encryption transition.

**Impact.** It is the reason `SOURCE_ENCRYPTED` and `SOURCE_WITHDRAWN` are
separate statuses rather than one "gone" state, and the reason the withdrawal
explanation explicitly lists encryption as a possible cause it cannot rule out.

**Friction rating: medium.** A real constraint we designed around without being
able to reproduce it.

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

**Friction rating: medium.** No API change would fix this; it is inherent in
reading a filtered view of someone else's data.

---

## 7. Rate limits and retry behaviour are not documented in enough detail to rely on

**What happened.** We could not confirm the exact rate limits, the retry
semantics, or the delivery guarantees of the webhook from an authenticated
source. We did not want to guess, because guessing here would mean either
hammering an API we do not have credentials for, or writing retry logic against
an imagined contract.

**What we did instead.** The adapter classifies `429` as `RATE_LIMITED` and does
not retry. It reports the condition honestly and lets the caller decide. We
documented the uncertainty rather than inventing a backoff policy.

**Friction rating: low for the build, but a real risk for production.** Flagged
here because a team with credentials should measure it rather than assume.

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

## Summary

The Ring Partner API is a read-only window onto a system that is deliberately
withholding information: devices disappear when encrypted, events arrive without
classification, and a failed request looks exactly like an empty one. Building on
it well is not a matter of calling more endpoints. It is a matter of being
precise about which of those situations you are actually in.

That is what WITNESS does, and it is why the product is not a wrapper around a
model call: the model is optional, and the value is in the reconciliation logic
that the API does not provide.
