# WITNESS — Incident Evidence Matrix

This document is the single place that answers, for each incident class WITNESS
handles, four questions: what does the household need to know, what does WITNESS
actually do, what evidence justifies that, and how confident are we that it
works.

It is written to be checked against the code, not trusted. Every row names the
rule, the file, and the tests that back it. Where a claim is not backed, the row
says so.

## How to read the verification status

| Status | Meaning |
|---|---|
| **VERIFIED LIVE** | Exercised against the real Ring source, with sanitized evidence recorded in `docs/SUBMISSION.md` (VERIFIED LIVE). |
| **VERIFIED BY SIMULATION** | Exercised through the deterministic engine against fixture or scripted observations. Proves the interpretation, not that the live service emits the condition. |
| **PARTIALLY VERIFIED** | Some requirements demonstrated; specific evidence remains unverified. |
| **DOCUMENTED** | Described in code or docs, not sufficiently demonstrated. |
| **UNVERIFIED** | Insufficient evidence to establish the behaviour. |

A live endpoint returning data does **not** verify WITNESS's interpretation of
that data. The two are recorded separately.

## What is verified live, and what that does not cover

Three read-only endpoints were exercised against the Ring Developer Playground
on 2026-10-21, all HTTP 200 (`docs/SUBMISSION.md`, VERIFIED LIVE):

- `GET /v1/devices` — one synthetic device, `attributes` = `name`, `image_url`.
- `GET /v1/devices/{id}/configurations` — `image_enhancements`, `audio`,
  `motion_detection`, `e2e_encryption`.
- `GET /v1/history/devices/{id}/events` — zero events.

That is the whole of the live evidence. It covers **device discovery, configuration
retrieval and history retrieval**, and nothing else. The Playground exposes one
device, emits no events, and cannot produce a withdrawal, an encryption
transition, a duplicate delivery, a late arrival, or an outage. Every row below
for those classes is therefore **VERIFIED BY SIMULATION** at best.

The verification is **dated**. It used a short-lived Playground token (~30
minutes), which has since expired, so no valid Ring credential is present now
and the live path cannot be re-run without a fresh one. The result above stands;
its reproducibility does not depend on the credential still existing.
`docs/SUBMISSION.md` records the current credential state.

## Thresholds (the whole of the policy)

From `witness/engine/rules.py`, `Policy`:

| Threshold | Value | Why |
|---|---|---|
| `withdrawal_threshold` | 2 consecutive successful absences | One absence is common API behaviour and proves nothing. |
| `event_lateness_threshold` | 5 minutes | Below this, delay is not worth annotating. |
| `staleness_threshold` | 30 minutes | Older evidence is not presented as current. |
| `reconciliation_window` | 20 observations | Bounds the absence count. |

---

## Incident classes

### 1. Withdrawal — a device stops being returned

| Column | Content |
|---|---|
| **Incident class** | A previously observed device is absent from successful inventory responses. |
| **User-facing question** | "Did my camera disappear?" |
| **Expected WITNESS behavior** | Below threshold: `NOT_OBSERVED`, stating the count and how many more are needed. At or above threshold: `SOURCE_WITHDRAWN`, stating that the source stopped exposing it and that WITNESS cannot say why. Never asserts physical removal or tampering. |
| **Required evidence** | ≥2 consecutive *successful* inventory responses omitting the device, plus a prior successful observation that included it. |
| **Evidence source** | Ring `GET /v1/devices` (live); scripted frames (`witness/adapters/simulated.py`). |
| **Evidence limitations** | A failed inventory is not evidence of absence and does not advance the count. The reason for withdrawal is not observable: unlinking, enabling end-to-end encryption, and leaving the account are indistinguishable from the API. |
| **False-positive risks** | A single absence (pagination or transient API behaviour); an outage misread as absence; a device that was never observed being called withdrawn. |
| **Verification method** | Automated tests over the deterministic engine; scripted scenario. |
| **Verification status** | **VERIFIED BY SIMULATION**. Not producible on the Playground. |
| **Supporting evidence** | `rule:inventory_absent_below_threshold`, `rule:inventory_absent_at_or_above_threshold`, `consecutive_successful_absences` in `witness/engine/rules.py`. Tests: `test_single_absence_is_not_observed_and_not_withdrawn`, `test_repeated_absences_reach_withdrawal_threshold`, `test_withdrawal_does_not_claim_tampering`, `test_failed_request_does_not_advance_the_withdrawal_counter`, `test_withdrawal_survives_absence_longer_than_the_window`, `test_empty_inventory_with_no_prior_history_is_not_a_withdrawal`, `test_failed_inventory_never_becomes_a_withdrawal`, `test_withdrawal_requires_consecutive_successes_not_elapsed_time` (`tests/test_reconciliation.py`, `tests/test_incidents.py`). |
| **Remaining work** | A live withdrawal cannot be induced on the Playground. Confidence would rise only from a real account where a device is genuinely unlinked. |

