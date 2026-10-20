"""Tests for the shared data types.

``parse_iso`` is small but load-bearing: it decides whether WITNESS knows when
something happened. A bug here is silent -- an unparseable timestamp returns
None, which downstream rules treat as "time unknown", so late-event detection
quietly stops working without any test going red unless the function itself is
tested directly. That happened once; these tests exist so it cannot again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from witness.engine.models import (
    Failure,
    FailureKind,
    FetchResult,
    iso,
    parse_iso,
    utcnow,
)

T0 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# parse_iso -- the "do we know when this happened" decision
# --------------------------------------------------------------------------


def test_parse_iso_accepts_an_aware_datetime_unchanged():
    """A source that hands us a datetime must not lose its value.

    Regression: the function originally accepted only strings, so a datetime
    from the simulated adapter was silently discarded as None. Late-arrival
    detection then never fired, and every event looked punctual.
    """
    assert parse_iso(T0) == T0


def test_parse_iso_treats_a_naive_datetime_as_utc():
    naive = datetime(2026, 10, 20, 12, 0, 0)
    assert parse_iso(naive) == T0
    assert parse_iso(naive).tzinfo is not None


def test_parse_iso_parses_a_zulu_string():
    assert parse_iso("2026-10-20T12:00:00Z") == T0


def test_parse_iso_parses_an_offset_string():
    assert parse_iso("2026-10-20T14:00:00+02:00") == T0


def test_parse_iso_parses_a_naive_string_as_utc():
    parsed = parse_iso("2026-10-20T12:00:00")
    assert parsed == T0
    assert parsed.tzinfo is not None


def test_parse_iso_resolves_different_offsets_to_the_same_instant():
    """An offset timestamp must compare equal to its UTC equivalent.

    parse_iso preserves the source's offset rather than rewriting it; every
    comparison is on the instant, and serialisation normalises through iso().
    What matters is that two spellings of the same moment are equal.
    """
    assert parse_iso("2026-10-20T14:00:00+02:00") == T0
    assert parse_iso("2026-10-20T12:00:00Z") == T0
    assert parse_iso("2026-10-20T07:00:00-05:00") == T0


@pytest.mark.parametrize(
    "value",
    [
        "not-a-timestamp",
        "",
        "   ",
        "2026-13-45T99:99:99Z",
        "20/10/2026",
        None,
        12345,
        [],
        {},
        True,
    ],
)
def test_parse_iso_returns_none_rather_than_raising(value):
    """A bad timestamp is a data-quality finding, not a crash.

    Returning None is a real answer: it means WITNESS does not know the time.
    """
    assert parse_iso(value) is None


def test_parse_iso_none_is_distinguishable_from_a_parsed_time():
    """The caller must be able to tell 'unknown time' from 'known time'."""
    assert parse_iso("nonsense") is None
    assert parse_iso("2026-10-20T12:00:00Z") is not None


# --------------------------------------------------------------------------
# iso -- the inverse
# --------------------------------------------------------------------------


def test_iso_round_trips_through_parse_iso():
    assert parse_iso(iso(T0)) == T0


def test_iso_uses_the_z_suffix():
    assert iso(T0).endswith("Z")
    assert "+00:00" not in iso(T0)


def test_iso_converts_a_non_utc_time_to_utc():
    other = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert iso(other) == "2026-10-20T12:00:00Z"


def test_iso_handles_none():
    assert iso(None) is None


def test_utcnow_is_aware_and_utc():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


# --------------------------------------------------------------------------
# Failure and FetchResult -- the empty-success / failed-request distinction
# --------------------------------------------------------------------------


def test_success_with_no_data_is_still_a_success():
    """The distinction the whole product rests on."""
    result = FetchResult.success(
        "/v1/devices", [], observed_at=T0, request_id="r1", http_status=200
    )
    assert result.ok is True
    assert result.data == []
    assert result.failure is None


def test_failure_carries_a_reason_and_is_not_ok():
    result = FetchResult.failed(
        "/v1/devices",
        Failure(kind=FailureKind.SERVER_ERROR, message="boom", http_status=500),
        observed_at=T0,
    )
    assert result.ok is False
    assert result.failure is not None
    assert result.failure.kind is FailureKind.SERVER_ERROR


def test_success_and_empty_failure_are_distinguishable():
    ok = FetchResult.success("/v1/devices", [], observed_at=T0, request_id="r", http_status=200)
    bad = FetchResult.failed(
        "/v1/devices", Failure(kind=FailureKind.TIMEOUT, message="t"), observed_at=T0
    )
    assert ok.ok != bad.ok
    assert ok.data == []
    assert bad.failure is not None


def test_a_success_cannot_carry_a_failure():
    """The type makes the forbidden state unrepresentable."""
    with pytest.raises(ValueError):
        FetchResult(
            ok=True,
            endpoint="/v1/devices",
            observed_at=T0,
            data=[],
            failure=Failure(kind=FailureKind.TIMEOUT, message="t"),
        )


def test_a_failure_must_carry_a_failure():
    with pytest.raises(ValueError):
        FetchResult(ok=False, endpoint="/v1/devices", observed_at=T0)


def test_failure_kinds_are_a_closed_set():
    """Every failure must be classifiable, so none is silently swallowed."""
    for kind in FailureKind:
        failure = Failure(kind=kind, message="x")
        assert failure.kind is kind
        assert failure.to_dict()["kind"] == kind.value


def test_response_failures_are_distinct_from_transport_failures():
    """A 404 is a response; a timeout is the absence of one. They license
    different conclusions, so the enum must keep them apart."""
    transport = {FailureKind.NETWORK_ERROR, FailureKind.TIMEOUT}
    response = {FailureKind.AUTH_FAILED, FailureKind.NOT_FOUND, FailureKind.SERVER_ERROR}
    assert transport.isdisjoint(response)

