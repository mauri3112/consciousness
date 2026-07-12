from __future__ import annotations

from fastapi.testclient import TestClient

from consciousness.api import _resolve_event_cursor, _stream_events, app, get_store
from consciousness.store import ConsciousnessStore


def test_versioned_api_supports_runtime_drafts_and_approvals(tmp_path):
    store = ConsciousnessStore(tmp_path / "api.db")
    store.setup()
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["execution_mode"] == "preview"

        snapshot = client.get("/api/v1/procedure")
        assert snapshot.status_code == 200
        assert snapshot.json()["version"]["status"] == "active"

        command = client.post("/api/v1/control/step")
        assert command.status_code == 202
        assert command.json()["status"] == "pending"

        interval = client.put("/api/v1/runtime/interval", json={"interval_seconds": 300})
        assert interval.status_code == 200
        assert interval.json()["interval_seconds"] == 300
        assert client.put("/api/v1/runtime/interval", json={"interval_seconds": 0}).status_code == 422

        draft = client.post("/api/v1/procedure/drafts").json()
        draft["definition"]["states"][0]["name"] = "Gather through API"
        saved = client.put(
            f"/api/v1/procedure/drafts/{draft['id']}",
            headers={"If-Match": f'"{draft["revision"]}"'},
            json={"revision": draft["revision"], "definition": draft["definition"]},
        )
        assert saved.status_code == 200
        assert saved.headers["etag"] == '"2"'
        assert client.post(f"/api/v1/procedure/drafts/{draft['id']}/validate").json() == {"valid": True, "errors": []}

        stale = client.post("/api/v1/procedure/drafts").json()
        fresh = client.post("/api/v1/procedure/drafts").json()
        assert client.post(f"/api/v1/procedure/drafts/{fresh['id']}/activate").status_code == 200
        conflict = client.post(f"/api/v1/procedure/drafts/{stale['id']}/activate")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "stale_procedure_parent"
    finally:
        app.dependency_overrides.clear()


def test_collection_cursors_are_stable_and_errors_have_one_envelope(tmp_path):
    store = ConsciousnessStore(tmp_path / "api-pagination.db")
    store.setup()
    for _ in range(3):
        run = store.begin_run(store.current_state(), store.list_models()[0])
        store.fail_run(run.id, "test", "fixture")
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        first = client.get("/api/v1/runs", params={"limit": 2})
        assert first.status_code == 200
        assert len(first.json()) == 2
        cursor = first.headers["x-next-cursor"]

        second = client.get("/api/v1/runs", params={"limit": 2, "cursor": cursor})
        assert second.status_code == 200
        assert len(second.json()) == 1
        assert {item["id"] for item in first.json()}.isdisjoint({item["id"] for item in second.json()})

        invalid = client.get("/api/v1/runs", params={"cursor": "not-a-cursor"})
        assert invalid.status_code == 400
        assert invalid.json()["error"] == {
            "code": "invalid_cursor",
            "message": "The cursor is invalid.",
            "details": {},
        }
        assert invalid.json()["detail"]["code"] == "invalid_cursor"

        missing = client.get("/api/v1/commands/999999")
        assert missing.status_code == 404
        assert missing.json()["error"] == {
            "code": "not_found",
            "message": "not found",
            "details": {"resource": "command"},
        }

        validation = client.get("/api/v1/runs", params={"limit": 0})
        assert validation.status_code == 422
        assert validation.json()["error"]["code"] == "validation_error"
    finally:
        app.dependency_overrides.clear()


def test_approval_cursor_and_sse_resume_from_last_event_id(tmp_path, monkeypatch):
    store = ConsciousnessStore(tmp_path / "api-events.db")
    store.setup()
    for index in range(3):
        store.request_approval(kind="tool_call", risk="external_write", proposed_action={"index": index})
    approvals, cursor = store.list_approvals_page(limit=2)
    remaining, final_cursor = store.list_approvals_page(limit=2, cursor=cursor)
    assert len(approvals) == 2
    assert len(remaining) == 1
    assert final_cursor is None

    first = store.add_event("test.first", {})
    second = store.add_event("test.second", {})
    assert _resolve_event_cursor(0, str(first.id)) == first.id
    assert _resolve_event_cursor(second.id, str(first.id)) == second.id

    monkeypatch.setattr("consciousness.api.time.sleep", lambda _seconds: None)
    stream = _stream_events(store, first.id)
    assert next(stream) == "retry: 1000\n\n"
    resumed = next(stream)
    assert f"id: {second.id}\n" in resumed
    assert "event: test.second\n" in resumed

    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        invalid = client.get("/api/v1/events", headers={"Last-Event-ID": "bad"})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_event_cursor"
    finally:
        app.dependency_overrides.clear()


def test_model_health_route_accepts_model_ids_with_slashes(tmp_path, monkeypatch):
    store = ConsciousnessStore(tmp_path / "api-model-health.db")
    store.setup()

    class HealthyProvider:
        def health(self):
            return {"status": "healthy", "model": "qwen3.5:9b"}

    monkeypatch.setattr("consciousness.api.build_provider", lambda *_args, **_kwargs: HealthyProvider())
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        response = client.post("/api/v1/models/local/qwen3.5-9b/test")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "model": "qwen3.5:9b"}
    finally:
        app.dependency_overrides.clear()
