from __future__ import annotations

from fastapi.testclient import TestClient

from consciousness.api import app, get_store
from consciousness.graph import validate_procedure
from consciousness.presets import built_in_access_presets, resolve_state_access
from consciousness.store import ConsciousnessStore


def test_bundled_presets_cover_coding_and_specialized_agent_types() -> None:
    presets = built_in_access_presets()
    by_id = {preset.id: preset for preset in presets}

    assert len(by_id) == len(presets)
    assert {
        "coding-agent",
        "coding-reviewer",
        "researcher",
        "browser-operator",
        "data-analyst",
        "memory-steward",
        "procedure-auditor",
    } <= set(by_id)
    coding = by_id["coding-agent"]
    assert coding.permissions.filesystem == "workspace_write"
    assert coding.permissions.shell == "workspace_write"
    assert coding.permissions.external_writes == "ask"
    assert coding.permissions.secrets == "deny"
    assert {"filesystem.read", "filesystem.write", "shell.run", "git.diff"} <= set(
        coding.tools
    )


def test_preset_resolution_is_deterministic_and_applies_explicit_overrides(
    tmp_path,
) -> None:
    store = ConsciousnessStore(tmp_path / "presets.db")
    store.setup()
    definition = store.current_version().definition.model_copy(deep=True)
    state = definition.states[0]
    state.access_preset_id = "coding-agent"
    state.access_overrides.add_tools = ["browser.screenshot", "filesystem.read"]
    state.access_overrides.remove_tools = ["filesystem.write"]
    state.access_overrides.add_skills = ["visual-qa"]

    first = resolve_state_access(definition, state)
    second = resolve_state_access(definition, state)

    assert first == second
    assert "browser.screenshot" in first.tools
    assert "filesystem.write" not in first.tools
    assert first.tools.count("filesystem.read") == 1
    assert "visual-qa" in first.skills
    assert first.preset_id == "coding-agent"


def test_validation_rejects_unknown_preset_reference(tmp_path) -> None:
    store = ConsciousnessStore(tmp_path / "invalid-preset.db")
    store.setup()
    definition = store.current_version().definition.model_copy(deep=True)
    definition.states[0].access_preset_id = "missing-profile"

    errors = validate_procedure(definition)

    assert any("unknown access preset" in error for error in errors)


def test_run_pins_resolved_access_snapshot(tmp_path) -> None:
    store = ConsciousnessStore(tmp_path / "run-access.db")
    store.setup()
    definition = store.current_version().definition
    state = definition.states[0]
    access = resolve_state_access(definition, state)
    run = store.begin_run(state, store.list_models()[0], agent_access=access)

    assert run.agent_access == access
    assert run.agent_access is not None
    assert run.agent_access.tools == state.tools
    assert run.agent_access.skills == state.skills


def test_access_catalog_reports_runtime_availability_and_preset_assignment(
    tmp_path,
) -> None:
    store = ConsciousnessStore(tmp_path / "api-presets.db")
    store.setup()
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        catalog = client.get("/api/v1/access/catalog")
        assert catalog.status_code == 200
        payload = catalog.json()
        assert "coding-agent" in {preset["id"] for preset in payload["presets"]}
        assert "artifact.write" in {tool["name"] for tool in payload["tools"]}
        assert "filesystem.write" in payload["unavailable_tools"]

        draft = client.post("/api/v1/procedure/drafts").json()
        assigned = client.put(
            f"/api/v1/procedure/drafts/{draft['id']}/states/gather/access",
            json={
                "revision": draft["revision"],
                "preset_id": "researcher",
                "overrides": {"remove_tools": ["web.open"]},
            },
        )
        assert assigned.status_code == 200
        state = next(
            item
            for item in assigned.json()["definition"]["states"]
            if item["id"] == "gather"
        )
        assert state["access_preset_id"] == "researcher"
        assert state["access_overrides"]["remove_tools"] == ["web.open"]
    finally:
        app.dependency_overrides.clear()
