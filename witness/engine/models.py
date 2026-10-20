"""Core data model for WITNESS.

The model is deliberately split into four layers so that the product can always
answer "what did the source actually say?" separately from "what does WITNESS
conclude?":

1. ``FetchResult``  -- the outcome of one request. Either a success carrying
   normalised data, or a typed failure. These are *not* interchangeable and the
   code must never coerce one into the other.
2. ``DeviceRecord`` / ``EventRecord`` -- normalised source data.
3. ``Observation``  -- an immutable, timestamped snapshot of one fetch.
4. ``Assessment``   -- a status plus the rule and evidence that produced it.

Nothing in this module is inferred. Every field is either returned by the
source, derived by a named deterministic rule, or explicitly marked unknown.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from .status import Status


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Serialise a datetime as ISO-8601 UTC, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    """Best-effort parse of a timestamp into an aware datetime.

    Accepts an ISO-8601 string or an already-constructed ``datetime``. Returns
    None rather than raising: a source returning an unparseable timestamp is a
    data-quality finding, not a crash.

    Note that returning None is a *real* finding -- it means WITNESS does not
    know the time -- and downstream rules treat an unknown time as unknown
    rather than substituting the retrieval time.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------
# Failure typing
# --------------------------------------------------------------------------


class FailureKind(str, Enum):
    """Why a fetch did not succeed.

    These are kept distinct because they license different conclusions. In
    particular AUTH_FAILED and NOT_FOUND are *responses* from the source, while
    NETWORK_ERROR and TIMEOUT are absences of any response at all.
    """

    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    AUTH_FAILED = "AUTH_FAILED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    NOT_CONFIGURED = "NOT_CONFIGURED"


#: Failure kinds where the source answered and refused or denied.
RESPONSE_FAILURES = frozenset(
    {FailureKind.AUTH_FAILED, FailureKind.FORBIDDEN, FailureKind.NOT_FOUND, FailureKind.RATE_LIMITED}
)

#: Failure kinds where no usable response arrived.
TRANSPORT_FAILURES = frozenset(
    {FailureKind.NETWORK_ERROR, FailureKind.TIMEOUT, FailureKind.SERVER_ERROR}
)


def classify_http_status(code: int) -> FailureKind:
    """Map an HTTP status code to a failure kind.

    Only called for non-2xx responses. 3xx is treated as a transport problem
    because WITNESS never follows a redirect to an unconfigured host.
    """
    if code in (401,):
        return FailureKind.AUTH_FAILED
    if code == 403:
        return FailureKind.FORBIDDEN
    if code == 404:
        return FailureKind.NOT_FOUND
    if code == 429:
        return FailureKind.RATE_LIMITED
    if 500 <= code <= 599:
        return FailureKind.SERVER_ERROR
    if 300 <= code <= 399:
        return FailureKind.NETWORK_ERROR
    return FailureKind.SERVER_ERROR


@dataclass(frozen=True)
class Failure:
    """A typed, user-explainable failure.

    ``detail`` must never contain a credential. Adapters are responsible for
    redacting before constructing a Failure.
    """

    kind: FailureKind
    message: str
    http_status: int | None = None
    endpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "http_status": self.http_status,
            "endpoint": self.endpoint,
        }

    def describe(self) -> str:
        """Short phrase suitable for embedding in a user-facing sentence."""
        base = {
            FailureKind.NETWORK_ERROR: "the network request did not complete",
            FailureKind.TIMEOUT: "the request timed out",
            FailureKind.AUTH_FAILED: "the source rejected the credentials",
            FailureKind.FORBIDDEN: "the credentials were accepted but access was denied",
            FailureKind.NOT_FOUND: "the endpoint was not found",
            FailureKind.RATE_LIMITED: "the source rate-limited the request",
            FailureKind.SERVER_ERROR: "the source returned a server error",
            FailureKind.MALFORMED_RESPONSE: "the response could not be parsed",
            FailureKind.NOT_CONFIGURED: "no credentials are configured for this source",
        }[self.kind]
        if self.http_status is not None:
            return f"{base} (HTTP {self.http_status})"
        return base


