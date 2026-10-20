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

import httpx
import pytest

from witness.adapters.base import _normalise_device, _normalise_event, _read_encryption, collect_observation
from witness.adapters.ring import HISTORY_EVENT_TYPES_PARAM, RingAdapter
from witness.adapters.simulated import DEFAULT_SCENARIO, SimulatedAdapter
from witness.engine.models import FailureKind


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


async def test_unexpected_response_shape_does_not_raise():
    adapter = _mock(handler=lambda r: httpx.Response(200, json={"unexpected": "shape"}))
    result = await adapter.fetch_inventory()
    assert result.ok is True
    assert result.data == []


async def test_redirect_is_not_followed():
    adapter = _mock(handler=lambda r: httpx.Response(302, headers={"Location": "https://evil.example"}))
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
