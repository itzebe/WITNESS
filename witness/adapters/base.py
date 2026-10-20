"""Adapter interface shared by the live Ring source and the simulated source.

The interface is small on purpose. It returns ``FetchResult`` objects, which
cannot represent "a failure that looks like an empty success" -- the type
system forbids the single most common way a system like this lies to its user.

An adapter never raises for an expected failure. It returns ``FetchResult.failed``.
Unexpected exceptions are converted by ``collect_observation`` so that one bad
adapter cannot take down a reconciliation run.
"""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Sequence
from uuid import uuid4

from ..engine.models import (
    DeviceRecord,
    EventRecord,
    Failure,
    FailureKind,
    FetchResult,
    Observation,
    parse_iso,
    utcnow,
)

#: How much history to request. Ring's history endpoint is polled, so a bounded
#: window keeps the request cheap and the evidence scoped.
DEFAULT_HISTORY_LIMIT = 25


class SourceAdapter(abc.ABC):
    """A read-only source WITNESS can observe."""

    #: Stable identifier, e.g. "ring" or "ring-simulated".
    source_id: str
    #: "live" for a real integration, "simulated" for generated data.
    source_kind: str
    #: Human-facing name.
    display_name: str

    @abc.abstractmethod
    async def fetch_inventory(self) -> FetchResult:
        """Return the device inventory, or a typed failure."""

    @abc.abstractmethod
    async def fetch_history(self, device_id: str, limit: int = DEFAULT_HISTORY_LIMIT) -> FetchResult:
        """Return recent events for one device, or a typed failure."""

    @abc.abstractmethod
    async def fetch_configurations(self, device_id: str) -> FetchResult:
        """Return per-device configurations, or a typed failure."""

    def capabilities(self) -> dict[str, str]:
        """Declare which capabilities are VERIFIED, DOCUMENTED, UNAVAILABLE, SIMULATED."""
        raise NotImplementedError


