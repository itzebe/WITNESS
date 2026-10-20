"""Adapter tests.

Two things are tested here:

1. **The live Ring adapter**, against ``httpx.MockTransport``. This pins the
   exact HTTP behaviour -- URL, headers, query parameters, JSON:API unwrapping,
   error classification, and credential redaction -- without making a network
   call and without needing a real token.

2. **The simulated adapter**, confirming it produces the scripted sequence and
   is always labelled as simulated.

No test in this file claims a live authenticated Ring call was performed. The
live path is exercised structurally; the live integration itself is reported as
DOCUMENTED, not VERIFIED.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from witness.adapters.base import (
    _normalise_device,
    _normalise_event,
    _read_encryption,
    collect_observation,
    normalise_device_list,
    normalise_event_list,
)
from witness.adapters.ring import HISTORY_EVENT_TYPES_PARAM, RingAdapter, _extract_data_list
from witness.adapters.simulated import DEFAULT_SCENARIO, SimulatedAdapter
from witness.engine.models import FailureKind, utcnow
from witness.engine.reconcile import reconcile
from witness.engine.status import Status


def _mock(token: str = "test-token", handler=None) -> RingAdapter:
    """Build a RingAdapter backed by a MockTransport."""
    return RingAdapter(access_token=token, transport=httpx.MockTransport(handler))


DEVICES_BODY = {
    "data": [
        {"id": "dev-1", "type": "device", "attributes": {"name": "Front Door", "device_type": "doorbell", "online": True}},
        {"id": "dev-2", "type": "device", "attributes": {"name": "Driveway", "device_type": "camera", "online": False}},
    ]
}

HISTORY_BODY = {
    "data": [
        {"id": "ev-1", "type": "event", "attributes": {"event_type": "motion", "start": "2026-10-20T11:00:00Z", "end": "2026-10-20T11:00:30Z", "is_third_party_reviewed": False}},
    ]
}

CONFIG_ENCRYPTED = {"data": {"id": "dev-1", "attributes": {"e2e_encryption": {"enabled": True}}}}
CONFIG_PLAIN = {"data": {"id": "dev-1", "attributes": {"e2e_encryption": {"enabled": False}}}}


def _router(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/devices":
        return httpx.Response(200, json=DEVICES_BODY, headers={"x-request-id": "req-abc"})
    if path.startswith("/v1/history/devices/"):
        return httpx.Response(200, json=HISTORY_BODY, headers={"x-request-id": "req-hist"})
    if path.endswith("/configurations"):
        return httpx.Response(200, json=CONFIG_PLAIN, headers={"x-request-id": "req-cfg"})
    if path == "/v1/users/me":
        return httpx.Response(200, json={"data": {"id": "acct-1", "attributes": {"name": "Test"}}})
    return httpx.Response(404, json={"errors": []})


# --------------------------------------------------------------------------
# Success path
# --------------------------------------------------------------------------


async def test_inventory_unwraps_jsonapi_envelope():
    adapter = _mock(handler=_router)
    result = await adapter.fetch_inventory()
    assert result.ok is True
    assert len(result.data) == 2
    assert result.data[0]["id"] == "dev-1"


async def test_inventory_captures_request_id():
    adapter = _mock(handler=_router)
    result = await adapter.fetch_inventory()
    assert result.request_id == "req-abc"


async def test_bearer_token_is_sent_and_never_in_the_url():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=DEVICES_BODY)

    adapter = _mock(token="super-secret-token", handler=handler)
    await adapter.fetch_inventory()
    assert seen["auth"] == "Bearer super-secret-token"
    assert "super-secret-token" not in seen["url"]


async def test_history_uses_the_plural_event_types_parameter():
    """Ring ignores event_type; only event_types (plural) is honoured."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=HISTORY_BODY)

    adapter = _mock(handler=handler)
    await adapter.fetch_history("dev-1")
    assert HISTORY_EVENT_TYPES_PARAM in seen["params"]
    assert "event_type" not in seen["params"]
    assert "filter[event_type]" not in seen["params"]


