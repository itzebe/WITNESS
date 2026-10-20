"""Deterministic status taxonomy for WITNESS.

Every status in this module is produced by a named, testable rule in
``witness.engine.rules``. No status is ever produced by a language model.

The registry below is the single source of truth for what each status means,
what triggers it, what evidence it requires, and which conclusions it
explicitly does NOT license. ``STATUS_SPEC`` is asserted against the ``Status``
enum in the test suite, so a status cannot be added without documenting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """The complete set of statuses WITNESS can assign.

    Statuses are grouped by the axis they describe. A single observation can
    carry a presence status *and* a content status *and* one or more
    question-level statuses, because these are genuinely independent facts.
    """

    # --- Axis 1: inventory / presence -------------------------------------
    OBSERVED = "OBSERVED"
    NOT_OBSERVED = "NOT_OBSERVED"
    SOURCE_WITHDRAWN = "SOURCE_WITHDRAWN"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

    # --- Axis 2: content visibility ---------------------------------------
    CONTENT_VISIBLE = "CONTENT_VISIBLE"
    SOURCE_ENCRYPTED = "SOURCE_ENCRYPTED"
    CONTENT_UNAVAILABLE = "CONTENT_UNAVAILABLE"

    # --- Axis 3: events ----------------------------------------------------
    EVENT_OBSERVED = "EVENT_OBSERVED"
    EVENT_LATE = "EVENT_LATE"
    EVENT_DUPLICATE = "EVENT_DUPLICATE"

    # --- Axis 4: questions the source cannot answer -----------------------
    CLASSIFICATION_UNAVAILABLE = "CLASSIFICATION_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNKNOWN = "UNKNOWN"


#: Statuses that assert something about a device's presence in the inventory.
PRESENCE_STATUSES = frozenset(
    {
        Status.OBSERVED,
        Status.NOT_OBSERVED,
        Status.SOURCE_WITHDRAWN,
        Status.SOURCE_UNAVAILABLE,
    }
)

#: Statuses that assert something about whether content can be read.
CONTENT_STATUSES = frozenset(
    {
        Status.CONTENT_VISIBLE,
        Status.SOURCE_ENCRYPTED,
        Status.CONTENT_UNAVAILABLE,
    }
)

#: Statuses that assert something about a single event.
EVENT_STATUSES = frozenset(
    {
        Status.EVENT_OBSERVED,
        Status.EVENT_LATE,
        Status.EVENT_DUPLICATE,
    }
)

#: Statuses that express a limit on what WITNESS can conclude.
LIMITATION_STATUSES = frozenset(
    {
        Status.CLASSIFICATION_UNAVAILABLE,
        Status.INSUFFICIENT_EVIDENCE,
        Status.UNKNOWN,
    }
)


@dataclass(frozen=True)
class StatusSpec:
    """Documentation and semantics for one status.

    Attributes:
        status: The status this spec describes.
        label: Short human-facing label.
        definition: What the status asserts, precisely.
        trigger: The exact condition under which a rule assigns this status.
        required_evidence: Evidence the rule must have before assigning it.
        forbidden_conclusions: Conclusions this status does *not* license.
        explanation_template: User-facing explanation. ``{...}`` placeholders
            are filled from the rule's explanation context.
        tone: One of ``ok``, ``warn``, ``unknown``, ``error`` -- drives UI
            styling and must not be conflated with severity of the underlying
            real-world situation.
    """

    status: Status
    label: str
    definition: str
    trigger: str
    required_evidence: tuple[str, ...]
    forbidden_conclusions: tuple[str, ...]
    explanation_template: str
    tone: str
    extra: dict = field(default_factory=dict)


STATUS_SPEC: dict[Status, StatusSpec] = {
    Status.OBSERVED: StatusSpec(
        status=Status.OBSERVED,
        label="Observed",
        definition=(
            "The source returned a record for this device in a successful "
            "inventory response at the stated observation time."
        ),
        trigger=(
            "rule:inventory_present -- the inventory request completed with a "
            "successful status AND the device id appears in the returned set."
        ),
        required_evidence=("inventory_response",),
        forbidden_conclusions=(
            "That the device is online.",
            "That the device is functioning correctly.",
            "That any event occurred on the device.",
            "That the device is not encrypted.",
        ),
        explanation_template=(
            "{device_label} was returned by {source_name} in the inventory "
            "response captured at {observed_at}. This establishes that the "
            "source still exposes the device to this integration. It does not "
            "establish that the device is online, healthy, or recording."
        ),
        tone="ok",
    ),
    Status.NOT_OBSERVED: StatusSpec(
        status=Status.NOT_OBSERVED,
        label="Not observed",
        definition=(
            "A previously observed device was absent from a successful "
            "inventory response, but the absence has not yet been corroborated "
            "across enough independent successful observations to assert a "
            "withdrawal."
        ),
        trigger=(
            "rule:inventory_absent_below_threshold -- the inventory request "
            "succeeded AND the device id was absent AND the count of "
            "consecutive successful absences is below the withdrawal threshold."
        ),
        required_evidence=("inventory_response", "prior_observation"),
        forbidden_conclusions=(
            "That the device is offline.",
            "That the device was removed.",
            "That the device was tampered with.",
            "That nothing happened at the device.",
        ),
        explanation_template=(
            "{device_label} was not present in the inventory response captured "
            "at {observed_at}, but it was present as recently as {last_seen_at}. "
            "This is {absent_cycles} consecutive successful observation(s) "
            "without the device; {remaining} more are required before WITNESS "
            "will describe this as a withdrawal. A single absence can be "
            "ordinary API behaviour and is not evidence of removal."
        ),
        tone="unknown",
    ),
    Status.SOURCE_WITHDRAWN: StatusSpec(
        status=Status.SOURCE_WITHDRAWN,
        label="No longer exposed",
        definition=(
            "A previously observed device has been absent from enough "
            "consecutive successful inventory responses that WITNESS asserts "
            "the source has stopped exposing it to this integration."
        ),
        trigger=(
            "rule:inventory_absent_at_or_above_threshold -- the inventory "
            "request succeeded AND the device id was absent AND consecutive "
            "successful absences >= WITHDRAWAL_THRESHOLD."
        ),
        required_evidence=("inventory_response", "prior_observation"),
        forbidden_conclusions=(
            "That the device was physically removed.",
            "That the device was tampered with or compromised.",
            "That the user revoked access (this is one of several possible "
            "causes and WITNESS cannot distinguish them).",
            "That the device is broken.",
        ),
        explanation_template=(
            "{device_label} has been absent from {absent_cycles} consecutive "
            "successful inventory responses, most recently at {observed_at}. "
            "WITNESS concludes the source has stopped exposing this device. "
            "WITNESS cannot determine why: the documented causes include the "
            "owner unlinking the integration, the owner enabling end-to-end "
            "encryption on the device, or the device leaving the account. "
            "These are not distinguishable from the available data."
        ),
        tone="warn",
    ),
    Status.SOURCE_UNAVAILABLE: StatusSpec(
        status=Status.SOURCE_UNAVAILABLE,
        label="Source unavailable",
        definition=(
            "The inventory request itself did not complete successfully, so no "
            "conclusion can be drawn about any device's presence."
        ),
        trigger=(
            "rule:source_request_failed -- the adapter reported a failure "
            "(network error, timeout, auth failure, or non-success status)."
        ),
        required_evidence=("failure_record",),
        forbidden_conclusions=(
            "That any device is offline.",
            "That any device is missing.",
            "That any device was removed.",
            "That no events occurred.",
        ),
        explanation_template=(
            "WITNESS could not complete the inventory request to {source_name} "
            "at {observed_at}. The request failed with: {failure_kind}. "
            "No conclusion can be drawn about any device. A failed request is "
            "not evidence that a device is offline, missing, or that nothing "
            "happened -- it is evidence that WITNESS could not look."
        ),
        tone="error",
    ),
    Status.CONTENT_VISIBLE: StatusSpec(
        status=Status.CONTENT_VISIBLE,
        label="Content readable",
        definition=(
            "The source did not report encryption for this device, so recorded "
            "and live content is expected to be readable by this integration."
        ),
        trigger=(
            "rule:content_not_encrypted -- the configurations request succeeded "
            "and e2e_encryption.enabled is explicitly false."
        ),
        required_evidence=("configurations_response",),
        forbidden_conclusions=(
            "That content was actually retrieved.",
            "That any particular event was captured.",
        ),
        explanation_template=(
            "{device_label} reported e2e_encryption.enabled = false at "
            "{observed_at}. Content from this device is expected to be readable "
            "by this integration. WITNESS has not necessarily retrieved any "
            "content; this states only that encryption is not the obstacle."
        ),
        tone="ok",
    ),
    Status.SOURCE_ENCRYPTED: StatusSpec(
        status=Status.SOURCE_ENCRYPTED,
        label="Content encrypted",
        definition=(
            "The source explicitly reports that this device is end-to-end "
            "encrypted, so its content cannot be read by this integration."
        ),
        trigger=(
            "rule:content_encrypted -- the configurations request succeeded and "
            "e2e_encryption.enabled is true."
        ),
        required_evidence=("configurations_response",),
        forbidden_conclusions=(
            "That anything happened or did not happen at the device.",
            "That the device is offline.",
        ),
        explanation_template=(
            "{device_label} reported e2e_encryption.enabled = true at "
            "{observed_at}. The source has told us that content from this device "
            "is not readable by this integration. This is a confirmed "
            "limitation, not a fault: WITNESS can report the device's presence "
            "but can never report its content."
        ),
        tone="warn",
    ),
    Status.CONTENT_UNAVAILABLE: StatusSpec(
        status=Status.CONTENT_UNAVAILABLE,
        label="Content unavailable",
        definition=(
            "WITNESS could not determine the encryption state of the device, so "
            "content readability is unknown."
        ),
        trigger=(
            "rule:content_state_unknown -- the configurations request failed or "
            "did not report an e2e_encryption object."
        ),
        required_evidence=("failure_record", "configurations_response"),
        forbidden_conclusions=(
            "That the device is encrypted.",
            "That the device is unencrypted.",
            "That content is retrievable.",
        ),
        explanation_template=(
            "WITNESS could not determine whether content from {device_label} is "
            "readable. The configurations request at {observed_at} "
            "{failure_kind}. Content readability is therefore unknown, and "
            "WITNESS will not guess in either direction."
        ),
        tone="unknown",
    ),
    Status.EVENT_OBSERVED: StatusSpec(
        status=Status.EVENT_OBSERVED,
        label="Event observed",
        definition=(
            "The source returned this event in a successful history response "
            "within the requested scope and time window."
        ),
        trigger="rule:event_present -- the history request succeeded and the event appears once with no detected anomaly.",
        required_evidence=("history_response",),
        forbidden_conclusions=(
            "That this is a complete record of what happened.",
            "That the event was classified.",
        ),
        explanation_template=(
            "The source returned this event with source-reported time "
            "{reported_at}, retrieved at {observed_at}. This establishes the "
            "source reported the event. It does not establish that the source "
            "reported every event."
        ),
        tone="ok",
    ),
    Status.EVENT_LATE: StatusSpec(
        status=Status.EVENT_LATE,
        label="Event arrived late",
        definition=(
            "The event's source-reported time is earlier than the retrieval "
            "time by more than the configured lateness threshold."
        ),
        trigger=(
            "rule:event_late -- the history request succeeded AND "
            "(observed_at - reported_at) > EVENT_LATENESS_THRESHOLD."
        ),
        required_evidence=("history_response",),
        forbidden_conclusions=(
            "That the event is inaccurate.",
            "That an event was lost.",
        ),
        explanation_template=(
            "This event reports a source time of {reported_at} but was first "
            "retrieved at {observed_at}, a delay of {delay}. WITNESS notes the "
            "delay because it means a real-time view at {reported_at} would not "
            "have shown this event. The delay does not make the event "
            "inaccurate."
        ),
        tone="warn",
    ),
    Status.EVENT_DUPLICATE: StatusSpec(
        status=Status.EVENT_DUPLICATE,
        label="Duplicate event",
        definition=(
            "Two or more records in the retrieved data share the same "
            "deterministic event identity."
        ),
        trigger=(
            "rule:event_duplicate -- two records produce an identical identity "
            "key under the documented identity rule."
        ),
        required_evidence=("history_response",),
        forbidden_conclusions=(
            "That two real-world events did not occur.",
        ),
        explanation_template=(
            "WITNESS retrieved {duplicate_count} records that share the same "
            "identity key {identity_key}. The source documents that deliveries "
            "may be retried and that ordering is not guaranteed, so repeated "
            "records are expected. WITNESS counts this event once. Note that "
            "identical records do not prove a single real-world event."
        ),
        tone="warn",
    ),
    Status.CLASSIFICATION_UNAVAILABLE: StatusSpec(
        status=Status.CLASSIFICATION_UNAVAILABLE,
        label="Classification unavailable",
        definition=(
            "The question requires a classification the source does not expose "
            "on the endpoint WITNESS can read."
        ),
        trigger=(
            "rule:classification_not_exposed -- the history response succeeded "
            "and its records carry no classification field."
        ),
        required_evidence=("history_response",),
        forbidden_conclusions=(
            "That the event had no classification.",
            "That the event was not a person, vehicle, animal, or package.",
            "Any guess at what the event was.",
        ),
        explanation_template=(
            "WITNESS retrieved {event_count} event record(s) at {observed_at}. "
            "The records carry only these fields: {present_fields}. They do not "
            "carry a classification field, so WITNESS cannot say what the event "
            "was. {classification_note} WITNESS will not infer a classification "
            "from anything other than a field the source actually returned."
        ),
        tone="unknown",
    ),
    Status.INSUFFICIENT_EVIDENCE: StatusSpec(
        status=Status.INSUFFICIENT_EVIDENCE,
        label="Insufficient evidence",
        definition=(
            "The available evidence cannot establish the requested conclusion, "
            "and WITNESS declines to assert one."
        ),
        trigger=(
            "rule:insufficient_evidence -- no other rule matched and the "
            "requested conclusion is not supported by the retrieved data."
        ),
        required_evidence=(),
        forbidden_conclusions=(
            "Any conclusion about the real-world situation.",
        ),
        explanation_template=(
            "WITNESS cannot establish '{question}' from the available evidence. "
            "{reason} WITNESS is reporting this as unresolved rather than "
            "choosing the most plausible answer."
        ),
        tone="unknown",
    ),
    Status.UNKNOWN: StatusSpec(
        status=Status.UNKNOWN,
        label="Unknown",
        definition="WITNESS has no basis on which to assign a status.",
        trigger="rule:unknown -- the defensive fallback when no rule produced a conclusion.",
        required_evidence=(),
        forbidden_conclusions=("Any conclusion at all.",),
        explanation_template=(
            "WITNESS could not establish a presence status for {device_label} from the "
            "observations it holds. No rule produced a conclusion for this device."
        ),
        tone="unknown",
    ),
}


def spec_for(status: Status) -> StatusSpec:
    """Return the documented spec for a status.

    Raises:
        KeyError: if the status has no spec, which the test suite forbids.
    """
    return STATUS_SPEC[status]
