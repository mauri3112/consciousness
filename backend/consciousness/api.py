from __future__ import annotations

import logging
import time
import uuid
from typing import Annotated

import uvicorn
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from .artifacts import ArtifactStore
from .config import get_settings
from .models import (
    ApprovalRecord,
    ArtifactRecord,
    CommandKind,
    IntegrationStatus,
    ModelProfile,
    ProcedureDefinition,
    ProcedureSnapshot,
    ProcedureVersion,
    RunEvent,
    RunRecord,
    RuntimeCommand,
    RuntimeState,
    ToolCallRecord,
)
from .only_memories import OnlyMemoriesClient
from .operations import configure_structured_logging
from .providers import ProviderError, build_provider
from .store import ConsciousnessStore, utcnow


settings = get_settings()
app = FastAPI(title="Consciousness", version="1.0.0")
logger = logging.getLogger("consciousness.api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "Authorization", "If-Match"],
    expose_headers=["ETag", "X-Next-Cursor"],
)


def _error_response(status_code: int, detail: object) -> JSONResponse:
    if isinstance(detail, dict):
        code = str(detail.get("code", "request_error"))
        message = str(detail.get("message") or code.replace("_", " "))
        details = {key: value for key, value in detail.items() if key not in {"code", "message"}}
    else:
        code = "request_error"
        message = str(detail)
        details = {}
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}, "detail": detail},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    response = _error_response(exc.status_code, exc.detail)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed.",
                "details": {"errors": errors},
            },
            "detail": errors,
        },
    )


class TokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.api_token and request.url.path not in {"/api/v1/health", "/api/v1/ready", "/health"}:
            if request.headers.get("authorization") != f"Bearer {settings.api_token}":
                return _error_response(401, {"code": "invalid_token", "message": "Invalid API token."})
        return await call_next(request)


app.add_middleware(TokenMiddleware)


@app.middleware("http")
async def structured_access_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request failed",
            extra={"fields": {"request_id": request_id, "method": request.method, "path": request.url.path}},
        )
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request complete",
        extra={
            "fields": {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            }
        },
    )
    return response


class DraftUpdate(BaseModel):
    definition: ProcedureDefinition
    revision: int


class ApprovalDecision(BaseModel):
    approved: bool
    note: str | None = None


class DraftModelUpdate(BaseModel):
    revision: int
    profile: ModelProfile


class ToolCallReconciliation(BaseModel):
    applied: bool
    result: dict[str, object] = Field(default_factory=dict)


def get_store() -> ConsciousnessStore:
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    return store


@app.get("/api/v1/health")
def health(store: ConsciousnessStore = Depends(get_store)) -> dict[str, object]:
    runtime = store.runtime()
    return {
        "status": "ok",
        "current_state": runtime.current_state_id,
        "runtime_status": runtime.status,
        "execution_mode": runtime.execution_mode,
    }


@app.get("/api/v1/ready")
def ready(store: ConsciousnessStore = Depends(get_store)) -> dict[str, object]:
    integrity = store.integrity_check()
    if integrity != "ok":
        raise HTTPException(status_code=503, detail={"code": "database_integrity", "message": integrity})
    return {"status": "ready", "database": integrity, "active_version": store.runtime().active_version_id}


@app.get("/api/v1/procedure", response_model=ProcedureSnapshot)
def procedure(store: ConsciousnessStore = Depends(get_store)) -> ProcedureSnapshot:
    return store.snapshot()


@app.get("/api/v1/runtime", response_model=RuntimeState)
def runtime(store: ConsciousnessStore = Depends(get_store)) -> RuntimeState:
    return store.runtime()


@app.post("/api/v1/control/{kind}", response_model=RuntimeCommand, status_code=202)
def control(kind: CommandKind, store: ConsciousnessStore = Depends(get_store)) -> RuntimeCommand:
    return store.enqueue_command(kind)


@app.get("/api/v1/commands/{command_id}", response_model=RuntimeCommand)
def command(command_id: int, store: ConsciousnessStore = Depends(get_store)) -> RuntimeCommand:
    try:
        return store.get_command(command_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "command"}) from exc


@app.get("/api/v1/procedure/versions", response_model=list[ProcedureVersion])
def versions(store: ConsciousnessStore = Depends(get_store)) -> list[ProcedureVersion]:
    return store.list_versions()


@app.get("/api/v1/procedure/versions/{version_id}", response_model=ProcedureVersion)
def version(version_id: str, response: Response, store: ConsciousnessStore = Depends(get_store)) -> ProcedureVersion:
    try:
        value = store.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc
    response.headers["ETag"] = f'"{value.revision}"'
    return value


@app.post("/api/v1/procedure/drafts", response_model=ProcedureVersion, status_code=201)
def create_draft(store: ConsciousnessStore = Depends(get_store)) -> ProcedureVersion:
    return store.create_draft()