async def collect_observation(
    adapter: SourceAdapter,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> Observation:
    """Run one full read-only pass against an adapter and package it.

    Failures are contained: a failed sub-request becomes a ``FetchResult.failed``
    inside the observation rather than an exception. The observation always
    records whether each request succeeded, so downstream rules can tell a
    successful empty response from a failure.
    """
    observation_id = f"obs-{uuid4().hex[:12]}"
    observed_at = utcnow()

    inventory = await _guard(adapter.fetch_inventory(), f"{adapter.source_id}:/v1/devices")

    devices: tuple[DeviceRecord, ...] = ()
    if inventory.ok:
        devices, problem = normalise_device_list(inventory.data)
        if problem is not None:
            # A payload WITNESS cannot fully read is a malformed response, not a
            # short device list. Treating it as the latter would make unreadable
            # devices look absent, and a sustained absence becomes an asserted
            # withdrawal. Fail the request instead of inventing a conclusion.
            inventory = FetchResult.failed(
                inventory.endpoint,
                Failure(
                    kind=FailureKind.MALFORMED_RESPONSE,
                    message=problem,
                    http_status=inventory.http_status,
                ),
                observed_at=inventory.observed_at,
            )
            devices = ()

    history: FetchResult | None = None
    configurations: FetchResult | None = None
    events: list[EventRecord] = []

    if devices:
        # WITNESS inspects the first device's history and configurations. This
        # keeps the observation cheap and honest: the UI states the scope
        # explicitly rather than implying every device was inspected.
        primary = devices[0]
        history = await _guard(
            adapter.fetch_history(primary.device_id, history_limit),
            f"{adapter.source_id}:/v1/history/devices/{primary.device_id}/events",
        )
        configurations = await _guard(
            adapter.fetch_configurations(primary.device_id),
            f"{adapter.source_id}:/v1/devices/{primary.device_id}/configurations",
        )
        if history.ok:
            events, unreadable = normalise_event_list(history.data, observed_at)
            if unreadable:
                # Mirror the inventory guard: a history payload WITNESS cannot
                # fully read must not be reinterpreted as a shorter event list.
                # An unreadable entry would otherwise become a phantom event
                # (id "", type "unknown"), and several of them collapse into a
                # single fabricated EVENT_DUPLICATE finding.
                history = FetchResult.failed(
                    history.endpoint,
                    Failure(
                        kind=FailureKind.MALFORMED_RESPONSE,
                        message=unreadable,
                        http_status=history.http_status,
                    ),
                    observed_at=history.observed_at,
                )
                events = []

    # Re-derive encryption state onto the primary device record from the
    # configurations response, since that is where the source reports it.
    if configurations is not None and configurations.ok and devices:
        primary = devices[0]
        config_data = configurations.data or {}
        stated, encrypted = _read_encryption(config_data)
        devices = (
            DeviceRecord(
                device_id=primary.device_id,
                device_label=primary.device_label,
                device_kind=primary.device_kind,
                online=primary.online,
                reported_at=primary.reported_at,
                e2e_encrypted=encrypted,
                encryption_stated=stated,
                raw_field_names=primary.raw_field_names,
            ),
        ) + devices[1:]

    return Observation(
        observation_id=observation_id,
        source_id=adapter.source_id,
        source_kind=adapter.source_kind,
        observed_at=observed_at,
        inventory=inventory,
        history=history,
        configurations=configurations,
        devices=devices,
        events=tuple(events),
    )


async def _guard(coro, endpoint: str) -> FetchResult:
    """Await an adapter call, converting any escape into a typed failure."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - deliberately contained
        return FetchResult.failed(
            endpoint,
            Failure(
                kind=FailureKind.SERVER_ERROR,
                message=f"adapter raised {type(exc).__name__}: {exc}",
                endpoint=endpoint,
            ),
        )


def _read_encryption(config_data) -> tuple[bool, bool | None]:
    """Extract (stated, encrypted) from a configurations payload.

    Ring reports this as ``e2e_encryption: {enabled: bool}``. WITNESS looks for
    that shape but does not invent a value when the field is absent.
    """
    if not isinstance(config_data, dict):
        return False, None
    node = config_data.get("e2e_encryption")
    if not isinstance(node, dict):
        return False, None
    if "enabled" not in node:
        return False, None
    return True, bool(node.get("enabled"))


def _normalise_device(raw) -> DeviceRecord | None:
    """Normalise one device as returned by a source.

    Accepts either WITNESS's own normalised dict (used by the simulated source)
    or a Ring JSON:API resource object. Unknown fields are ignored rather than
    guessed at.

    Returns ``None`` when the entry cannot be interpreted as a device. It must
    never invent one: a payload entry with no usable identity would otherwise
    become a phantom device that WITNESS reports as OBSERVED, and two such
    entries would collapse into a single fabricated device.
    """
    if not isinstance(raw, dict):
        return None

    if "device_id" in raw:  # already normalised
        device_id = str(raw["device_id"] or "").strip()
        if not device_id:
            return None
        return DeviceRecord(
            device_id=device_id,
            device_label=str(raw.get("device_label") or device_id),
            device_kind=raw.get("device_kind"),
            online=raw.get("online"),
            reported_at=parse_iso(raw.get("reported_at")),
            e2e_encrypted=raw.get("e2e_encrypted"),
            encryption_stated=bool(raw.get("encryption_stated", False)),
            raw_field_names=tuple(sorted(raw.keys())),
        )

    attributes = raw.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    raw_id = raw.get("id") or attributes.get("id")
    if raw_id is None or not str(raw_id).strip():
        # A device with no identity cannot be tracked between observations.
        # Giving it a placeholder id would make distinct devices collide and
        # make a later absence look like a withdrawal.
        return None
    device_id = str(raw_id).strip()
    label = str(attributes.get("name") or attributes.get("description") or device_id)
    kind = attributes.get("device_type") or attributes.get("kind")
    online = attributes.get("online") if isinstance(attributes.get("online"), bool) else None
    return DeviceRecord(
        device_id=device_id,
        device_label=label,
        device_kind=str(kind) if kind else None,
        online=online,
        reported_at=parse_iso(attributes.get("reported_at")),
        e2e_encrypted=None,
        encryption_stated=False,
        raw_field_names=tuple(sorted(attributes.keys())),
    )


def normalise_device_list(data: Any) -> tuple[tuple[DeviceRecord, ...], str | None]:
    """Normalise an inventory payload, refusing to invent devices.

    Returns ``(devices, problem)``. ``problem`` is a human-readable description
    when part of the payload could not be interpreted as a device.

    A partially-understood inventory is treated as a problem rather than as a
    short device list. If WITNESS silently dropped the entries it could not
    read, the missing devices would look absent, and absent devices eventually
    become an asserted withdrawal. Refusing to interpret the payload is the
    honest outcome: the response is malformed, not empty.
    """
    if data is None:
        return (), None
    if not isinstance(data, list):
        return (), f"expected a list of device resources, got {type(data).__name__}"

    devices: list[DeviceRecord] = []
    unreadable = 0
    for entry in data:
        record = _normalise_device(entry)
        if record is None:
            unreadable += 1
        else:
            devices.append(record)

    if unreadable:
        return tuple(devices), (
            f"{unreadable} of {len(data)} device resource(s) carried no usable identity "
            "and could not be interpreted"
        )
    return tuple(devices), None


def _normalise_event(raw, retrieved_at: datetime) -> EventRecord | None:
    """Normalise one event as returned by a source.

    ``classification`` is populated *only* from a field the source actually
    returned. Ring's event history does not carry one, so it stays None and the
    CLASSIFICATION_UNAVAILABLE rule fires downstream. That is the intended
    behaviour, not a gap.

    Returns ``None`` when the entry cannot be interpreted as an event. It must
    never invent one: an unreadable entry would otherwise become an event with
    an empty id and the type "unknown", which WITNESS would then report as a
    real finding. Several such entries collapse into one fabricated
    EVENT_DUPLICATE, and a phantom event is exactly the kind of unsupported
    claim this product refuses to make.
    """
    if not isinstance(raw, dict):
        return None

    if "event_type" in raw and "attributes" not in raw:  # already normalised
        if not str(raw.get("event_id") or "").strip():
            return None
        return EventRecord(
            event_id=str(raw.get("event_id") or ""),
            event_type=str(raw.get("event_type") or "unknown"),
            reported_at=parse_iso(raw.get("reported_at")),
            retrieved_at=retrieved_at,
            classification=raw.get("classification"),
            raw_field_names=tuple(sorted(raw.keys())),
        )

    attributes = raw.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    event_id = str(raw.get("id") or attributes.get("id") or "").strip()
    if not event_id:
        # No identity: WITNESS cannot tell this event apart from any other, so
        # it cannot count it, deduplicate it, or report it as observed.
        return None
    event_type = str(attributes.get("event_type") or "unknown")
    reported = parse_iso(attributes.get("start") or attributes.get("created_at"))
    classification = attributes.get("sub_type") or attributes.get("classification")
    return EventRecord(
        event_id=event_id,
        event_type=event_type,
        reported_at=reported,
        retrieved_at=retrieved_at,
        classification=str(classification) if classification else None,
        raw_field_names=tuple(sorted(attributes.keys())),
    )


def normalise_event_list(
    data: Any,
    retrieved_at: datetime,
) -> tuple[list[EventRecord], str | None]:
    """Normalise an event history payload, refusing to invent events.

    Returns ``(events, problem)``. ``problem`` is a human-readable description
    when part of the payload could not be interpreted as an event.

    A partially-understood history is treated as a problem rather than as a
    short event list: silently dropping entries would understate what the source
    returned, and fabricating placeholders for them would overstate it. Both are
    dishonest, so WITNESS refuses to interpret the payload at all.
    """
    if data is None:
        return [], None
    if not isinstance(data, list):
        return [], f"expected a list of event resources, got {type(data).__name__}"

    events: list[EventRecord] = []
    unreadable = 0
    for entry in data:
        record = _normalise_event(entry, retrieved_at)
        if record is None:
            unreadable += 1
        else:
            events.append(record)

    if unreadable:
        return [], (
            f"{unreadable} of {len(data)} event resource(s) carried no usable identity "
            "and could not be interpreted"
        )
    return events, None
