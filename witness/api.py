"""WITNESS HTTP API.

The API is a thin read model over ``WitnessService``. It performs no
reconciliation of its own: every status returned here came out of the
deterministic engine, and every response that carries a conclusion also carries
the rule id and the evidence references that produced it.

Endpoints
    GET  /api/health                  liveness, no state
    GET  /api/overview                what we can inspect and what we know
    GET  /api/sources                 registered sources and their capabilities
    POST /api/observe/{source_id}     run one read-only observation pass
    GET  /api/reconcile/{source_id}   current reconciled state
    GET  /api/timeline/{source_id}    chronological observations
    GET  /api/devices/{source_id}     device views
    GET  /api/ledger                  the evidence ledger
    GET  /api/ledger/verify           verify the chain
    GET  /api/ledger/export           download the ledger as JSON
    POST /api/simulated/reset         reset the scripted source
    POST /api/simulated/advance       step the scripted source forward
    GET  /api/status-spec             the documented status taxonomy
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapters.ring import RingAdapter
from .adapters.simulated import SimulatedAdapter
from .engine.status import STATUS_SPEC
from .service import WitnessService
from .store import Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def build_service(db_path: str | None = None) -> WitnessService:
    """Wire the default service: a live Ring source and a simulated source."""
    store = Store(path=db_path or os.environ.get("WITNESS_DB", "witness.db"))
    service = WitnessService(store)
    service.register(RingAdapter())
    service.register(SimulatedAdapter())
    return service


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="WITNESS",
        description=(
            "An evidence-based visibility layer for connected-home systems. "
            "WITNESS reports what a source actually said, what can be established "
            "from it, and what remains unknown."
        ),
        version="1.0.0",
    )
    service = build_service(db_path)
    app.state.service = service

    # ---------------------------------------------------------------- health

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "witness", "version": "1.0.0"}

    # -------------------------------------------------------------- overview

    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        return service.overview()

    @app.get("/api/sources")
    async def sources() -> dict[str, Any]:
        return {"sources": service.list_sources()}

    @app.get("/api/status-spec")
    async def status_spec() -> dict[str, Any]:
        """The full documented taxonomy. The UI renders this rather than hardcoding."""
        return {
            "statuses": [
                {
                    "status": spec.status.value,
                    "label": spec.label,
                    "definition": spec.definition,
                    "trigger": spec.trigger,
                    "required_evidence": list(spec.required_evidence),
                    "does_not_establish": list(spec.forbidden_conclusions),
                    "tone": spec.tone,
                }
                for spec in STATUS_SPEC.values()
            ]
        }

    # --------------------------------------------------------------- observe

    @app.post("/api/observe/{source_id}")
    async def observe(source_id: str) -> dict[str, Any]:
        try:
            result = await service.observe(source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source: {source_id}") from None
        return result.to_dict()

    # ------------------------------------------------------------- reconcile

    @app.get("/api/reconcile/{source_id}")
    async def reconcile_source(source_id: str) -> dict[str, Any]:
        if service.get(source_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
        return service.reconciliation_for(source_id).to_dict()

    @app.get("/api/devices/{source_id}")
    async def devices(source_id: str) -> dict[str, Any]:
        if service.get(source_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
        recon = service.reconciliation_for(source_id)
        return {
            "source_id": source_id,
            "source_kind": recon.source_kind,
            "generated_at": recon.to_dict()["generated_at"],
            "staleness": recon.staleness,
            "devices": [v.to_dict() for v in recon.devices],
            "limitations": [l.to_dict() for l in recon.limitations],
        }

    @app.get("/api/timeline/{source_id}")
    async def timeline(source_id: str, limit: int = 100) -> dict[str, Any]:
        if service.get(source_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
        return {"source_id": source_id, "entries": service.timeline(source_id, limit)}

    # ---------------------------------------------------------------- ledger

    @app.get("/api/ledger")
    async def ledger(limit: int = 200, offset: int = 0) -> dict[str, Any]:
        chain = service.ledger()
        records = chain.to_list()
        window = records[offset : offset + limit]
        return {
            "record_count": len(records),
            "head_hash": chain.head_hash,
            "offset": offset,
            "limit": limit,
            "records": window,
        }

    @app.get("/api/ledger/verify")
    async def ledger_verify() -> dict[str, Any]:
        return service.ledger().verify().to_dict()

    @app.get("/api/ledger/export")
    async def ledger_export() -> JSONResponse:
        chain = service.ledger()
        return JSONResponse(
            content={
                "head_hash": chain.head_hash,
                "record_count": len(chain),
                "verification": chain.verify().to_dict(),
                "records": chain.to_list(),
            },
            headers={"Content-Disposition": 'attachment; filename="witness-evidence-ledger.json"'},
        )

    # ------------------------------------------------------------- simulated

    @app.post("/api/simulated/reset")
    async def simulated_reset() -> dict[str, Any]:
        adapter = service.get("ring-simulated")
        if not isinstance(adapter, SimulatedAdapter):
            raise HTTPException(status_code=404, detail="simulated source not registered")
        adapter.reset()
        return {"cursor": adapter.cursor, "frames": adapter.frame_count, "source_kind": "simulated"}

    @app.post("/api/simulated/advance")
    async def simulated_advance() -> dict[str, Any]:
        adapter = service.get("ring-simulated")
        if not isinstance(adapter, SimulatedAdapter):
            raise HTTPException(status_code=404, detail="simulated source not registered")
        moved = adapter.advance()
        frame = adapter.current_frame()
        return {
            "moved": moved,
            "cursor": adapter.cursor,
            "frames": adapter.frame_count,
            "source_kind": "simulated",
            "frame_note": frame.note if frame else None,
        }

    @app.get("/api/simulated/state")
    async def simulated_state() -> dict[str, Any]:
        adapter = service.get("ring-simulated")
        if not isinstance(adapter, SimulatedAdapter):
            raise HTTPException(status_code=404, detail="simulated source not registered")
        frame = adapter.current_frame()
        return {
            "cursor": adapter.cursor,
            "frames": adapter.frame_count,
            "frame_note": frame.note if frame else None,
            "source_kind": "simulated",
        }

    # ------------------------------------------------------------------ ring

    @app.get("/api/ring/connection")
    async def ring_connection() -> dict[str, Any]:
        """Report the live source's configuration state without revealing the token."""
        adapter = service.get("ring")
        if not isinstance(adapter, RingAdapter):
            raise HTTPException(status_code=404, detail="ring source not registered")
        return {
            "configured": adapter.configured,
            "token_fingerprint": adapter.token_fingerprint(),
            "base_url": adapter._base_url,
            "capabilities": adapter.capabilities(),
            "verification_note": (
                "A live authenticated call has not been performed by WITNESS in this "
                "environment. Set RING_ACCESS_TOKEN to a token from the Ring Developer "
                "Playground to exercise the live path."
            ),
        }

    # ------------------------------------------------------------------- web

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

    return app


app = create_app()