@app.put("/api/v1/procedure/drafts/{version_id}", response_model=ProcedureVersion)
def update_draft(
    version_id: str,
    payload: DraftUpdate,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    store: ConsciousnessStore = Depends(get_store),
) -> ProcedureVersion:
    expected = payload.revision
    if if_match:
        try:
            expected = int(if_match.strip('"'))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_etag"}) from exc
    try:
        value = store.update_draft(version_id, payload.definition, expected_revision=expected)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "revision_conflict"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "not_a_draft", "message": str(exc)}) from exc
    response.headers["ETag"] = f'"{value.revision}"'
    return value


@app.post("/api/v1/procedure/drafts/{version_id}/validate")
def validate_draft(version_id: str, store: ConsciousnessStore = Depends(get_store)) -> dict[str, object]:
    try:
        errors = store.validate_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc
    return {"valid": not errors, "errors": errors}


@app.post("/api/v1/procedure/drafts/{version_id}/activate", response_model=ProcedureVersion)
def activate_draft(version_id: str, store: ConsciousnessStore = Depends(get_store)) -> ProcedureVersion:
    try:
        return store.activate_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc
    except RuntimeError as exc:
        if str(exc) == "stale_procedure_parent":
            raise HTTPException(
                status_code=409,
                detail={"code": "stale_procedure_parent", "message": "The active procedure changed. Rebase this draft before activation."},
            ) from exc
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_procedure", "message": str(exc)}) from exc


@app.get("/api/v1/procedure/diff")
def procedure_diff(base: str, target: str, store: ConsciousnessStore = Depends(get_store)) -> dict[str, str]:
    try:
        return {"base": base, "target": target, "diff": store.diff_versions(base, target)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc


@app.post("/api/v1/procedure/versions/{version_id}/rollback", response_model=ProcedureVersion)
def rollback(version_id: str, store: ConsciousnessStore = Depends(get_store)) -> ProcedureVersion:
    try:
        return store.rollback(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_rollback", "message": str(exc)}) from exc


@app.get("/api/v1/procedure/export", response_model=ProcedureDefinition)
def export_procedure(store: ConsciousnessStore = Depends(get_store)) -> ProcedureDefinition:
    return store.current_version().definition


@app.post("/api/v1/procedure/import", response_model=ProcedureVersion, status_code=201)
def import_procedure(definition: ProcedureDefinition = Body(), store: ConsciousnessStore = Depends(get_store)) -> ProcedureVersion:
    draft = store.create_draft()
    try:
        return store.update_draft(draft.id, definition, expected_revision=draft.revision)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_import", "message": str(exc)}) from exc


@app.get("/api/v1/runs", response_model=list[RunRecord])
def runs(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    state_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    store: ConsciousnessStore = Depends(get_store),
) -> list[RunRecord]:
    try:
        items, next_cursor = store.list_runs_page(limit=limit, state_id=state_id, status=status, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "The cursor is invalid."}) from exc
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return items


@app.get("/api/v1/runs/{run_id}", response_model=RunRecord)
def run_detail(run_id: str, store: ConsciousnessStore = Depends(get_store)) -> RunRecord:
    try:
        return store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "run"}) from exc


@app.get("/api/v1/runs/{run_id}/events", response_model=list[RunEvent])
def run_events(
    run_id: str,
    response: Response,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    store: ConsciousnessStore = Depends(get_store),
) -> list[RunEvent]:
    items = store.list_events(after_id=after_id, limit=limit, run_id=run_id)
    if len(items) == limit:
        response.headers["X-Next-Cursor"] = str(items[-1].id)
    return items


@app.get("/api/v1/runs/{run_id}/tools", response_model=list[ToolCallRecord])
def run_tools(run_id: str, store: ConsciousnessStore = Depends(get_store)) -> list[ToolCallRecord]:
    return store.list_tool_calls(run_id)


@app.post("/api/v1/tool-calls/{call_id}/reconcile", response_model=ToolCallRecord)
def reconcile_tool_call(
    call_id: str,
    payload: ToolCallReconciliation,
    store: ConsciousnessStore = Depends(get_store),
) -> ToolCallRecord:
    try:
        return store.reconcile_tool_call(call_id, applied=payload.applied, result=payload.result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "tool_call"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "tool_call_not_uncertain", "message": str(exc)}) from exc


@app.get("/api/v1/runs/{run_id}/artifacts", response_model=list[ArtifactRecord])
def run_artifacts(run_id: str, store: ConsciousnessStore = Depends(get_store)) -> list[ArtifactRecord]:
    return store.list_artifacts(run_id)


@app.get("/api/v1/artifacts/{run_id}/{filename}")
def artifact(run_id: str, filename: str, store: ConsciousnessStore = Depends(get_store)) -> FileResponse:
    try:
        path = ArtifactStore(settings.artifact_root, store).resolve(run_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "artifact"}) from exc
    return FileResponse(path)


@app.get("/api/v1/approvals", response_model=list[ApprovalRecord])
def approvals(
    response: Response,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    store: ConsciousnessStore = Depends(get_store),
) -> list[ApprovalRecord]:
    try:
        items, next_cursor = store.list_approvals_page(limit=limit, status=status, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "The cursor is invalid."}) from exc
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return items