**Explicit non-claim:** an empty event history is never treated as a withdrawal.
An empty history is a *successful* response reporting no events, and the device
is still `OBSERVED`. Pinned by `test_empty_event_history_does_not_produce_a_withdrawal`.

---

### 2. Encryption transition — `e2e_encryption` changes

| Column | Content |
|---|---|
| **Incident class** | A device's reported encryption state differs between two observations. |
| **User-facing question** | "Why can I no longer see this camera's content — and did something change?" |
| **Expected WITNESS behavior** | Report the **current** reading only: `SOURCE_ENCRYPTED` when `e2e_encryption.enabled` is true, `CONTENT_VISIBLE` when it is explicitly false, `CONTENT_UNAVAILABLE` when the field is absent, unreadable, or the request failed. WITNESS does **not** emit a transition conclusion; no status in the taxonomy asserts one. |
| **Required evidence** | A successful configurations response carrying a readable `e2e_encryption` object with a boolean `enabled`. |
| **Evidence source** | Ring `GET /v1/devices/{id}/configurations` (live, HTTP 200, `e2e_encryption` present); scripted frames. |
| **Evidence limitations** | The field states the *current* configuration, not a transition. The API exposes no configuration history, so WITNESS cannot establish a before-and-after from the source. The field does not distinguish encryption modes (e.g. E2EE vs TAKE), so WITNESS does not name one. |
| **False-positive risks** | Reading an absent field as `false` (would claim content is readable when it is unknown); reading a failed request as `false`; re-stamping a stale reading with the latest observation's time. |
| **Verification method** | Automated tests; live field presence confirmed. |
| **Verification status** | **PARTIALLY VERIFIED**. The field's presence is VERIFIED LIVE; the interpretation (encrypted / visible / unknown) is VERIFIED BY SIMULATION; the transition itself is **UNVERIFIED** — no historical snapshots exist to establish it. |
| **Supporting evidence** | `rule:content_encrypted`, `rule:content_not_encrypted`, `rule:content_state_unknown` in `witness/engine/rules.py`; `_encryption_reading_at` in `witness/engine/reconcile.py`. Tests: `test_encrypted_device_is_source_encrypted`, `test_unencrypted_device_is_content_visible`, `test_absent_encryption_field_is_unknown_not_false`, `test_failed_configurations_is_unknown_not_unencrypted`, `test_unreadable_encryption_value_does_not_deny_the_field`, `test_encryption_reading_keeps_the_time_it_was_taken`, `test_current_encryption_value_does_not_assert_a_transition`, `test_encryption_field_absent_is_unknown_not_unencrypted`, `test_failed_configurations_is_unknown_not_encrypted_and_not_plaintext`, `test_encryption_reading_survives_the_device_leaving_the_inventory`. |
| **Remaining work** | Configuration snapshots over time would be required to establish a genuine transition. WITNESS does not currently retain per-device configuration history beyond what each observation records. |

**Explicit non-claim:** the presence of `e2e_encryption` does not prove a
transition occurred. Pinned by `test_current_encryption_value_does_not_assert_a_transition`.

---

### 3. Duplicate event — repeated delivery

