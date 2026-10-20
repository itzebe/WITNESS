"""API tests using FastAPI's TestClient against a temporary database.

These cover the real HTTP surface: status codes, the shape of each response,
and the honesty guarantees the API is required to uphold (for example, that a
failed request never surfaces as an empty device list).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from witness.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "api-test.db"))
    with TestClient(app) as c:
        yield c


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_sources_lists_both_and_labels_the_simulated_one(client):
    body = client.get("/api/sources").json()
    ids = {s["source_id"]: s for s in body["sources"]}
    assert "ring" in ids
    assert "ring-simulated" in ids
    assert ids["ring-simulated"]["is_simulated"] is True
    assert ids["ring-simulated"]["source_kind"] == "simulated"
    assert ids["ring"]["source_kind"] == "live"


def test_status_spec_exposes_the_documented_taxonomy(client):
    body = client.get("/api/status-spec").json()
    statuses = {s["status"] for s in body["statuses"]}
    for expected in (
        "OBSERVED",
        "NOT_OBSERVED",
        "SOURCE_UNAVAILABLE",
        "SOURCE_WITHDRAWN",
        "SOURCE_ENCRYPTED",
        "EVENT_LATE",
        "EVENT_DUPLICATE",
        "CLASSIFICATION_UNAVAILABLE",
        "INSUFFICIENT_EVIDENCE",
    ):
        assert expected in statuses
    not_observed = next(s for s in body["statuses"] if s["status"] == "NOT_OBSERVED")
    assert not_observed["does_not_establish"]


def test_ring_connection_never_leaks_a_token(client):
    """The connection endpoint may name the env var but must never carry a value."""
    body = client.get("/api/ring/connection").json()
    # No field may hold a credential.
    assert "access_token" not in body
    assert body["token_fingerprint"] is None
    assert body["configured"] is False
    # The only place the word "token" may appear is the fingerprint field name
    # and the instruction naming the environment variable.
    for key, value in body.items():
        if key != "verification_note":
            assert not (isinstance(value, str) and len(value) > 40 and "eyJ" in value), (
                f"{key} looks like a JWT"
            )


def test_ring_connection_reports_configuration_without_the_secret(tmp_path, monkeypatch):
    """With a token present, only a short fingerprint is exposed."""
    monkeypatch.setenv("RING_ACCESS_TOKEN", "eyJhbGciOiJIUzI1NiJ9.secret-payload.sig")
    app = create_app(db_path=str(tmp_path / "cfg.db"))
    with TestClient(app) as c:
        body = c.get("/api/ring/connection").json()
    assert body["configured"] is True
    assert body["token_fingerprint"] is not None
    assert len(body["token_fingerprint"]) == 12
    assert "secret-payload" not in json.dumps(body)


def test_observe_unknown_source_is_404(client):
    assert client.post("/api/observe/nope").status_code == 404


def test_reconcile_unknown_source_is_404(client):
    assert client.get("/api/reconcile/nope").status_code == 404


def test_observe_then_reconcile_round_trip(client):
    observed = client.post("/api/observe/ring-simulated")
    assert observed.status_code == 200
    body = observed.json()
    assert body["reconciliation"]["counts"]["observed"] == 2

    reconciled = client.get("/api/reconcile/ring-simulated").json()
    assert reconciled["counts"]["observed"] == 2
    assert reconciled["source_kind"] == "simulated"


def test_devices_endpoint_returns_assessments_with_evidence(client):
    client.post("/api/observe/ring-simulated")
    body = client.get("/api/devices/ring-simulated").json()
    assert len(body["devices"]) == 2
    for device in body["devices"]:
        assert device["presence"]["rule_id"].startswith("rule:")
        assert device["presence"]["evidence_refs"]
        assert device["presence"]["does_not_establish"]
        assert device["content"]["rule_id"].startswith("rule:")


def test_full_scenario_over_http_shows_withdrawal(client):
    for _ in range(4):
        client.post("/api/observe/ring-simulated")
        client.post("/api/simulated/advance")
    body = client.get("/api/reconcile/ring-simulated").json()
    statuses = {d["presence"]["status"] for d in body["devices"]}
    assert "SOURCE_WITHDRAWN" in statuses


def test_simulated_state_reports_its_label(client):
    body = client.get("/api/simulated/state").json()
    assert body["source_kind"] == "simulated"
    assert body["frames"] >= 4
    assert body["frame_note"]


def test_simulated_reset_and_advance(client):
    client.post("/api/simulated/advance")
    assert client.get("/api/simulated/state").json()["cursor"] == 1
    client.post("/api/simulated/reset")
    assert client.get("/api/simulated/state").json()["cursor"] == 0


def test_advance_at_the_end_of_the_scenario_does_not_move(client):
    for _ in range(10):
        client.post("/api/simulated/advance")
    state = client.get("/api/simulated/state").json()
    assert state["cursor"] == state["frames"] - 1
    assert client.post("/api/simulated/advance").json()["moved"] is False


def test_timeline_labels_source_kind_and_failures(client):
    for _ in range(3):
        client.post("/api/observe/ring-simulated")
        client.post("/api/simulated/advance")
    body = client.get("/api/timeline/ring-simulated").json()
    assert all(e["source_kind"] == "simulated" for e in body["entries"])
    assert any(e["inventory_ok"] is False for e in body["entries"])


def test_ledger_verify_endpoint_reports_valid(client):
    client.post("/api/observe/ring-simulated")
    body = client.get("/api/ledger/verify").json()
    assert body["valid"] is True
    assert body["record_count"] > 0


def test_ledger_endpoint_paginates(client):
    client.post("/api/observe/ring-simulated")
    full = client.get("/api/ledger?limit=1000").json()
    page = client.get("/api/ledger?limit=2&offset=0").json()
    assert len(page["records"]) == 2
    assert page["record_count"] == full["record_count"]


def test_ledger_export_is_downloadable_and_verifiable(client):
    client.post("/api/observe/ring-simulated")
    response = client.get("/api/ledger/export")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    body = response.json()
    assert body["verification"]["valid"] is True
    assert body["record_count"] == len(body["records"])


def test_overview_includes_policy_and_ledger_state(client):
    client.post("/api/observe/ring-simulated")
    body = client.get("/api/overview").json()
    assert body["policy"]["withdrawal_threshold"] == 2
    assert body["ledger"]["verification"]["valid"] is True
    assert "ring-simulated" in body["reconciliations"]


def test_overview_before_any_observation_is_empty_not_fabricated(client):
    body = client.get("/api/overview").json()
    assert body["ledger"]["record_count"] == 0
    recon = body["reconciliations"]["ring-simulated"]
    assert recon["devices"] == []
    assert recon["latest_observation_at"] is None


# --------------------------------------------------------------------------
# Error paths a visitor can actually reach
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/devices/nope", "/api/timeline/nope"])
def test_device_and_timeline_endpoints_404_for_an_unknown_source(client, path):
    """A mistyped or stale source id must say so, not return an empty list.

    An empty device list is a meaningful answer in this product ("the source
    returned nothing"). Returning one for an unknown source would let a caller
    mistake "no such source" for "source reports no devices".
    """
    response = client.get(path)
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_unknown_source_error_is_not_an_empty_result():
    """Contrast the two cases directly, using the same client."""
    from fastapi.testclient import TestClient

    from witness.api import create_app

    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        unknown = c.get("/api/devices/nope")
        assert unknown.status_code == 404

        c.post("/api/observe/ring-simulated")
        known = c.get("/api/devices/ring-simulated")
        assert known.status_code == 200
        assert "devices" in known.json()


def test_timeline_limit_is_respected(client):
    for _ in range(3):
        client.post("/api/observe/ring-simulated")
    body = client.get("/api/timeline/ring-simulated?limit=1").json()
    assert len(body["entries"]) == 1


def test_simulated_state_is_reported_before_any_advance(client):
    body = client.get("/api/simulated/state").json()
    assert "cursor" in body or "frame" in body or "total_frames" in body


def test_simulated_reset_returns_to_the_first_frame(client):
    client.post("/api/simulated/advance")
    client.post("/api/simulated/advance")
    client.post("/api/simulated/reset")
    state = client.get("/api/simulated/state").json()
    assert state.get("cursor") == 0


def test_root_serves_the_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_ledger_verify_is_valid_before_anything_is_recorded(client):
    body = client.get("/api/ledger/verify").json()
    assert body["valid"] is True


def test_ledger_export_is_downloadable_json(client):
    client.post("/api/observe/ring-simulated")
    response = client.get("/api/ledger/export")
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["records"]
    assert payload["verification"]["valid"] is True

