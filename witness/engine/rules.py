"""The deterministic rules engine.

Every function in this module is pure: given the same inputs it returns the
same Assessment. No network access, no clock reads except those passed in, no
language model, no randomness. This is what lets WITNESS claim its statuses are
reproducible.

Each rule is identified by a stable ``rule_id`` string that appears in the
Assessment and therefore in the evidence ledger. Rule ids are part of the
public contract; renaming one is a breaking change.

The thresholds below are the whole of WITNESS's policy. They are explicit,
small, and defensible rather than hidden inside a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from .models import (
    Assessment,
    DeviceRecord,
    EventRecord,
    Observation,
    iso,
    utcnow,
)
from .status import Status


@dataclass(frozen=True)
class Policy:
    """Explicit, inspectable thresholds.

    These values are deliberately conservative. Each one trades responsiveness
    for a lower chance of asserting something the evidence does not support.
    """

    #: How many consecutive *successful* inventory responses must omit a device
    #: before WITNESS describes that as a withdrawal rather than a single
    #: unexplained absence. A single absence is common and proves nothing.
    withdrawal_threshold: int = 2

    #: How late (retrieval time minus source-reported time) an event must be
    #: before WITNESS annotates it as late.
    event_lateness_threshold: timedelta = timedelta(minutes=5)

    #: How old an observation may be before WITNESS labels it stale and
    #: refuses to present it as current.
    staleness_threshold: timedelta = timedelta(minutes=30)

    #: Only the most recent N observations are consulted when counting
    #: consecutive absences.
    reconciliation_window: int = 20


DEFAULT_POLICY = Policy()


# --------------------------------------------------------------------------
# Presence rules (inventory axis)
# --------------------------------------------------------------------------


def rule_source_request_failed(obs: Observation, now: datetime | None = None) -> Assessment | None:
    """rule:source_request_failed

    If the inventory request did not succeed, WITNESS can say nothing about any
    device. This rule is checked first and short-circuits the others.
    """
    if obs.inventory.ok:
        return None
    failure = obs.inventory.failure
    assert failure is not None
    return Assessment(
        status=Status.SOURCE_UNAVAILABLE,
        subject_type="source",
        subject_id=obs.source_id,
        subject_label=obs.source_id,
        rule_id="rule:source_request_failed",
        explanation=(
            f"WITNESS could not complete the inventory request to {obs.source_id} at "
            f"{iso(obs.observed_at)}. The request failed with: {failure.describe()}. "
            "No conclusion can be drawn about any device. A failed request is not "
            "evidence that a device is offline, missing, or that nothing happened "
            "-- it is evidence that WITNESS could not look."
        ),
        observed_at=obs.observed_at,
        scope=f"inventory:{obs.source_id}",
        evidence_refs=(obs.observation_id,),
        detail={"failure": failure.to_dict()},
        axis="presence",
    )


def rule_inventory_present(obs: Observation, device: DeviceRecord) -> Assessment | None:
    """rule:inventory_present -- the device appeared in a successful response."""
    if not obs.inventory.ok:
        return None
    if not any(d.device_id == device.device_id for d in obs.devices):
        return None
    return Assessment(
        status=Status.OBSERVED,
        subject_type="device",
        subject_id=device.device_id,
        subject_label=device.device_label,
        rule_id="rule:inventory_present",
        explanation=(
            f"{device.device_label} was returned by {obs.source_id} in the inventory "
            f"response captured at {iso(obs.observed_at)}. This establishes that the "
            "source still exposes the device to this integration. It does not "
            "establish that the device is online, healthy, or recording."
        ),
        observed_at=obs.observed_at,
        scope=f"inventory:{obs.source_id}",
        evidence_refs=(obs.observation_id,),
        detail={"device_kind": device.device_kind, "source_reported_online": device.online},
        axis="presence",
    )


def consecutive_successful_absences(
    prior: Sequence[Observation],
    device_id: str,
    source_id: str,
    policy: Policy = DEFAULT_POLICY,
) -> tuple[int, Observation | None]:
    """Count consecutive successful inventories, newest first, that omit a device.

    Returns ``(count, last_observation_that_included_it)``.

    ``prior`` must be the full observation sequence *including* the current
    observation, newest last. The current observation's absence counts, because
    the caller has already established the device is missing from it.

    Only observations from the same source are considered, and only successful
    inventories are counted. A failed inventory breaks the streak rather than
    extending it, because a failure is not evidence of absence.
    """
    count = 0
    last_seen: Observation | None = None
    window_start = max(0, len(prior) - policy.reconciliation_window)

    # Count consecutive absences within the window. The window bounds the count,
    # so a long absence is reported as "at least the window size" rather than an
    # unbounded number.
    for obs in reversed(prior[window_start:]):
        if obs.source_id != source_id:
            continue
        if not obs.inventory.ok:
            # A failure neither extends nor resets the streak; skip it.
            continue
        if any(d.device_id == device_id for d in obs.devices):
            last_seen = obs
            break
        count += 1

    if last_seen is None:
        # The streak ran to the window edge without finding the device, but it
        # may still have been seen earlier in the history. Search the full
        # sequence before concluding anything. Without this, a device absent
        # for longer than the window is reported as never observed -- which is
        # false, and would downgrade a real withdrawal to UNKNOWN.
        for obs in reversed(prior[:window_start]):
            if obs.source_id != source_id:
                continue
            if not obs.inventory.ok:
                continue
            if any(d.device_id == device_id for d in obs.devices):
                last_seen = obs
                break

    return count, last_seen


def rule_inventory_absent(
    obs: Observation,
    device_id: str,
    device_label: str,
    prior: Sequence[Observation],
    policy: Policy = DEFAULT_POLICY,
) -> Assessment | None:
    """rule:inventory_absent_below_threshold / rule:inventory_absent_at_or_above_threshold

    A device that a previous successful observation returned is missing from a
    later successful observation. One absence is an anomaly; repeated absences
    are a withdrawal. The threshold is explicit and reported to the user.
    """
    if not obs.inventory.ok:
        return None
    if any(d.device_id == device_id for d in obs.devices):
        return None

    count, last_seen = consecutive_successful_absences(prior, device_id, obs.source_id, policy)
    if last_seen is None:
        # Never observed by a successful inventory: this is not a withdrawal,
        # it is a device we have no history for.
        return None

    evidence = (obs.observation_id, last_seen.observation_id)
    common_detail: dict[str, Any] = {
        "consecutive_absences": count,
        "threshold": policy.withdrawal_threshold,
        "last_seen_at": iso(last_seen.observed_at),
    }

    if count >= policy.withdrawal_threshold:
        return Assessment(
            status=Status.SOURCE_WITHDRAWN,
            subject_type="device",
            subject_id=device_id,
            subject_label=device_label,
            rule_id="rule:inventory_absent_at_or_above_threshold",
            explanation=(
                f"{device_label} has been absent from {count} consecutive successful "
                f"inventory responses, most recently at {iso(obs.observed_at)}. WITNESS "
                "concludes the source has stopped exposing this device. WITNESS cannot "
                "determine why: the documented causes include the owner unlinking the "
                "integration, the owner enabling end-to-end encryption on the device, "
                "or the device leaving the account. These are not distinguishable from "
                "the available data."
            ),
            observed_at=obs.observed_at,
            scope=f"inventory:{obs.source_id}",
            evidence_refs=evidence,
            detail=common_detail,
            axis="presence",
        )

    remaining = policy.withdrawal_threshold - count
    return Assessment(
        status=Status.NOT_OBSERVED,
        subject_type="device",
        subject_id=device_id,
        subject_label=device_label,
        rule_id="rule:inventory_absent_below_threshold",
        explanation=(
            f"{device_label} was not present in the inventory response captured at "
            f"{iso(obs.observed_at)}, but it was present as recently as "
            f"{iso(last_seen.observed_at)}. This is {count} consecutive successful "
            f"observation(s) without the device; {remaining} more are required before "
            "WITNESS will describe this as a withdrawal. A single absence can be "
            "ordinary API behaviour and is not evidence of removal."
        ),
        observed_at=obs.observed_at,
        scope=f"inventory:{obs.source_id}",
        evidence_refs=evidence,
        detail=common_detail,
        axis="presence",
    )


# --------------------------------------------------------------------------
# Content rules (encryption axis)
# --------------------------------------------------------------------------


def rule_content_state(obs: Observation, device: DeviceRecord) -> Assessment:
    """rule:content_encrypted / rule:content_not_encrypted / rule:content_state_unknown

    Reads only what the source stated. ``e2e_encrypted is None`` means the
    source did not state a value, which is a different finding from "false".
    """
    base = dict(
        subject_type="device",
        subject_id=device.device_id,
        subject_label=device.device_label,
        observed_at=obs.observed_at,
        scope=f"configurations:{obs.source_id}",
        evidence_refs=(obs.observation_id,),
        axis="content",
    )

    if obs.configurations is None or not obs.configurations.ok:
        kind = (
            obs.configurations.failure.describe()
            if obs.configurations is not None and obs.configurations.failure is not None
            else "was not attempted"
        )
        return Assessment(
            status=Status.CONTENT_UNAVAILABLE,
            rule_id="rule:content_state_unknown",
            explanation=(
                f"WITNESS could not determine whether content from {device.device_label} "
                f"is readable. The configurations request at {iso(obs.observed_at)} {kind}. "
                "Content readability is therefore unknown, and WITNESS will not guess in "
                "either direction."
            ),
            detail={"configurations_ok": False},
            **base,
        )

    if not device.encryption_stated:
        return Assessment(
            status=Status.CONTENT_UNAVAILABLE,
            rule_id="rule:content_state_unknown",
            explanation=(
                f"The configurations response for {device.device_label} at "
                f"{iso(obs.observed_at)} did not include an encryption field. WITNESS "
                "therefore cannot state whether content is readable. The absence of the "
                "field is not evidence that the device is unencrypted."
            ),
            detail={"encryption_field_present": False},
            **base,
        )

    if device.e2e_encrypted is True:
        return Assessment(
            status=Status.SOURCE_ENCRYPTED,
            rule_id="rule:content_encrypted",
            explanation=(
                f"{device.device_label} reported e2e_encryption.enabled = true at "
                f"{iso(obs.observed_at)}. The source has told us that content from this "
                "device is not readable by this integration. This is a confirmed "
                "limitation, not a fault: WITNESS can report the device's presence but "
                "can never report its content."
            ),
            detail={"e2e_encrypted": True},
            **base,
        )

    return Assessment(
        status=Status.CONTENT_VISIBLE,
        rule_id="rule:content_not_encrypted",
        explanation=(
            f"{device.device_label} reported e2e_encryption.enabled = false at "
            f"{iso(obs.observed_at)}. Content from this device is expected to be readable "
            "by this integration. WITNESS has not necessarily retrieved any content; this "
            "states only that encryption is not the obstacle."
        ),
        detail={"e2e_encrypted": False},
        **base,
    )


# --------------------------------------------------------------------------
# Event rules
# --------------------------------------------------------------------------


def deduplicate_events(events: Sequence[EventRecord]) -> tuple[list[EventRecord], dict[str, int]]:
    """Collapse duplicate events by identity key.

    Returns the unique events in first-seen order plus a map of identity key to
    the number of records that shared it.
    """
    seen: dict[str, EventRecord] = {}
    counts: dict[str, int] = {}
    for event in events:
        key = event.identity_key()
        counts[key] = counts.get(key, 0) + 1
        seen.setdefault(key, event)
    return list(seen.values()), counts


def rule_event_assessments(
    obs: Observation,
    policy: Policy = DEFAULT_POLICY,
) -> list[Assessment]:
    """rule:event_present / rule:event_late / rule:event_duplicate

    Produces one assessment per unique event, plus duplicate annotations.
    """
    if obs.history is None or not obs.history.ok:
        return []

    unique, counts = deduplicate_events(obs.events)
    out: list[Assessment] = []
    for event in unique:
        key = event.identity_key()
        dup = counts.get(key, 1)
        delay = None
        if event.reported_at is not None:
            delay = event.retrieved_at - event.reported_at

        is_late = delay is not None and delay > policy.event_lateness_threshold
        base = dict(
            subject_type="event",
            subject_id=event.event_id or key,
            subject_label=f"{event.event_type} at {iso(event.reported_at) or 'unknown time'}",
            observed_at=obs.observed_at,
            scope=f"history:{obs.source_id}",
            evidence_refs=(obs.observation_id,),
            axis="events",
            detail={
                "event_type": event.event_type,
                "reported_at": iso(event.reported_at),
                "retrieved_at": iso(event.retrieved_at),
                "duplicate_count": dup,
                "identity_key": key,
                "classification": event.classification,
            },
        )

        if dup > 1:
            out.append(
                Assessment(
                    status=Status.EVENT_DUPLICATE,
                    rule_id="rule:event_duplicate",
                    explanation=(
                        f"WITNESS retrieved {dup} records that share the same identity key "
                        f"{key}. The source documents that deliveries may be retried and "
                        "that ordering is not guaranteed, so repeated records are expected. "
                        "WITNESS counts this event once. Note that identical records do not "
                        "prove a single real-world event."
                    ),
                    **base,
                )
            )
            continue

        if is_late:
            assert delay is not None
            seconds = int(delay.total_seconds())
            out.append(
                Assessment(
                    status=Status.EVENT_LATE,
                    rule_id="rule:event_late",
                    explanation=(
                        f"This event reports a source time of {iso(event.reported_at)} but "
                        f"was first retrieved at {iso(event.retrieved_at)}, a delay of "
                        f"{seconds // 60}m {seconds % 60}s. WITNESS notes the delay because "
                        f"it means a real-time view at {iso(event.reported_at)} would not "
                        "have shown this event. The delay does not make the event inaccurate."
                    ),
                    **base,
                )
            )
            continue

        out.append(
            Assessment(
                status=Status.EVENT_OBSERVED,
                rule_id="rule:event_present",
                explanation=(
                    f"The source returned this event with source-reported time "
                    f"{iso(event.reported_at)}, retrieved at {iso(event.retrieved_at)}. This "
                    "establishes the source reported the event. It does not establish that "
                    "the source reported every event."
                ),
                **base,
            )
        )
    return out


def rule_classification_availability(
    obs: Observation,
    question: str,
) -> Assessment | None:
    """rule:classification_not_exposed

    Fires when the user asks something that needs a classification and the
    retrieved records carry no classification field. WITNESS reports the
    limitation instead of guessing.
    """
    if obs.history is None or not obs.history.ok:
        return None
    if not obs.events:
        return None
    if any(e.classification for e in obs.events):
        return None

    present = sorted({name for e in obs.events for name in e.raw_field_names})
    note = (
        "Ring's motion webhook carries a classification inside its attributes, but "
        "the event history endpoint does not, and the two are not interchangeable."
    )
    return Assessment(
        status=Status.CLASSIFICATION_UNAVAILABLE,
        subject_type="source",
        subject_id=obs.source_id,
        subject_label=obs.source_id,
        rule_id="rule:classification_not_exposed",
        explanation=(
            f"WITNESS retrieved {len(obs.events)} event record(s) at {iso(obs.observed_at)}. "
            f"The records expose these fields: {', '.join(present) or 'none'}. None of them "
            f"carries a populated classification value, so WITNESS cannot say what the event "
            f"was. {note} WITNESS will not infer a classification from anything other than a "
            "field the source actually returned with a value."
        ),
        observed_at=obs.observed_at,
        scope=f"question:{question}",
        evidence_refs=(obs.observation_id,),
        detail={"present_fields": present, "question": question},
        axis="limitation",
    )


def rule_insufficient_evidence(
    obs: Observation,
    question: str,
    reason: str,
) -> Assessment:
    """rule:insufficient_evidence -- the explicit refusal to conclude."""
    return Assessment(
        status=Status.INSUFFICIENT_EVIDENCE,
        subject_type="source",
        subject_id=obs.source_id,
        subject_label=obs.source_id,
        rule_id="rule:insufficient_evidence",
        explanation=(
            f"WITNESS cannot establish '{question}' from the available evidence. "
            f"{reason} WITNESS is reporting this as unresolved rather than choosing the "
            "most plausible answer."
        ),
        observed_at=obs.observed_at,
        scope=f"question:{question}",
        evidence_refs=(obs.observation_id,),
        detail={"question": question},
        axis="limitation",
    )


def rule_unknown(device_id: str, device_label: str) -> Assessment:
    """rule:unknown -- the defensive fallback: no rule produced a conclusion.

    This is reached only when every presence rule declines to assess a device
    that WITNESS nonetheless has a record of. The wording must not claim the
    device was never observed: a device reaches this function only because it
    appears in a successful inventory, so asserting "never observed" here would
    be false. The honest statement is that no rule reached a conclusion.
    """
    return Assessment(
        status=Status.UNKNOWN,
        subject_type="device",
        subject_id=device_id,
        subject_label=device_label,
        rule_id="rule:unknown",
        explanation=(
            f"WITNESS could not establish a presence status for {device_label} from the "
            "observations it holds. No rule produced a conclusion for this device, so "
            "WITNESS reports the question as unresolved rather than choosing the most "
            "plausible answer."
        ),
        observed_at=utcnow(),
        scope="inventory:none",
        evidence_refs=(),
        detail={},
        axis="presence",
    )


def rule_staleness(assessment: Assessment, now: datetime, policy: Policy = DEFAULT_POLICY) -> dict[str, Any]:
    """Annotate an assessment with staleness. Not a status; a modifier.

    Old data must never be presented as current, so this is surfaced alongside
    every device view rather than folded into the status.
    """
    age = now - assessment.observed_at
    stale = age > policy.staleness_threshold
    return {
        "age_seconds": int(age.total_seconds()),
        "stale": stale,
        "staleness_threshold_seconds": int(policy.staleness_threshold.total_seconds()),
    }