@app.get("/api/v1/approvals/{approval_id}", response_model=ApprovalRecord)
def approval(approval_id: str, store: ConsciousnessStore = Depends(get_store)) -> ApprovalRecord:
    try:
        return store.get_approval(approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "approval"}) from exc


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=ApprovalRecord)
def approval_decision(approval_id: str, payload: ApprovalDecision, store: ConsciousnessStore = Depends(get_store)) -> ApprovalRecord:
    try:
        return store.decide_approval(approval_id, payload.approved, payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "approval"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "already_decided", "message": str(exc)}) from exc


@app.get("/api/v1/mutations")
def mutations(store: ConsciousnessStore = Depends(get_store)):
    return store.list_mutations()


@app.get("/api/v1/models")
def models(store: ConsciousnessStore = Depends(get_store)):
    return store.list_models()


@app.put("/api/v1/procedure/drafts/{version_id}/models/{model_id}", response_model=ProcedureVersion)
def update_draft_model(
    version_id: str,
    model_id: str,
    payload: DraftModelUpdate,
    store: ConsciousnessStore = Depends(get_store),
) -> ProcedureVersion:
    try:
        draft = store.get_version(version_id)
        definition = draft.definition.model_copy(deep=True)
        index = next(index for index, model in enumerate(definition.models) if model.id == model_id)
        definition.models[index] = payload.profile
        return store.update_draft(version_id, definition, expected_revision=payload.revision)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "model"}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "procedure_version"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "revision_conflict"}) from exc


@app.post("/api/v1/models/{model_id:path}/test")
def test_model(model_id: str, store: ConsciousnessStore = Depends(get_store)):
    try:
        model = next(item for item in store.list_models() if item.id == model_id)
        provider = build_provider(
            model,
            execution_mode="live",
            openai_api_key=settings.openai_api_key,
            ollama_url=settings.ollama_url,
        )
        return provider.health()
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "resource": "model"}) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.category, "message": str(exc)}) from exc


@app.get("/api/v1/metrics")
def metrics(store: ConsciousnessStore = Depends(get_store)):
    snapshot = store.snapshot()
    return {
        "runs_total": len(snapshot.runs),
        "runs_failed": sum(run.status in {"failed", "interrupted"} for run in snapshot.runs),
        "pending_approvals": sum(item.status == "pending" for item in snapshot.approvals),
        "recorded_cost": sum(run.cost for run in snapshot.runs),
        "runtime_status": snapshot.runtime.status,
        "worker_attached": bool(snapshot.runtime.worker_id),
    }


@app.get("/api/v1/integrations")
def integrations(store: ConsciousnessStore = Depends(get_store)):
    return store.list_integrations()


@app.post("/api/v1/integrations/only-memories/test")
def test_only_memories(store: ConsciousnessStore = Depends(get_store)):
    if not settings.only_memories_url:
        raise HTTPException(status_code=422, detail={"code": "integration_disabled"})
    try:
        details = OnlyMemoriesClient(settings.only_memories_url).health()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "integration_unreachable", "message": str(exc)}) from exc
    status = IntegrationStatus(name="only-memories", status="healthy", endpoint=settings.only_memories_url, last_checked_at=utcnow(), details=details)
    store.upsert_integration(status)
    return status


def _resolve_event_cursor(after_id: int, last_event_id: str | None) -> int:
    if not last_event_id:
        return after_id
    try:
        return max(after_id, int(last_event_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_event_cursor", "message": "Last-Event-ID must be an integer."},
        ) from exc


def _stream_events(store: ConsciousnessStore, after_id: int):
    cursor = after_id
    yield "retry: 1000\n\n"
    while True:
        batch = store.list_events(after_id=cursor, limit=100)
        if not batch:
            yield ": keepalive\n\n"
        for event in batch:
            cursor = event.id
            yield f"id: {event.id}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n"
        time.sleep(1)


@app.get("/api/v1/events")
def events(
    after_id: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    store: ConsciousnessStore = Depends(get_store),
) -> StreamingResponse:
    initial_cursor = _resolve_event_cursor(after_id, last_event_id)

    return StreamingResponse(
        _stream_events(store, initial_cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Compatibility aliases retained for scaffold clients.
@app.get("/health")
def legacy_health(store: ConsciousnessStore = Depends(get_store)):
    return health(store)


@app.get("/procedure", response_model=ProcedureSnapshot)
def legacy_procedure(store: ConsciousnessStore = Depends(get_store)):
    return procedure(store)


@app.post("/tick", response_model=RuntimeCommand, status_code=202)
def legacy_tick(store: ConsciousnessStore = Depends(get_store)):
    return store.enqueue_command(CommandKind.step)


def run() -> None:
    settings.validate_bind()
    configure_structured_logging()
    uvicorn.run("consciousness.api:app", host=settings.api_host, port=settings.api_port, reload=False, log_config=None)