| Column | Content |
|---|---|
| **Incident class** | The source returns more than one record for the same event. |
| **User-facing question** | "Was there one event or two?" |
| **Expected WITNESS behavior** | Collapse records sharing a deterministic identity key, report `EVENT_DUPLICATE` with the count and the key, and state that identical records do not prove a single real-world event. |
| **Required evidence** | ≥2 records in one history response sharing an identity key. |
| **Evidence source** | Ring history endpoint (live returns zero events); scripted frames include a retried delivery. |
| **Evidence limitations** | The live source emitted no events, so live duplicate behaviour is unobserved. Ring documents that deliveries may be retried and ordering is not guaranteed, but WITNESS has not seen a duplicate from the live service. |
| **False-positive risks** | Two genuinely distinct events merged because they share type and time (possible only when the source provides no id). |
| **Verification method** | Automated tests over `deduplicate_events`. |
| **Verification status** | **VERIFIED BY SIMULATION**. |
| **Supporting evidence** | `EventRecord.identity_key()` in `witness/engine/models.py`; `deduplicate_events` and `rule:event_duplicate` in `witness/engine/rules.py`. Tests: `test_duplicate_event_is_detected_and_counted_once`, `test_duplicate_does_not_claim_only_one_real_event_occurred`, `test_deduplication_key_rule_is_deterministic`, `test_event_with_source_id_deduplicates_on_id_not_time`, `test_distinct_event_ids_are_not_merged_even_when_content_is_identical`, `test_records_without_ids_merge_only_on_type_and_time`, `test_duplicate_detection_is_reported_and_counted_once`. |
| **Remaining work** | A live duplicate would need a registered webhook or a live device emitting events. |

**Identity rule (the whole of it):** key on `id:<event_id>` when the source
provides an id; otherwise on `tt:<event_type>|<source_time>`. Deliberately
conservative: it merges records that are genuinely identical and does not merge
records that differ in type or time.

---

### 4. Late-arriving event — source time precedes retrieval time

| Column | Content |
|---|---|
| **Incident class** | An event's source-reported time is more than 5 minutes before the time it was retrieved. |
| **User-facing question** | "Would I have seen this when it happened?" |
| **Expected WITNESS behavior** | Annotate the event `EVENT_LATE` with the delay, and state that a real-time view at the event's time would not have shown it, and that the delay does not make the event inaccurate. |
| **Required evidence** | A successful history response containing an event whose `reported_at` is known and whose `retrieved_at - reported_at` exceeds the threshold. |
| **Evidence source** | Ring history endpoint (live returns zero events); scripted frames include a late event. |
| **Evidence limitations** | Requires a trustworthy source-reported time. If the source reports no time, no lateness claim is possible. Clock skew between the source and WITNESS is not detectable from the data. |
| **False-positive risks** | Claiming lateness when the source time is unknown (there is no interval to measure); a slow request being attributed to the source rather than to retrieval. |
| **Verification method** | Automated tests, including the threshold boundary. |
| **Verification status** | **VERIFIED BY SIMULATION**. |
| **Supporting evidence** | `rule:event_late` in `witness/engine/rules.py`; `parse_iso` returning `None` for unparseable times in `witness/engine/models.py`. Tests: `test_late_event_is_flagged_late`, `test_lateness_threshold_is_respected`, `test_event_with_no_source_time_is_not_claimed_to_be_late`, `test_out_of_order_events_in_one_response_are_judged_individually`, `test_lateness_boundary_is_exclusive_at_the_threshold`. |
| **Remaining work** | A live late event requires a live device producing events. |

**Explicit non-claim:** an event is never called late without a source-reported
time. Pinned by `test_event_with_no_source_time_is_not_claimed_to_be_late`.

---

### 5. Outage / evidence unavailability