async def test_history_limit_is_applied():
    body = {"data": [{"id": f"ev-{i}", "attributes": {"event_type": "motion", "start": "2026-10-20T11:00:00Z"}} for i in range(10)]}
    adapter = _mock(handler=lambda r: httpx.Response(200, json=body))
    result = await adapter.fetch_history("dev-1", limit=3)
    assert len(result.data) == 3


# --------------------------------------------------------------------------
# Failure paths -- each must be classified distinctly
# --------------------------------------------------------------------------


async def test_401_is_classified_as_auth_failure():
    adapter = _mock(handler=lambda r: httpx.Response(401))
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.AUTH_FAILED
    assert result.failure.http_status == 401


async def test_403_is_classified_as_forbidden_not_auth_failure():
    adapter = _mock(handler=lambda r: httpx.Response(403))
    result = await adapter.fetch_inventory()
    assert result.failure.kind is FailureKind.FORBIDDEN


async def test_404_is_classified_as_not_found():
    adapter = _mock(handler=lambda r: httpx.Response(404))
    result = await adapter.fetch_inventory()
    assert result.failure.kind is FailureKind.NOT_FOUND


async def test_429_is_classified_as_rate_limited():
    adapter = _mock(handler=lambda r: httpx.Response(429))
    result = await adapter.fetch_inventory()
    assert result.failure.kind is FailureKind.RATE_LIMITED


async def test_500_is_classified_as_server_error():
    adapter = _mock(handler=lambda r: httpx.Response(503))
    result = await adapter.fetch_inventory()
    assert result.failure.kind is FailureKind.SERVER_ERROR


async def test_network_error_is_classified_as_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = _mock(handler=handler)
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.NETWORK_ERROR


async def test_timeout_is_classified_as_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    adapter = _mock(handler=handler)
    result = await adapter.fetch_inventory()
    assert result.failure.kind is FailureKind.TIMEOUT


async def test_malformed_json_is_classified_and_does_not_raise():
    adapter = _mock(handler=lambda r: httpx.Response(200, content=b"<html>not json</html>"))
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.MALFORMED_RESPONSE


async def test_successful_empty_response_is_a_success_not_a_failure():
    """The distinction the whole product depends on."""
    adapter = _mock(handler=lambda r: httpx.Response(200, json={"data": []}))
    result = await adapter.fetch_inventory()
    assert result.ok is True
    assert result.data == []


async def test_unreadable_envelope_is_a_failure_not_an_empty_result():
    """A 200 response WITNESS cannot read is not "the source has no devices".

    This test previously asserted the opposite -- that an unrecognised payload
    silently became an empty success. That behaviour let a sustained upstream
    error envelope (`{"error": "..."}` with HTTP 200) masquerade as repeated
    successful empty inventories, which the presence rules then turned into an
    asserted device withdrawal. The adapter must refuse the payload instead.
    """
    adapter = _mock(handler=lambda r: httpx.Response(200, json={"unexpected": "shape"}))
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.MALFORMED_RESPONSE
    assert "data" in result.failure.message