@dataclass(frozen=True)
class FetchResult:
    """The outcome of one request: exactly one of success or failure.

    Use the ``ok``/``failed`` constructors. There is intentionally no way to
    build a result that is both, and no way to read ``data`` from a failure.
    """

    ok: bool
    endpoint: str
    observed_at: datetime
    data: Any = None
    failure: Failure | None = None
    request_id: str | None = None
    http_status: int | None = None

    @classmethod
    def success(
        cls,
        endpoint: str,
        data: Any,
        observed_at: datetime | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> "FetchResult":
        return cls(
            ok=True,
            endpoint=endpoint,
            observed_at=observed_at or utcnow(),
            data=data,
            request_id=request_id,
            http_status=http_status,
        )

    @classmethod
    def failed(
        cls,
        endpoint: str,
        failure: Failure,
        observed_at: datetime | None = None,
    ) -> "FetchResult":
        return cls(
            ok=False,
            endpoint=endpoint,
            observed_at=observed_at or utcnow(),
            failure=failure,
            http_status=failure.http_status,
        )

    def __post_init__(self) -> None:
        if self.ok and self.failure is not None:
            raise ValueError("a successful FetchResult cannot carry a failure")
        if not self.ok and self.failure is None:
            raise ValueError("a failed FetchResult must carry a failure")


# --------------------------------------------------------------------------
# Normalised source data
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceRecord:
    """A device as the source described it.

    ``device_id`` is stored in full because it is required to re-query the
    source. ``device_label`` is a display name the source provided. WITNESS
    stores no location, address, or owner identity.
    """

    device_id: str
    device_label: str
    device_kind: str | None = None
    online: bool | None = None
    reported_at: datetime | None = None
    #: True/False when the source stated the encryption state; None when it
    #: did not state it. ``None`` and ``False`` are different findings.
    e2e_encrypted: bool | None = None
    encryption_stated: bool = False
    raw_field_names: tuple[str, ...] = ()

    def identity(self) -> str:
        return self.device_id


@dataclass(frozen=True)
class EventRecord:
    """An event as the source described it.

    ``classification`` is populated only when the source returned a
    classification field. It is never inferred.
    """

    event_id: str
    event_type: str
    reported_at: datetime | None
    retrieved_at: datetime
    classification: str | None = None
    raw_field_names: tuple[str, ...] = ()

    def identity_key(self) -> str:
        """Deterministic identity used for deduplication.

        Documented rule: the source documents that deliveries may be retried
        and that ordering is not guaranteed. WITNESS therefore keys an event on
        (event id) when the source provides one, and otherwise on the tuple
        (event type, source-reported time). This rule is deliberately
        conservative: it will merge records that are genuinely identical, and
        it will not merge records that differ in type or time.
        """
        if self.event_id:
            return f"id:{self.event_id}"
        return f"tt:{self.event_type}|{iso(self.reported_at)}"


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One immutable, timestamped snapshot produced by one adapter run.

    An observation is the atomic unit of evidence. It records what was asked,
    what came back, and when. Reconciliations read observations; they never
    mutate them.
    """

    observation_id: str
    source_id: str
    source_kind: str  # "live" | "simulated"
    observed_at: datetime
    inventory: FetchResult
    history: FetchResult | None = None
    configurations: FetchResult | None = None
    devices: tuple[DeviceRecord, ...] = ()
    events: tuple[EventRecord, ...] = ()
    #: The device whose history and configurations this observation actually
    #: requested. One observation inspects one device, so a content claim about
    #: any other device must not describe a response that was never fetched.
    inspected_device_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "observed_at": iso(self.observed_at),
            "inventory_ok": self.inventory.ok,
            "inventory_endpoint": self.inventory.endpoint,
            "inventory_failure": self.inventory.failure.to_dict() if self.inventory.failure else None,
            "history_ok": self.history.ok if self.history else None,
            "history_failure": self.history.failure.to_dict() if self.history and self.history.failure else None,
            "configurations_ok": self.configurations.ok if self.configurations else None,
            "inspected_device_id": self.inspected_device_id,
            "device_count": len(self.devices),
            "event_count": len(self.events),
        }


# --------------------------------------------------------------------------
# Assessments
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Assessment:
    """A status plus the exact reasoning and evidence that produced it.

    This is the only object the UI renders as a conclusion. It always carries
    the rule that fired and the evidence ids that rule consumed, so any claim
    on screen can be traced back to a stored observation.
    """

    status: Status
    subject_type: str  # "device" | "event" | "source"
    subject_id: str
    subject_label: str
    rule_id: str
    explanation: str
    observed_at: datetime
    scope: str
    evidence_refs: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)
    axis: str = "presence"

    @property
    def tone(self) -> str:
        from .status import spec_for

        return spec_for(self.status).tone

    def to_dict(self) -> dict[str, Any]:
        from .status import spec_for

        spec = spec_for(self.status)
        return {
            "status": self.status.value,
            "tone": spec.tone,
            "label": spec.label,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "subject_label": self.subject_label,
            "rule_id": self.rule_id,
            "explanation": self.explanation,
            "observed_at": iso(self.observed_at),
            "scope": self.scope,
            "evidence_refs": list(self.evidence_refs),
            "detail": dict(self.detail),
            "axis": self.axis,
            "does_not_establish": list(spec.forbidden_conclusions),
        }


def deterministic_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 over a canonical JSON encoding.

    Canonical means: keys sorted, separators without spaces, ASCII-safe,
    datetimes already serialised by the caller. The same payload always
    produces the same digest, which is what makes the ledger verifiable.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