| Column | Content |
|---|---|
| **Incident class** | A request fails, or a request succeeds but the evidence needed is absent. |
| **User-facing question** | "Is my home fine, or could WITNESS not look?" |
| **Expected WITNESS behavior** | Distinguish a source-level failure (`SOURCE_UNAVAILABLE`) from a successful empty result, and from an unknown content state (`CONTENT_UNAVAILABLE`). State explicitly that a failed request is evidence that WITNESS could not look — not evidence about the device. |
| **Required evidence** | A typed `Failure` on the relevant `FetchResult`, carrying a `FailureKind`. |
| **Evidence source** | Ring API responses (live 401 observed unauthenticated); `httpx.MockTransport` for the full error matrix; scripted frames. |
| **Evidence limitations** | Rate limiting and server errors are classified but not reproduced live. A redirect to an unconfigured host is treated as transport failure, not followed. |
| **False-positive risks** | A 401 reported as an empty home; a timeout reported as a withdrawal; a malformed payload reported as a short device list. |
| **Verification method** | Automated tests over `classify_http_status` and the reconciliation rules; adapter tests against `httpx.MockTransport`. |
| **Verification status** | **VERIFIED BY SIMULATION** for the classification and interpretation; **VERIFIED LIVE** only for the fact that the host answers (401 unauthenticated, 404 on a bogus path). |
| **Supporting evidence** | `FailureKind`, `RESPONSE_FAILURES`, `TRANSPORT_FAILURES`, `classify_http_status` in `witness/engine/models.py`; `rule:source_request_failed` in `witness/engine/rules.py`. Tests: `test_auth_failure_is_a_source_failure_not_an_empty_home`, `test_http_status_maps_to_the_documented_failure_kinds`, `test_partial_response_keeps_the_readable_axis_and_flags_the_other`, `test_missing_history_is_not_reported_as_no_events_occurred`, `test_stale_evidence_is_flagged_and_not_presented_as_current`, `test_successful_empty_response_differs_from_failure`, `test_source_level_failure_is_reported_once_not_per_device`, `test_failed_request_does_not_prove_device_offline`, `test_unreadable_envelopes_are_reported_as_problems`. |
| **Remaining work** | Live rate-limit and server-error behaviour is unobserved. |

**Explicit non-claim:** WITNESS never concludes that nothing happened because it
could not obtain evidence.

---

### 6. Classification unavailable

| Column | Content |
|---|---|
| **Incident class** | The user asks what an event was, and the readable endpoint does not expose a classification. |
| **User-facing question** | "Was it a person, a car, an animal, a package?" |
| **Expected WITNESS behavior** | Report `CLASSIFICATION_UNAVAILABLE`, name the fields actually present, and refuse to infer a classification from anything but a field the source returned with a value. |
| **Required evidence** | A successful history response with ≥1 event and no populated classification field. |
| **Evidence source** | Ring history endpoint; scripted frames. |
| **Evidence limitations** | Ring's motion webhook carries a classification, but the history endpoint does not, and the two are not interchangeable. WITNESS reads only the history endpoint. |
| **False-positive risks** | Inferring a classification from event type; asserting a field is absent when it is present but unreadable. |
| **Verification method** | Automated tests. |
| **Verification status** | **VERIFIED BY SIMULATION**. |
| **Supporting evidence** | `rule:classification_not_exposed` in `witness/engine/rules.py`. Tests: `test_missing_classification_produces_a_limitation_not_a_guess`, `test_classification_limitation_names_the_available_fields`, `test_classification_explanation_does_not_deny_a_field_it_lists`, `test_present_classification_suppresses_the_limitation`. |
| **Remaining work** | None identified; the limitation is the intended behaviour. |

---

### 7. Stale evidence

| Column | Content |
|---|---|
| **Incident class** | The most recent observation is older than the staleness threshold. |
| **User-facing question** | "Is this current?" |
| **Expected WITNESS behavior** | Flag the evidence stale and refuse to present it as current. Staleness is a modifier on every view, not a status. |
| **Required evidence** | The observation's `observed_at` and the current time. |
| **Evidence source** | Observation timestamps in `witness/store.py`. |
| **Evidence limitations** | Depends on the observation clock; clock skew would shift the flag. |
| **False-positive risks** | A fresh observation flagged stale because the process clock is ahead. |
| **Verification method** | Automated tests. |
| **Verification status** | **VERIFIED BY SIMULATION**. |
| **Supporting evidence** | `rule_staleness` in `witness/engine/rules.py`. Tests: `test_old_observation_is_marked_stale`, `test_recent_observation_is_not_stale`, `test_staleness_carries_the_age_and_the_threshold`. |
| **Remaining work** | None identified. |

---

### 8. Unknown device attributes

