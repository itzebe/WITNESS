"""Live Ring Partner API adapter.

This talks to the real Ring Partner API documented at
https://developer.amazon.com/docs/ring/api-documentation.html

Base URL and endpoint paths are taken from Ring's own documentation and from
the official ``ring-api-helloworld`` sample:

    GET /v1/devices
    GET /v1/devices/{device_id}/status
    GET /v1/devices/{device_id}/configurations
    GET /v1/history/devices/{device_id}/events
    GET /v1/users/me

Authentication is a bearer token. The Ring Developer Playground issues a
short-lived (~30 minute) token without requiring app registration, OAuth
account linking, or an active Ring subscription, which is the documented
lowest-friction route:

    https://developer.amazon.com/ring/console/playground

WITNESS does not perform OAuth, does not store refresh tokens, and does not
attempt to obtain credentials. If no token is configured the adapter returns a
typed NOT_CONFIGURED failure rather than pretending the source is empty.

VERIFICATION STATUS OF THIS ADAPTER
    The HTTP client, endpoint paths, JSON:API parsing, error classification and
    redaction are implemented and unit-tested against recorded/fixture
    payloads. A live authenticated call has NOT been performed by WITNESS,
    because no Ring credential is available in this environment. The adapter is
    therefore DOCUMENTED, not VERIFIED. WITNESS reports that distinction in its
    own UI rather than hiding it.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..engine.models import Failure, FailureKind, FetchResult, classify_http_status, utcnow
from .base import DEFAULT_HISTORY_LIMIT, SourceAdapter

RING_BASE_URL = "https://api.amazonvision.com"

#: Query parameter for history filtering. Ring's own developer advocate
#: confirmed on the hackathon forum that the correct parameter is ``event_types``
#: (plural, comma-separated) and that ``event_type`` / ``filter[event_type]``
#: are silently ignored. WITNESS uses the plural form.
HISTORY_EVENT_TYPES_PARAM = "event_types"

DEFAULT_TIMEOUT = 20.0


class RingAdapter(SourceAdapter):
    """Read-only adapter for the Ring Partner API."""

    source_id = "ring"
    source_kind = "live"
    display_name = "Ring Partner API"

    def __init__(
        self,
        access_token: str | None = None,
        base_url: str = RING_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = access_token or os.environ.get("RING_ACCESS_TOKEN") or None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._last_request_id: str | None = None

    # -- introspection -----------------------------------------------------

    @property
    def configured(self) -> bool:
        """True when a token is present. Never reveals the token itself."""
        return bool(self._token)

    def token_fingerprint(self) -> str | None:
        """A non-reversible short fingerprint, safe to display.

        Lets an operator confirm *which* token is loaded without exposing it.
        This is a truncated SHA-256, not the token.
        """
        if not self._token:
            return None
        import hashlib

        return hashlib.sha256(self._token.encode()).hexdigest()[:12]

    def capabilities(self) -> dict[str, str]:
        return {
            "device_discovery": "DOCUMENTED",
            "device_status": "DOCUMENTED",
            "device_configurations": "DOCUMENTED",
            "event_history": "DOCUMENTED",
            "user_profile": "DOCUMENTED",
            "motion_webhooks": "UNAVAILABLE",
            "event_classification_via_history": "UNAVAILABLE",
            "device_control": "UNAVAILABLE",
            "live_video": "DOCUMENTED",
        }

    # -- HTTP plumbing -----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _redact(self, text: str) -> str:
        """Remove anything that looks like a credential from an error string."""
        if not text:
            return text
        redacted = text
        if self._token:
            redacted = redacted.replace(self._token, "<redacted-token>")
        return redacted

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> FetchResult:
        endpoint = f"{self._base_url}{path}"
        if not self.configured:
            return FetchResult.failed(
                endpoint,
                Failure(
                    kind=FailureKind.NOT_CONFIGURED,
                    message=(
                        "No Ring access token is configured. Generate a short-lived token at "
                        "https://developer.amazon.com/ring/console/playground and set "
                        "RING_ACCESS_TOKEN."
                    ),
                    endpoint=endpoint,
                ),
            )

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = await client.get(endpoint, headers=self._headers(), params=params)
        except httpx.TimeoutException:
            return FetchResult.failed(
                endpoint,
                Failure(FailureKind.TIMEOUT, "the request timed out", endpoint=endpoint),
            )
        except httpx.HTTPError as exc:
            return FetchResult.failed(
                endpoint,
                Failure(
                    FailureKind.NETWORK_ERROR,
                    self._redact(f"network error: {type(exc).__name__}"),
                    endpoint=endpoint,
                ),
            )

        request_id = response.headers.get("x-request-id")
        self._last_request_id = request_id

        if response.status_code >= 300:
            # 3xx is not success. WITNESS never follows a redirect to a host it
            # was not configured to trust, so an unexpected redirect is treated
            # as a transport failure rather than being followed.
            return FetchResult.failed(
                endpoint,
                Failure(
                    kind=classify_http_status(response.status_code),
                    message=self._redact(f"unexpected HTTP {response.status_code} response"),
                    http_status=response.status_code,
                    endpoint=endpoint,
                ),
                observed_at=utcnow(),
            )

        try:
            payload = response.json()
        except ValueError:
            return FetchResult.failed(
                endpoint,
                Failure(
                    FailureKind.MALFORMED_RESPONSE,
                    "the response body was not valid JSON",
                    http_status=response.status_code,
                    endpoint=endpoint,
                ),
            )

        return FetchResult.success(
            endpoint,
            payload,
            observed_at=utcnow(),
            request_id=request_id,
            http_status=response.status_code,
        )

    # -- SourceAdapter -----------------------------------------------------

    async def fetch_inventory(self) -> FetchResult:
        """GET /v1/devices -> list of device resource objects."""
        result = await self._get("/v1/devices")
        if not result.ok:
            return result
        rows, problem = _extract_data_list(result.data)
        if problem is not None:
            # An unreadable envelope is not an empty inventory. Reporting it as
            # one would let sustained "successful" empty responses assert that a
            # device was withdrawn -- a conclusion drawn from a response WITNESS
            # could not read.
            return FetchResult.failed(
                result.endpoint,
                Failure(
                    kind=FailureKind.MALFORMED_RESPONSE,
                    message=problem,
                    http_status=result.http_status,
                    endpoint=result.endpoint,
                ),
                observed_at=result.observed_at,
            )
        return FetchResult.success(
            result.endpoint,
            rows,
            observed_at=result.observed_at,
            request_id=result.request_id,
            http_status=result.http_status,
        )

    async def fetch_history(
        self, device_id: str, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> FetchResult:
        """GET /v1/history/devices/{id}/events -> list of event resources."""
        result = await self._get(
            f"/v1/history/devices/{device_id}/events",
            params={HISTORY_EVENT_TYPES_PARAM: "motion.human,motion.vehicle,motion.other_motion,ding,on_demand"},
        )
        if not result.ok:
            return result
        rows, problem = _extract_data_list(result.data)
        if problem is not None:
            return FetchResult.failed(
                result.endpoint,
                Failure(
                    kind=FailureKind.MALFORMED_RESPONSE,
                    message=problem,
                    http_status=result.http_status,
                    endpoint=result.endpoint,
                ),
                observed_at=result.observed_at,
            )
        return FetchResult.success(
            result.endpoint,
            rows[:limit],
            observed_at=result.observed_at,
            request_id=result.request_id,
            http_status=result.http_status,
        )

    async def fetch_configurations(self, device_id: str) -> FetchResult:
        """GET /v1/devices/{id}/configurations -> configurations resource."""
        return await self._get(f"/v1/devices/{device_id}/configurations")

    async def fetch_status(self, device_id: str) -> FetchResult:
        return await self._get(f"/v1/devices/{device_id}/status")

    async def fetch_user_profile(self) -> FetchResult:
        return await self._get("/v1/users/me")

    async def aclose(self) -> None:
        return None


def _extract_data_list(payload: Any) -> tuple[list[Any], str | None]:
    """Pull the resource list out of a Ring JSON:API envelope.

    Ring follows JSON:API, so a collection arrives as ``{"data": [...]}``. A
    single resource arrives as ``{"data": {...}}``, and an explicit empty
    collection as ``{"data": []}`` or ``{"data": null}``.

    Returns ``(rows, problem)``. ``problem`` is a description when the payload
    is not an envelope WITNESS can read -- for example an error object such as
    ``{"error": "..."}`` returned with HTTP 200, or a ``data`` member of an
    unexpected type. Treating those as an empty list would report "the source
    successfully returned no devices", which is a device-level conclusion drawn
    from a response WITNESS could not actually read. That is the one mistake
    this product exists to avoid, so an unreadable envelope is a problem, not an
    empty result.
    """
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        if "data" not in payload:
            keys = ", ".join(sorted(str(k) for k in payload)) or "none"
            return (), (
                f"response carried no 'data' member (keys present: {keys}); "
                "this is not a JSON:API resource envelope"
            )
        data = payload["data"]
        if isinstance(data, list):
            return data, None
        if isinstance(data, dict):
            return [data], None
        if data is None:
            # JSON:API uses null for an absent to-one resource. Read it as an
            # explicit empty result, not as a malformed payload.
            return [], None
        return (), (
            f"'data' member was {type(data).__name__}, expected a list or object"
        )
    return (), f"response body was {type(payload).__name__}, expected a JSON:API envelope"