async def test_redirect_is_not_followed():
    """A redirect must not be followed off-host.

    The security property is not merely that the call fails, but that WITNESS
    never issues a request to the redirect target. Asserting only on the result
    would pass even if the adapter leaked a request (and its bearer token) to
    the attacker's host.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "evil.example" in str(request.url):
            return httpx.Response(200, json=DEVICES_BODY)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    adapter = _mock(handler=handler)
    result = await adapter.fetch_inventory()

    assert result.ok is False
    assert result.failure.kind is FailureKind.NETWORK_ERROR
    assert not any("evil.example" in url for url in seen), (
        f"adapter followed a redirect off-host; requests made: {seen}"
    )


async def test_redirect_response_body_is_not_parsed_as_data():
    """A 3xx body must never be unwrapped into device records."""
    adapter = _mock(handler=lambda r: httpx.Response(
        301,
        headers={"Location": "https://evil.example"},
        json=DEVICES_BODY,
    ))
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.data is None
    assert result.failure is not None


@pytest.mark.parametrize("status", [300, 301, 302, 303, 307, 308])
async def test_every_redirect_class_is_a_failure(status):
    adapter = _mock(handler=lambda r: httpx.Response(status, headers={"Location": "https://evil.example"}))
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.NETWORK_ERROR



# --------------------------------------------------------------------------
# Credential handling
# --------------------------------------------------------------------------


async def test_missing_token_is_not_configured_and_makes_no_request():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=DEVICES_BODY)

    adapter = RingAdapter(access_token=None, transport=httpx.MockTransport(handler))
    adapter._token = None
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.NOT_CONFIGURED
    assert calls["n"] == 0, "must not contact the network without credentials"


async def test_token_is_redacted_from_error_messages():
    adapter = _mock(token="leaky-token", handler=lambda r: httpx.Response(500))
    adapter._token = "leaky-token"
    result = await adapter.fetch_inventory()
    assert "leaky-token" not in result.failure.message
    assert "leaky-token" not in json.dumps(result.failure.to_dict())


async def test_token_fingerprint_is_not_the_token():
    adapter = RingAdapter(access_token="my-secret")
    fingerprint = adapter.token_fingerprint()
    assert fingerprint is not None
    assert "my-secret" not in fingerprint
    assert len(fingerprint) == 12


async def test_configured_reflects_token_presence():
    assert RingAdapter(access_token="x").configured is True
    adapter = RingAdapter()
    adapter._token = None
    assert adapter.configured is False


# --------------------------------------------------------------------------
# Capability declaration
# --------------------------------------------------------------------------


def test_ring_capabilities_do_not_claim_verified_status():
    caps = RingAdapter(access_token="x").capabilities()
    assert all(v != "VERIFIED" for v in caps.values()), (
        "the Ring adapter has not been exercised against a live credential, "
        "so it must not report VERIFIED capabilities"
    )
    assert caps["event_classification_via_history"] == "UNAVAILABLE"


def test_simulated_capabilities_are_all_labelled_simulated():
    caps = SimulatedAdapter().capabilities()
    assert all(v == "SIMULATED" for v in caps.values())


# --------------------------------------------------------------------------
# Normalisation helpers
# --------------------------------------------------------------------------


def test_normalise_device_from_jsonapi():
    record = _normalise_device(DEVICES_BODY["data"][0])
    assert record.device_id == "dev-1"
    assert record.device_label == "Front Door"
    assert record.device_kind == "doorbell"
    assert record.online is True


def test_normalise_device_falls_back_to_id_when_unnamed():
    record = _normalise_device({"id": "dev-9", "attributes": {}})
    assert record.device_label == "dev-9"


def test_normalise_event_reads_start_as_the_source_time():
    record = _normalise_event(HISTORY_BODY["data"][0], retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    assert record.event_id == "ev-1"
    assert record.reported_at is not None
    assert record.classification is None, "history carries no classification field"


def test_normalise_event_reads_sub_type_when_present():
    raw = {"id": "ev-2", "attributes": {"event_type": "motion", "sub_type": "human", "start": "2026-10-20T11:00:00Z"}}
    from datetime import datetime, timezone

    record = _normalise_event(raw, retrieved_at=datetime.now(timezone.utc))
    assert record.classification == "human"


def test_read_encryption_distinguishes_absent_from_false():
    assert _read_encryption({"e2e_encryption": {"enabled": True}}) == (True, True)
    assert _read_encryption({"e2e_encryption": {"enabled": False}}) == (True, False)
    assert _read_encryption({}) == (False, None)
    assert _read_encryption({"e2e_encryption": {}}) == (False, None)


def test_read_encryption_refuses_a_payload_that_is_not_an_object():
    """A non-object configurations payload is unreadable, not "encryption off".

    Reporting (False, None) here is correct -- it means "the source did not
    state" -- but it must never become (True, False), which would assert that
    the device is unencrypted when the source said nothing.
    """
    assert _read_encryption(None) == (False, None)
    assert _read_encryption("not a dict") == (False, None)
    assert _read_encryption(42) == (False, None)
    assert _read_encryption({"e2e_encryption": "yes"}) == (False, None)


def test_read_encryption_refuses_a_non_boolean_enabled_value():
    """Only a real boolean states an encryption state.

    ``enabled`` is documented as a boolean. Any other value is a shape WITNESS
    cannot read, and coercing it invents a claim the source did not make:
    ``bool("false")`` is ``True``, so a stringified false would be reported as
    "this device is end-to-end encrypted", and ``bool(None)`` is ``False``, so
    an explicit null would be reported as "this device is not encrypted".
    Both are fabrications. The honest reading is "the source did not state it".
    """
    assert _read_encryption({"e2e_encryption": {"enabled": "false"}}) == (False, None)
    assert _read_encryption({"e2e_encryption": {"enabled": "true"}}) == (False, None)
    assert _read_encryption({"e2e_encryption": {"enabled": None}}) == (False, None)
    assert _read_encryption({"e2e_encryption": {"enabled": 1}}) == (False, None)
    assert _read_encryption({"e2e_encryption": {"enabled": 0}}) == (False, None)


def test_normalise_device_refuses_a_non_boolean_encryption_state():
    """A stated encryption state must be a real boolean.

    The normalised branch is fed WITNESS's own device dicts. If a caller states
    an encryption value that is not a boolean, keeping it would let
    ``rule_content_state`` fall through to CONTENT_VISIBLE and print "reported
    e2e_encryption.enabled = false" -- asserting an unencrypted device from a
    value the source never stated. "Stated" and "is a boolean" must agree.
    """
    for bad in ("yes", "false", 1, 0, None, "true"):
        device = _normalise_device(
            {"device_id": "d1", "device_label": "Door", "encryption_stated": True, "e2e_encrypted": bad}
        )
        assert device is not None
        assert device.encryption_stated is False, bad
        assert device.e2e_encrypted is None, bad

    stated = _normalise_device(
        {"device_id": "d1", "device_label": "Door", "encryption_stated": True, "e2e_encrypted": True}
    )
    assert stated.encryption_stated is True
    assert stated.e2e_encrypted is True


# --------------------------------------------------------------------------
# Observation collection
# --------------------------------------------------------------------------


async def test_collect_observation_records_success_and_scope():
    adapter = _mock(handler=_router)
    obs = await collect_observation(adapter)
    assert obs.source_id == "ring"
    assert obs.source_kind == "live"
    assert obs.inventory.ok is True
    assert len(obs.devices) == 2
    assert obs.history is not None and obs.history.ok
    assert obs.configurations is not None and obs.configurations.ok


async def test_live_configurations_response_states_encryption():
    """The live Ring path must read encryption from the JSON:API envelope.

    Ring returns configurations as ``{"data": {"attributes": {...}}}``. The
    reader expects the unwrapped attributes object, so if the adapter hands it
    the raw envelope the encryption field is never seen. The device is then
    reported as CONTENT_UNAVAILABLE with an explanation claiming the response
    "did not include an encryption field" -- which is false: it did.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/devices":
            return httpx.Response(200, json=DEVICES_BODY)
        if request.url.path.startswith("/v1/history/devices/"):
            return httpx.Response(200, json=HISTORY_BODY)
        if request.url.path.endswith("/configurations"):
            return httpx.Response(200, json=CONFIG_ENCRYPTED)
        return httpx.Response(404)

    obs = await collect_observation(_mock(handler=handler))
    primary = obs.devices[0]
    assert primary.encryption_stated is True
    assert primary.e2e_encrypted is True

    recon = reconcile("ring", [obs])
    view = next(v for v in recon.devices if v.device_id == primary.device_id)
    assert view.content.status is Status.SOURCE_ENCRYPTED