| Column | Content |
|---|---|
| **Incident class** | The source returns a device without a field WITNESS would like to display (e.g. device kind, online state). |
| **User-facing question** | "What kind of device is this, and is it online?" |
| **Expected WITNESS behavior** | Report the field as unknown. Never guess from the name or the image URL. |
| **Required evidence** | The device resource as returned. |
| **Evidence source** | Ring `GET /v1/devices` (live: the Playground device carried only `name` and `image_url`, so `device_kind` and `online` were `None`). |
| **Evidence limitations** | The Playground device carries no `device_type`/`kind` and no online flag. This is a property of the synthetic device, not necessarily of real devices. |
| **False-positive risks** | Inferring kind from the label or image; defaulting `online` to true. |
| **Verification method** | Live observation plus automated tests. |
| **Verification status** | **VERIFIED LIVE** for the observation that the field can be absent; **VERIFIED BY SIMULATION** for the handling. |
| **Supporting evidence** | `normalise_device` in `witness/adapters/base.py`; `DeviceRecord.device_kind`/`online` defaulting to `None` in `witness/engine/models.py`. Live: `docs/SUBMISSION.md` VERIFIED LIVE. |
| **Remaining work** | Confirm the field is present on real (non-Playground) devices. |

---

### 9. Tamper evidence — the ledger chain

| Column | Content |
|---|---|
| **Incident class** | Someone modifies, deletes, reorders, or inserts a record in the evidence ledger. |
| **User-facing question** | "Can I trust that this evidence record has not been altered?" |
| **Expected WITNESS behavior** | Detect modification, deletion, reordering, broken links, sequence errors, and malformed entries; report the first bad index. Describe the ledger as **tamper-evident**, never immutable. |
| **Required evidence** | A hash chain in which each record commits to the previous record's digest. |
| **Evidence source** | `witness/engine/ledger.py`; persisted in `witness/store.py`. |
| **Evidence limitations** | A privileged operator with file access can regenerate a fresh, internally consistent chain. Detecting that requires an independently anchored checkpoint, which WITNESS does not have. |
| **False-positive risks** | Presenting tamper-evidence as immutability; presenting a truncated chain as verified when only its head changed. |
| **Verification method** | Automated tests, including a deliberately tampered chain; `scripts/demo_tamper.py`. |
| **Verification status** | **VERIFIED BY SIMULATION**. |
| **Supporting evidence** | `Ledger.verify` in `witness/engine/ledger.py`. Tests: `test_modified_record_is_detected`, `test_modified_status_field_is_detected`, `test_modified_evidence_refs_are_detected`, `test_broken_prev_hash_link_is_detected`, `test_reordered_records_are_detected`, `test_deleted_middle_record_is_detected`, `test_duplicate_sequence_identifier_is_detected`, `test_malformed_digest_is_detected`, `test_full_rewrite_is_not_detectable_by_a_local_verifier`. |
| **Remaining work** | An externally anchored checkpoint would be needed to detect a full rewrite. Out of scope for the current build. |

**Explicit non-claim:** the ledger is tamper-evident, not immutable. A full
rewrite is not detectable locally. Pinned by
`test_full_rewrite_is_not_detectable_by_a_local_verifier`.

---

## Summary counts

Distinct incident classes: **9**.

| Status | Count | Classes |
|---|---|---|
| VERIFIED LIVE | 0 | — |
| PARTIALLY VERIFIED | 1 | Encryption transition (field presence live; transition unverified) |
| VERIFIED BY SIMULATION | 8 | Withdrawal, duplicate event, late arrival, outage, classification unavailable, stale evidence, tamper evidence, unknown attributes (handling) |
| DOCUMENTED | 0 | — |
| UNVERIFIED | 1 (sub-claim) | Encryption *transition* specifically |

No incident class is VERIFIED LIVE, and none can be: the Playground exposes one
device, emits no events, and cannot produce any of these conditions. The three
live endpoints verify device discovery, configuration retrieval and history
retrieval — inputs to these classes, not the classes themselves.

## Most significant evidence gaps

1. **Encryption transition** — the API exposes only the current configuration.
   No configuration history is retained, so a genuine before-and-after cannot be
   established. WITNESS correctly declines to claim one; the gap is that the
   capability a user might want is not implemented.
2. **Live withdrawal, duplicate, and late arrival** — none can be produced on
   the Playground. They rest entirely on the deterministic engine and scripted
   frames.
3. **Live outage behaviour** — rate limiting and server errors are classified and
   tested against a mock transport, but not observed from the live service.
4. **Real-device attributes** — whether real Ring devices populate `device_type`
   and `online` is unconfirmed; only the Playground device has been observed.

## What this matrix does not claim

A passing test suite does not establish that every incident class works against
live Ring data. The engine's interpretation is well covered; the live service's
ability to produce these conditions is not demonstrated, and the Playground
cannot demonstrate it.
