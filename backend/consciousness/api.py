from __future__ import annotations

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import ProcedureSnapshot, TickResult
from .runner import run_once
from .store import ConsciousnessStore

app = FastAPI(title="Consciousness", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_store() -> ConsciousnessStore:
    settings = get_settings()
    store = ConsciousnessStore(settings.database_path)
    store.setup()
    return store


@app.get("/health")
def health(store: ConsciousnessStore = Depends(get_store)) -> dict[str, str]:
    current = store.current_state()
    return {"status": "ok", "current_state": current.id}


@app.get("/procedure", response_model=ProcedureSnapshot)
def procedure(store: ConsciousnessStore = Depends(get_store)) -> ProcedureSnapshot:
    return store.snapshot()


@app.post("/tick", response_model=TickResult)
def tick() -> TickResult:
    return run_once()


@app.post("/procedure/current/{state_id}", response_model=ProcedureSnapshot)
def set_current_state(state_id: str, store: ConsciousnessStore = Depends(get_store)) -> ProcedureSnapshot:
    try:
        store.set_current_state(state_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown state: {state_id}") from exc
    return store.snapshot()


def run() -> None:
    settings = get_settings()
    uvicorn.run("consciousness.api:app", host=settings.api_host, port=settings.api_port, reload=False)
