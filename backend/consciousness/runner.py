from __future__ import annotations

import argparse
import time
from pathlib import Path

from .config import get_settings
from .llm import choose_model
from .models import AuditorRecap, IntegrationStatus, RunStatus, TickResult
from .only_memories import OnlyMemoriesClient
from .store import ConsciousnessStore, utcnow


def run_once(database_path: Path | None = None) -> TickResult:
    settings = get_settings()
    store = ConsciousnessStore(database_path or settings.database_path)
    store.setup()

    _check_only_memories(store, settings.only_memories_url)

    state = store.current_state()
    model = choose_model(state, store.list_models())
    run = store.begin_run(state, model)

    context_used = _estimate_context_use(state.context_minimum, model.context_window)
    final_thoughts = (
        f"{state.name} completed a scaffolded pass. "
        f"Recorded state output for downstream agents and preserved context budget at "
        f"{context_used}/{model.context_window} tokens."
    )
    changes = [
        {
            "kind": "state-output",
            "state_id": state.id,
            "visible_to_next_agent": True,
            "summary": state.output_contract,
        }
    ]
    finished = store.finish_run(
        run.id,
        status=RunStatus.succeeded,
        context_used=context_used,
        final_thoughts=final_thoughts,
        changes=changes,
    )

    recap = _maybe_add_recap(store, state.id, finished, model.id)
    transition = store.next_transition(state.id)
    next_state = store.set_current_state(transition.target_id)

    if settings.only_memories_write_recaps and settings.only_memories_url:
        _write_recap_to_only_memories(settings.only_memories_url, finished, state.name, store)

    return TickResult(run=finished, previous_state=state, next_state=next_state, recap=recap)


def run_loop(database_path: Path | None = None, interval_seconds: int | None = None) -> None:
    settings = get_settings()
    interval = interval_seconds or settings.loop_interval_seconds
    while True:
        result = run_once(database_path)
        print(
            f"{result.run.id}: {result.previous_state.id} -> {result.next_state.id} "
            f"using {result.run.model_id}"
        )
        time.sleep(interval)


def run_once_cli() -> None:
    parser = argparse.ArgumentParser(description="Advance the consciousness loop once.")
    parser.add_argument("--db", type=Path, default=None, help="SQLite database path.")
    args = parser.parse_args()
    result = run_once(args.db)
    print(result.model_dump_json(indent=2))


def run_loop_cli() -> None:
    parser = argparse.ArgumentParser(description="Run the consciousness loop forever.")
    parser.add_argument("--db", type=Path, default=None, help="SQLite database path.")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between ticks.")
    args = parser.parse_args()
    run_loop(args.db, args.interval)


def _estimate_context_use(minimum: int, context_window: int) -> int:
    target = max(int(minimum * 0.72), 4_096)
    return min(target, int(context_window * 0.82))


def _maybe_add_recap(store: ConsciousnessStore, state_id: str, run, model_id: str) -> AuditorRecap | None:
    if state_id != "audit":
        return store.add_recap(
            run_id=run.id,
            auditor_model_id="loop-recorder",
            summary=f"{state_id} run succeeded and exposed its state output to the next agent.",
            decision="continue",
            procedure_changes=[],
        )
    return store.add_recap(
        run_id=run.id,
        auditor_model_id=model_id,
        summary="Auditor pass found the starter procedure coherent. No mutation applied in scaffold mode.",
        decision="continue_without_mutation",
        procedure_changes=[],
    )


def _check_only_memories(store: ConsciousnessStore, base_url: str | None) -> None:
    if not base_url:
        return
    client = OnlyMemoriesClient(base_url)
    try:
        health = client.health()
        store.upsert_integration(
            IntegrationStatus(
                name="only-memories",
                status="healthy",
                endpoint=base_url,
                last_checked_at=utcnow(),
                details=health,
            )
        )
    except Exception as exc:  # pragma: no cover - depends on local sibling service
        store.upsert_integration(
            IntegrationStatus(
                name="only-memories",
                status="unreachable",
                endpoint=base_url,
                last_checked_at=utcnow(),
                details={"error": str(exc)},
            )
        )


def _write_recap_to_only_memories(
    base_url: str,
    run,
    state_name: str,
    store: ConsciousnessStore,
) -> None:
    client = OnlyMemoriesClient(base_url)
    try:
        memory = client.remember_run_recap(run, state_name)
        store.upsert_integration(
            IntegrationStatus(
                name="only-memories",
                status="wrote_recap",
                endpoint=base_url,
                last_checked_at=utcnow(),
                details={"memory_id": memory.get("id")},
            )
        )
    except Exception as exc:  # pragma: no cover - depends on local sibling service
        store.upsert_integration(
            IntegrationStatus(
                name="only-memories",
                status="write_failed",
                endpoint=base_url,
                last_checked_at=utcnow(),
                details={"error": str(exc)},
            )
        )