async def test_live_configurations_plain_response_states_not_encrypted():
    """The converse: an explicit false must be read as a stated false."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/devices":
            return httpx.Response(200, json=DEVICES_BODY)
        if request.url.path.startswith("/v1/history/devices/"):
            return httpx.Response(200, json=HISTORY_BODY)
        if request.url.path.endswith("/configurations"):
            return httpx.Response(200, json=CONFIG_PLAIN)
        return httpx.Response(404)

    obs = await collect_observation(_mock(handler=handler))
    primary = obs.devices[0]
    assert primary.encryption_stated is True
    assert primary.e2e_encrypted is False

    recon = reconcile("ring", [obs])
    view = next(v for v in recon.devices if v.device_id == primary.device_id)
    assert view.content.status is Status.CONTENT_VISIBLE


async def test_collect_observation_contains_adapter_exceptions():
    class Exploding(RingAdapter):
        async def fetch_inventory(self):
            raise RuntimeError("boom")

    adapter = Exploding(access_token="x")
    obs = await collect_observation(adapter)
    assert obs.inventory.ok is False
    assert obs.inventory.failure.kind is FailureKind.SERVER_ERROR


async def test_collect_observation_makes_no_history_call_when_inventory_is_empty():
    adapter = _mock(handler=lambda r: httpx.Response(200, json={"data": []}))
    obs = await collect_observation(adapter)
    assert obs.devices == ()
    assert obs.history is None


async def test_collect_observation_records_failed_inventory_without_history_calls():
    adapter = _mock(handler=lambda r: httpx.Response(500))
    obs = await collect_observation(adapter)
    assert obs.inventory.ok is False
    assert obs.devices == ()


# --------------------------------------------------------------------------
# Simulated adapter
# --------------------------------------------------------------------------


async def test_simulated_source_is_labelled_simulated():
    adapter = SimulatedAdapter()
    assert adapter.source_kind == "simulated"
    assert "simulated" in adapter.source_id
    assert "Simulated" in adapter.display_name


async def test_simulated_scenario_walks_through_the_taxonomy():
    adapter = SimulatedAdapter()
    frames = []
    while True:
        obs = await collect_observation(adapter)
        frames.append(obs)
        if not adapter.advance():
            break
    assert len(frames) == len(DEFAULT_SCENARIO)

    assert frames[0].inventory.ok and len(frames[0].devices) == 2
    assert frames[1].inventory.ok and len(frames[1].devices) == 1
    assert frames[2].inventory.ok is False
    assert frames[3].inventory.ok and len(frames[3].devices) == 1


async def test_simulated_source_reports_inventory_failure_not_empty_success():
    """Frame 2 is a source error. It must surface as a failure, not an empty success."""
    adapter = SimulatedAdapter()
    await collect_observation(adapter)  # frame 0
    assert adapter.advance()
    await collect_observation(adapter)  # frame 1
    assert adapter.advance()
    obs = await collect_observation(adapter)  # frame 2
    assert obs.inventory.ok is False
    assert obs.inventory.failure.kind is FailureKind.SERVER_ERROR


async def test_simulated_reset_returns_to_the_first_frame():
    adapter = SimulatedAdapter()
    adapter.advance()
    adapter.advance()
    adapter.reset()
    assert adapter.cursor == 0


async def test_observation_and_its_requests_share_one_clock():
    """A pass must not be split across two clock domains.

    collect_observation used to stamp the observation from a separate wall-clock
    read while the adapter stamped its own responses. For the simulated source
    that put the observation before the inventory response it contains, and put
    later frames' events in the future relative to their own retrieval.
    """
    adapter = SimulatedAdapter()
    while True:
        obs = await collect_observation(adapter)
        assert obs.observed_at == obs.inventory.observed_at
        for event in obs.events:
            assert event.reported_at <= event.retrieved_at
            assert event.retrieved_at == obs.history.observed_at
        if not adapter.advance():
            break


async def test_late_event_delay_matches_the_scripted_offset():
    """The reported delay must be the delay the scenario describes.

    Frame 3 scripts ev-0900 at -90 minutes. Computing the delay against a
    retrieval time from another clock domain made it read as 59m 59s.
    """
    adapter = SimulatedAdapter()
    obs = None
    while True:
        obs = await collect_observation(adapter)
        if not adapter.advance():
            break

    late = [e for e in obs.events if e.event_id == "ev-0900"]
    assert len(late) == 1
    assert late[0].retrieved_at - late[0].reported_at == timedelta(minutes=90)


async def test_simulated_observations_are_not_dated_in_the_future():
    """The demo must never present a time that has not happened yet.

    The simulated clock used to run ahead of wall time, so events in later
    frames were dated in the future -- including relative to the observation
    that was supposed to have retrieved them.
    """
    adapter = SimulatedAdapter()
    while True:
        obs = await collect_observation(adapter)
        assert obs.observed_at <= utcnow()
        for event in obs.events:
            assert event.reported_at <= utcnow()
            assert event.retrieved_at <= utcnow()
        if not adapter.advance():
            break


async def test_simulated_session_never_reports_itself_stale():
    """The scripted session must fit inside the staleness window.

    Frames recede from the current moment. If the spacing pushed the earliest
    frame past the staleness threshold, the demo would flag its own fresh
    evidence as stale the moment it took a single observation.
    """
    adapter = SimulatedAdapter()
    observations = []
    while True:
        observations.append(await collect_observation(adapter))
        if not adapter.advance():
            break

    for count in range(1, len(observations) + 1):
        recon = reconcile("ring-simulated", observations[:count], now=utcnow())
        assert recon.staleness["stale"] is False, recon.staleness


# --------------------------------------------------------------------------
# Malformed inventories must not fabricate devices
# --------------------------------------------------------------------------


def test_normalise_device_refuses_an_entry_with_no_identity():
    """An unidentifiable entry must not become a device.

    Regression: a non-dict entry produced a placeholder device with the id
    "unknown". Several such entries collapsed into one fabricated device, and
    that device was then reported to the user as OBSERVED -- a device that does
    not exist, asserted as present.
    """
    assert _normalise_device(1) is None
    assert _normalise_device("device") is None
    assert _normalise_device(None) is None
    assert _normalise_device([]) is None
    assert _normalise_device({"type": "device", "attributes": {"name": "X"}}) is None


def test_normalise_device_refuses_an_empty_id():
    assert _normalise_device({"id": "", "attributes": {"name": "X"}}) is None
    assert _normalise_device({"id": "   ", "attributes": {"name": "X"}}) is None
    assert _normalise_device({"device_id": ""}) is None


def test_normalise_device_accepts_a_valid_resource():
    record = _normalise_device({"id": "d1", "attributes": {"name": "Front Door"}})
    assert record is not None
    assert record.device_id == "d1"
    assert record.device_label == "Front Door"


def test_normalise_device_tolerates_null_attributes():
    """A device with a null attributes block still has a usable identity."""
    record = _normalise_device({"id": "d1", "type": "device", "attributes": None})
    assert record is not None
    assert record.device_id == "d1"


def test_normalise_device_list_reports_unreadable_entries():
    devices, problem = normalise_device_list([1, 2, 3])
    assert devices == ()
    assert problem is not None
    assert "3 of 3" in problem


def test_normalise_device_list_flags_a_partially_readable_payload():
    """Dropping the unreadable entries would make them look absent.

    An absent device is one step away from an asserted withdrawal, so a partial
    read must be reported rather than silently narrowed.
    """
    devices, problem = normalise_device_list(
        [{"id": "d1", "attributes": {"name": "Real"}}, 42]
    )
    assert problem is not None
    assert "1 of 2" in problem


def test_normalise_device_list_accepts_a_clean_payload():
    devices, problem = normalise_device_list(
        [{"id": "d1", "attributes": {"name": "A"}}, {"id": "d2", "attributes": {"name": "B"}}]
    )
    assert problem is None
    assert [d.device_id for d in devices] == ["d1", "d2"]


def test_normalise_device_list_accepts_a_genuinely_empty_payload():
    devices, problem = normalise_device_list([])
    assert devices == ()
    assert problem is None, "an empty list is a real answer, not a problem"


def test_normalise_device_list_rejects_a_non_list():
    devices, problem = normalise_device_list({"data": "nonsense"})
    assert devices == ()
    assert problem is not None


@pytest.mark.parametrize(
    "body",
    [
        [1, 2, 3],
        {"data": [{"type": "device", "attributes": {"name": "X"}}]},
        {"data": [{"id": "d1", "attributes": {"name": "Real"}}, 42]},
    ],
)
async def test_malformed_inventory_fails_rather_than_inventing_devices(body):
    """The whole pipeline must refuse a payload it cannot read."""
    adapter = _mock(handler=lambda r: httpx.Response(200, json=body))
    obs = await collect_observation(adapter)

    assert obs.inventory.ok is False
    assert obs.inventory.failure.kind is FailureKind.MALFORMED_RESPONSE
    assert obs.devices == (), "no device may be fabricated from an unreadable payload"


async def test_malformed_inventory_is_not_read_as_zero_devices():
    """A malformed payload must not look like a device list that shrank.

    If it did, the next successful observation would show every device as
    absent, and repeated absences become an asserted withdrawal.
    """
    adapter = _mock(handler=lambda r: httpx.Response(200, json=[1, 2, 3]))
    obs = await collect_observation(adapter)

    assert obs.inventory.ok is False
    assert obs.inventory.failure is not None
    assert "interpreted" in obs.inventory.failure.message


async def test_genuinely_empty_inventory_is_still_a_success():
    """The malformed-payload guard must not break the empty-success case."""
    adapter = _mock(handler=lambda r: httpx.Response(200, json={"data": []}))
    obs = await collect_observation(adapter)

    assert obs.inventory.ok is True
    assert obs.inventory.failure is None
    assert obs.devices == ()



# --------------------------------------------------------------------------
# Defect #3: an unreadable envelope must never look like an empty inventory
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "upstream temporarily unavailable"},
        {"data": "oops"},
        {"data": 42},
        {"meta": {"count": 0}},
        "a bare string",
        42,
    ],
)
def test_unreadable_envelopes_are_reported_as_problems(payload):
    rows, problem = _extract_data_list(payload)
    assert list(rows) == []
    assert problem is not None, f"{payload!r} was silently read as an empty list"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"data": []}, []),
        ({"data": None}, []),
        ([], []),
        ({"data": [{"id": "d1"}]}, [{"id": "d1"}]),
        ({"data": {"id": "d1"}}, [{"id": "d1"}]),
    ],
)
def test_readable_envelopes_are_not_flagged_as_problems(payload, expected):
    """The guard must not break the legitimate shapes it sits in front of."""
    rows, problem = _extract_data_list(payload)
    assert problem is None
    assert rows == expected


async def test_error_envelope_with_http_200_fails_the_inventory_request():
    adapter = _mock(handler=lambda r: httpx.Response(200, json={"error": "rate limited"}))
    result = await adapter.fetch_inventory()
    assert result.ok is False
    assert result.failure.kind is FailureKind.MALFORMED_RESPONSE


async def test_error_envelope_does_not_become_an_asserted_withdrawal():
    """The end-to-end consequence of Defect #3.

    A device is seen once, then the upstream starts answering HTTP 200 with an
    error envelope. Before the fix, those responses counted as successful empty
    inventories, and the presence rules asserted that the device had been
    withdrawn -- a device-level conclusion drawn from a response WITNESS could
    not read. It must instead report that it could not look.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=DEVICES_BODY)
        return httpx.Response(200, json={"error": "upstream temporarily unavailable"})

    adapter = _mock(handler=handler)
    observations = [await collect_observation(adapter) for _ in range(8)]

    from witness.engine.reconcile import reconcile

    recon = reconcile("ring", observations)
    statuses = {v.device_id: v.presence.status.value for v in recon.devices}
    assert "SOURCE_WITHDRAWN" not in statuses.values(), (
        f"an unreadable envelope produced a withdrawal: {statuses}"
    )
    assert recon.source_assessment is not None
    assert recon.source_assessment.status.value == "SOURCE_UNAVAILABLE"


