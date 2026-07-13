from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .guardrails import default_guardrails
from .models import ModelProfile, ProcedureVersion
from .operations import redact
from .presets import built_in_access_presets
from .seed import STARTER_MODELS, STARTER_STATES
from .store import ConsciousnessStore


def backup_cli() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Create a consistent Consciousness SQLite backup.")
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = (args.destination or settings.database_path.with_name(f"consciousness-{stamp}.backup.db")).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    with store.connect() as source, sqlite3.connect(destination) as target:
        source.backup(target)
    print(destination)


def diagnostics_cli() -> None:
    settings = get_settings()
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    snapshot = store.snapshot()
    print(
        json.dumps(
            redact({
                "integrity": store.integrity_check(),
                "runtime": snapshot.runtime.model_dump(mode="json"),
                "active_version": snapshot.version.model_dump(mode="json", exclude={"definition"}),
                "recent_runs": [run.model_dump(mode="json") for run in snapshot.runs[:5]],
                "integrations": [item.model_dump(mode="json") for item in snapshot.integrations],
                "pending_approvals": [item.model_dump(mode="json") for item in snapshot.approvals if item.status == "pending"],
            }),
            indent=2,
            default=str,
        )
    )


def vacuum_cli() -> None:
    settings = get_settings()
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    with store.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")


def upgrade_bundled_profile(store: ConsciousnessStore) -> ProcedureVersion | None:
    """Version bundled memory-safety/profile changes into an existing database."""
    active = store.current_version()
    definition = active.definition.model_copy(deep=True)
    runtime = store.runtime()
    changes: list[dict[str, object]] = []
    existing_preset_ids = {preset.id for preset in definition.access_presets}
    added_presets = [preset for preset in built_in_access_presets() if preset.id not in existing_preset_ids]
    if added_presets:
        definition.access_presets.extend(added_presets)
        changes.append({"kind": "access-presets", "preset_ids": [preset.id for preset in added_presets]})
    starter_states = {str(item["id"]): item for item in STARTER_STATES}
    for state in definition.states:
        state.is_current = state.id == runtime.current_state_id
        if state.id in {"curate", "publish"}:
            tools = list(starter_states[state.id]["tools"])
            if state.tools != tools:
                changes.append({"kind": "state-tools", "state_id": state.id, "before": state.tools, "after": tools})
                state.tools = tools

    defaults = {item.state_id: item for item in default_guardrails().capability_policies}
    for index, policy in enumerate(definition.guardrails.capability_policies):
        if policy.state_id in {"curate", "publish"} and policy != defaults[policy.state_id]:
            changes.append({"kind": "capability-policy", "state_id": policy.state_id})
            definition.guardrails.capability_policies[index] = defaults[policy.state_id]

    legacy_local_ids = {
        "local/llama-3.1-8b-instruct",
        "local/qwen2.5-14b-instruct",
        "local/qwen3.5-9b",
    }
    legacy_models = [model.id for model in definition.models if model.id in legacy_local_ids]
    if legacy_models:
        definition.models = [model for model in definition.models if model.id not in legacy_local_ids]
        changes.append({"kind": "remove-legacy-model-profiles", "model_ids": legacy_models})
    local_model = ModelProfile.model_validate(STARTER_MODELS[0])
    if all(model.id != local_model.id for model in definition.models):
        definition.models.append(local_model)
        changes.append({"kind": "model-profile", "model_id": local_model.id})
    if not changes:
        return None

    draft = store.create_draft(active.id)
    updated = store.update_draft(draft.id, definition, expected_revision=draft.revision)
    errors = store.validate_version(updated.id)
    if errors:
        raise RuntimeError("bundled profile upgrade is invalid: " + "; ".join(errors))
    activated = store.activate_version(
        updated.id,
        rationale="upgrade bundled safety, agent access presets, and local Ollama profile",
    )
    store.add_recap(
        run_id=None,
        auditor_model_id="operator-maintenance",
        summary="Applied bundled safety, agent access presets, and Ornith profile to the persistent procedure.",
        decision="activate_version",
        procedure_changes=changes,
    )
    return activated


def upgrade_bundled_profile_cli() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Version bundled safety/profile changes into an existing database.")
    parser.add_argument("--apply", action="store_true", help="Required to activate a new auditable procedure version.")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required")
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    version = upgrade_bundled_profile(store)
    print(json.dumps({"status": "unchanged" if version is None else "activated", "version_id": version.id if version else store.current_version().id}))


def upgrade_second_run_profile(store: ConsciousnessStore) -> ProcedureVersion | None:
    """Activate the isolated Ornith routine loop plus MiniMax M3 audit supervisor."""
    active = store.current_version()
    definition = active.definition.model_copy(deep=True)
    starter_states = {str(item["id"]): item for item in STARTER_STATES}
    desired_models = {
        profile.id: profile
        for profile in (ModelProfile.model_validate(item) for item in STARTER_MODELS)
        if profile.id in {"local/ornith-1.0-9b-q4", "minimax/MiniMax-M3"}
    }
    changed: list[dict[str, object]] = []
    old_ids = {
        "local/qwen3.5-9b",
        "local/qwen2.5-14b-instruct",
        "local/llama-3.1-8b-instruct",
    }
    remaining = [model for model in definition.models if model.id not in old_ids | desired_models.keys()]
    next_models = remaining + list(desired_models.values())
    if definition.models != next_models:
        definition.models = next_models
        changed.append(
            {
                "kind": "second-run-models",
                "model_ids": sorted(desired_models),
                "removed_model_ids": sorted(old_ids),
            }
        )
    for state in definition.states:
        desired = starter_states.get(state.id)
        if not desired:
            continue
        before = {
            "context_minimum": state.context_minimum,
            "preferred_model_id": state.preferred_model_id,
            "allow_model_fallback": state.allow_model_fallback,
        }
        state.context_minimum = int(desired["context_minimum"])
        state.preferred_model_id = str(desired["preferred_model_id"])
        state.allow_model_fallback = bool(desired["allow_model_fallback"])
        after = {
            "context_minimum": state.context_minimum,
            "preferred_model_id": state.preferred_model_id,
            "allow_model_fallback": state.allow_model_fallback,
        }
        if before != after:
            changed.append({"kind": "state-model-selector", "state_id": state.id, **after})
    if not changed:
        return None
    draft = store.create_draft(active.id)
    updated = store.update_draft(draft.id, definition, expected_revision=draft.revision)
    errors = store.validate_version(updated.id)
    if errors:
        raise RuntimeError("second-run profile is invalid: " + "; ".join(errors))
    activated = store.activate_version(
        updated.id,
        rationale="pin Ornith 9B routine agents and MiniMax M3 audit supervisor for run two",
    )
    store.add_recap(
        run_id=None,
        auditor_model_id="operator-maintenance",
        summary="Activated the run-two Ornith routine profile and MiniMax M3 audit supervisor.",
        decision="activate_version",
        procedure_changes=changed,
    )
    return activated


def upgrade_second_run_profile_cli() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Activate the Ornith plus MiniMax run-two profile.")
    parser.add_argument("--apply", action="store_true", help="Required to activate the new version.")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required")
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    version = upgrade_second_run_profile(store)
    print(
        json.dumps(
            {
                "status": "unchanged" if version is None else "activated",
                "version_id": version.id if version else store.current_version().id,
            }
        )
    )