async def test_error_envelope_in_history_fails_the_history_request():
    def handler(request: httpx.Request) -> httpx.Response:
        if "history" in request.url.path:
            return httpx.Response(200, json={"error": "nope"})
        if "configurations" in request.url.path:
            return httpx.Response(200, json=CONFIG_PLAIN)
        return httpx.Response(200, json=DEVICES_BODY)

    obs = await collect_observation(_mock(handler=handler))
    assert obs.history is not None
    assert obs.history.ok is False
    assert obs.history.failure.kind is FailureKind.MALFORMED_RESPONSE


# --------------------------------------------------------------------------
# Defect #4: unreadable history entries must not become phantom events
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", [42, "nonsense", {"no": "identity"}, None, []])
def test_unreadable_event_entries_are_refused(entry):
    from witness.engine.models import utcnow

    assert _normalise_event(entry, retrieved_at=utcnow()) is None


def test_event_with_an_empty_id_is_refused():
    from witness.engine.models import utcnow

    assert _normalise_event({"id": "  ", "attributes": {}}, retrieved_at=utcnow()) is None
    assert _normalise_event({"event_id": "", "event_type": "motion"}, retrieved_at=utcnow()) is None


def test_normalise_event_list_reports_unreadable_entries():
    from witness.engine.models import utcnow

    events, problem = normalise_event_list([42, {"no": "id"}], retrieved_at=utcnow())
    assert events == []
    assert problem is not None
    assert "2 of 2" in problem


def test_normalise_event_list_accepts_a_clean_payload():
    from witness.engine.models import utcnow

    events, problem = normalise_event_list(HISTORY_BODY["data"], retrieved_at=utcnow())
    assert problem is None
    assert len(events) == 1
    assert events[0].event_id == "ev-1"


def test_normalise_event_list_treats_absent_history_as_empty_not_malformed():
    from witness.engine.models import utcnow

    events, problem = normalise_event_list(None, retrieved_at=utcnow())
    assert events == []
    assert problem is None


async def test_unreadable_history_does_not_fabricate_a_duplicate_finding():
    """The end-to-end consequence of Defect #4.

    Three unreadable history entries used to become three phantom events with
    the same empty identity, which the engine then reported as a real
    EVENT_DUPLICATE. WITNESS must report that it could not read the history.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if "history" in request.url.path:
            return httpx.Response(200, json={"data": [42, "nonsense", {"no": "id"}]})
        if "configurations" in request.url.path:
            return httpx.Response(200, json=CONFIG_PLAIN)
        return httpx.Response(200, json=DEVICES_BODY)

    obs = await collect_observation(_mock(handler=handler))

    assert obs.history is not None and obs.history.ok is False
    assert obs.events == (), "no event may be fabricated from an unreadable payload"

    from witness.engine.reconcile import reconcile

    recon = reconcile("ring", [obs])
    assert recon.events == ()

