from __future__ import annotations

import argparse
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import Settings, get_settings
from .credentials import CredentialStore
from .context import assemble_prompt, build_context_manifest
from .llm import choose_model
from .models import (
    ArtifactPointer,
    AuditDecision,
    AuditorRecap,
    CapabilityPolicy,
    CommandKind,
    IntegrationStatus,
    MemoryChange,
    MemoryChangeProposal,
    PublishReceipt,
    RunStatus,
    RuntimeStatus,
    SourceLink,
    SynthesisArtifact,
    TickResult,
    ValidationReport,
)
from .only_memories import OnlyMemoriesClient
from .operations import configure_structured_logging
from .providers import ProviderError, ProviderRequest, ProviderTool, build_provider
from .procedure_patch import apply_procedure_patch
from .presets import apply_resolved_access, resolve_state_access, resolved_policy
from .store import ConsciousnessStore, utcnow
from .tools import ToolRegistry, build_tool_registry


logger = logging.getLogger("consciousness.worker")


class _LeaseHeartbeat:
    def __init__(self, store: ConsciousnessStore, worker_id: str, lease_seconds: int) -> None:
        self.store = store
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.run_id: str | None = None
        self._lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-heartbeat-{worker_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.lease_seconds / 2))

    def assert_owned(self) -> None:
        if self._lost.is_set():
            raise RuntimeError("worker execution lease was lost")

    def _run(self) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                if not self.store.renew_lease(self.worker_id, self.lease_seconds):
                    self._lost.set()
                    return
                if self.run_id:
                    self.store.heartbeat_run(self.run_id)
            except Exception:
                self._lost.set()
                return


class _ProviderToolExecutor:
    def __init__(self, registry: ToolRegistry, *, run_id: str, policy: CapabilityPolicy) -> None:
        self.registry = registry
        self.run_id = run_id
        self.policy = policy
        self._call_index = 0

    def reset(self) -> None:
        """Replay a provider attempt with the same durable idempotency keys."""
        self._call_index = 0

    def __call__(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._call_index += 1
        durable_arguments = dict(arguments)
        if tool_name == "artifact.write":
            durable_arguments["run_id"] = self.run_id
        execution = self.registry.execute(
            run_id=self.run_id,
            tool_name=tool_name,
            arguments=durable_arguments,
            policy=self.policy,
            step_key=f"provider-tool-{self._call_index:04d}",
        )
        return {
            "status": execution.status,
            "result": execution.result,
            "approval_id": execution.approval_id,
        }


def run_once(database_path: Path | None = None, *, worker_id: str | None = None, hold_lease: bool = False) -> TickResult:
    settings = get_settings()
    store = ConsciousnessStore(database_path or settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    owner = worker_id or f"tick-{uuid.uuid4().hex[:8]}"
    if not store.acquire_lease(owner, settings.worker_lease_seconds):
        raise RuntimeError("another worker owns the active execution lease")
    heartbeat = _LeaseHeartbeat(store, owner, settings.worker_lease_seconds)
    heartbeat.start()

    try:
        store.recover_stale_work(settings.worker_lease_seconds * 2)
        only_memories = OnlyMemoriesClient(settings.only_memories_url) if settings.only_memories_url else None
        _check_only_memories(store, only_memories, settings.only_memories_url)
        runtime = store.runtime()
        if runtime.backoff_until and runtime.backoff_until > utcnow():
            raise RuntimeError(f"execution backoff remains active until {runtime.backoff_until.isoformat()}")
        definition = store.current_version().definition
        state = store.current_state()
        access = resolve_state_access(definition, state)
        state = apply_resolved_access(state, access)
        local_only = (
            runtime.status == RuntimeStatus.degraded
            or store.daily_spend() >= runtime.daily_budget_cap
        ) and state.kind != "audit"
        model = choose_model(
            state,
            store.list_models(),
            daily_spend=store.daily_spend(),
            daily_budget_cap=runtime.daily_budget_cap,
            local_only=local_only,
        )
        previous_runs = store.list_runs(limit=50)
        previous = next((run for run in previous_runs if run.status == RunStatus.succeeded), None)
        try:
            manifest = build_context_manifest(
                state,
                only_memories=only_memories,
                previous_runs=previous_runs,
                memory_space_id=settings.only_memories_space_id,
            )
        except Exception as exc:
            if state.kind != "gather" or only_memories is None:
                raise
            risk = f"only-memories search failed; Gather proceeded without memory evidence: {exc}"
            store.upsert_integration(
                IntegrationStatus(
                    name="only-memories",
                    status="degraded",
                    endpoint=settings.only_memories_url,
                    last_checked_at=utcnow(),
                    details={"operation": "search", "error": str(exc), "unresolved_risk": risk},
                )
            )
            store.add_event("integration.degraded", {"name": "only-memories", "operation": "search", "error": str(exc)})
            manifest = build_context_manifest(
                state,
                only_memories=None,
                previous_runs=previous_runs,
                memory_space_id=settings.only_memories_space_id,
            )
            manifest.unresolved_risks.append(risk)
        instructions, input_text = assemble_prompt(state, manifest, previous)
        run = store.begin_run(state, model, manifest=manifest, agent_access=access)
        heartbeat.run_id = run.id
        logger.info(
            "run started",
            extra={"fields": {"run_id": run.id, "state_id": state.id, "model_id": model.id, "worker_id": owner}},
        )
        store.add_event("provider.requested", {"provider": model.provider, "model": model.model}, run_id=run.id)

        provider = build_provider(
            model,
            execution_mode=settings.execution_mode,
            openai_api_key=settings.openai_api_key,
            ollama_url=settings.ollama_url,
            credential_resolver=CredentialStore(
                settings.credential_store_path, settings.credential_encryption_key
            ).get,
        )
        artifacts = ArtifactStore(settings.artifact_root, store)
        registry = build_tool_registry(store, only_memories=only_memories, artifacts=artifacts)
        policy = resolved_policy(access)
        allowed_state_tools = set(state.tools)
        provider_tools = [
            ProviderTool(tool.name, tool.description, tool.input_schema)
            for tool in registry.definitions_for(policy)
            if tool.name in allowed_state_tools
        ]
        tool_executor = _ProviderToolExecutor(registry, run_id=run.id, policy=policy)
        request = ProviderRequest(
            state=state,
            model=model,
            context=manifest,
            previous_output=previous.output if previous else None,
            instructions=instructions,
            input_text=input_text,
            tools=provider_tools,
            execute_tool=tool_executor if provider_tools else None,
        )
        tool_executor.reset()
        try:
            result = provider.execute(request)
        except ProviderError as exc:
            if exc.retryable:
                store.add_event("provider.retry", {"category": exc.category}, run_id=run.id)
                time.sleep(0.25)
                tool_executor.reset()
                result = provider.execute(request)
            else:
                raise
        heartbeat.assert_owned()

        store.add_event("output.validated", {"payload_kind": result.output.payload.kind if result.output.payload else None}, run_id=run.id)
        changed_resources: list[ArtifactPointer] = list(result.output.changed_resources)
        changes: list[dict[str, object]] = []

        if isinstance(result.output.payload, SynthesisArtifact):
            pointer = artifacts.write_text(
                run.id,
                "synthesis.md",
                f"# {result.output.payload.title}\n\n{result.output.payload.body}\n",
                label=result.output.payload.title,
            )
            changed_resources.append(pointer)
            changes.append({"kind": "artifact", "uri": pointer.uri})

        if state.kind == "publish":
            receipt = _publish_validated_changes(store, registry, run.id, definition.guardrails.capability_policies)
            result.output.payload = receipt
            changes.extend({"kind": "memory-write", "id": item} for item in receipt.applied)

        if isinstance(result.output.payload, AuditDecision) and result.output.payload.decision == "propose_mutation":
            approval_id = _stage_audit_mutation(store, run.id, result.output.payload)
            if approval_id:
                changes.append({"kind": "procedure-mutation-proposal", "approval_id": approval_id})

        result.output.changed_resources = changed_resources or [
            ArtifactPointer(label=f"{state.id} run", kind="sqlite-row", uri=f"sqlite://runs/{run.id}")
        ]
        if not result.output.source_links:
            result.output.source_links = [
                SourceLink(label=f"{state.name} contract", kind="procedure-state", uri=f"consciousness://states/{state.id}")
            ]
        result.output.unresolved_risks = list(dict.fromkeys([
            *result.output.unresolved_risks,
            *manifest.unresolved_risks,
        ]))

        cost = _calculate_cost(model.input_cost_per_million, model.output_cost_per_million, result.input_tokens, result.output_tokens)
        context_used = result.input_tokens or manifest.total_estimated_tokens
        final_thoughts = (
            f"{state.name} completed with {len(result.output.source_links)} sources, "
            f"{len(result.output.changed_resources)} changed resources, and "
            f"{context_used}/{model.context_window} context tokens recorded."
        )
        finished = store.finish_run(
            run.id,
            status=RunStatus.succeeded,
            context_used=context_used,
            final_thoughts=final_thoughts,
            changes=changes,
            output=result.output,
            provider_request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            cost=cost,
        )
        heartbeat.assert_owned()
        recap = _add_recap(store, state.id, finished.id, model.id, result.output)
        transition = store.next_transition(state.id, result.output)
        next_state = store.set_current_state(transition.target_id)
        store.add_event(
            "transition.committed",
            {"transition_id": transition.id, "source": state.id, "target": next_state.id},
            run_id=run.id,
        )
        if settings.only_memories_write_recaps and only_memories:
            _write_recap_to_only_memories(
                only_memories,
                finished,
                state.name,
                store,
                settings.only_memories_url,
                settings.only_memories_space_id,
            )
        store.record_execution_success()
        logger.info(
            "run complete",
            extra={
                "fields": {
                    "run_id": finished.id,
                    "state_id": state.id,
                    "next_state_id": next_state.id,
                    "input_tokens": finished.input_tokens,
                    "output_tokens": finished.output_tokens,
                    "cost": finished.cost,
                }
            },
        )
        return TickResult(run=finished, previous_state=state, next_state=next_state, recap=recap)
    except Exception as exc:
        if "run" in locals():
            store.fail_run(run.id, getattr(exc, "category", "execution_error"), str(exc))
        if "definition" in locals():
            store.record_execution_failure(definition.guardrails.loop_control)
        logger.exception(
            "run failed",
            extra={
                "fields": {
                    "run_id": run.id if "run" in locals() else None,
                    "state_id": state.id if "state" in locals() else None,
                    "worker_id": owner,
                    "error_category": getattr(exc, "category", "execution_error"),
                }
            },
        )
        raise
    finally:
        heartbeat.stop()
        if not hold_lease:
            store.release_lease(owner)


def run_worker(database_path: Path | None = None) -> None:
    settings = get_settings()
    store = ConsciousnessStore(database_path or settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    worker_id = f"worker-{uuid.uuid4().hex[:10]}"
    logger.info("worker started", extra={"fields": {"worker_id": worker_id, "database_path": str(store.database_path)}})
    last_run_at = 0.0
    try:
        while True:
            if not store.acquire_lease(worker_id, settings.worker_lease_seconds):
                time.sleep(settings.worker_poll_seconds)
                continue
            store.renew_lease(worker_id, settings.worker_lease_seconds)
            store.recover_stale_work(settings.worker_lease_seconds * 2)
            _execute_approved_actions(store, settings)
            command = store.claim_command()
            if command:
                try:
                    _apply_command(store, command.kind)
                    if command.kind == CommandKind.step:
                        runtime = store.runtime()
                        if runtime.backoff_until and runtime.backoff_until > utcnow():
                            raise RuntimeError(f"execution backoff remains active until {runtime.backoff_until.isoformat()}")
                        run_once(database_path, worker_id=worker_id, hold_lease=True)
                    store.complete_command(command.id)
                except Exception as exc:
                    store.complete_command(command.id, str(exc))
            runtime = store.runtime()
            now = time.monotonic()
            backoff_active = bool(runtime.backoff_until and runtime.backoff_until > utcnow())
            if runtime.status in {RuntimeStatus.running, RuntimeStatus.degraded} and not backoff_active and now - last_run_at >= runtime.interval_seconds:
                try:
                    run_once(database_path, worker_id=worker_id, hold_lease=True)
                    last_run_at = now
                except Exception as exc:
                    store.add_event("worker.tick_failed", {"error": str(exc)})
                    last_run_at = now
            time.sleep(settings.worker_poll_seconds)
    finally:
        store.release_lease(worker_id)


def _apply_command(store: ConsciousnessStore, kind: CommandKind) -> None:
    if kind in {CommandKind.run, CommandKind.resume}:
        store.set_runtime_status(RuntimeStatus.running)
    elif kind == CommandKind.pause:
        store.set_runtime_status(RuntimeStatus.paused)
    elif kind == CommandKind.stop:
        store.set_runtime_status(RuntimeStatus.stopped)
    elif kind == CommandKind.step:
        store.set_runtime_status(RuntimeStatus.paused)


def _publish_validated_changes(store: ConsciousnessStore, registry: ToolRegistry, run_id: str, policies) -> PublishReceipt:
    recent_runs = store.list_runs(limit=200)
    latest_audit = next(
        (
            candidate
            for candidate in recent_runs
            if candidate.state_id == "audit" and candidate.status == RunStatus.succeeded
        ),
        None,
    )
    cycle_runs = [
        candidate
        for candidate in recent_runs
        if latest_audit is None or candidate.started_at > latest_audit.started_at
    ]
    proposals = [
        candidate
        for candidate in cycle_runs
        if candidate.state_id == "curate"
        and candidate.status == RunStatus.succeeded
        and candidate.output
        and isinstance(candidate.output.payload, MemoryChangeProposal)
    ]
    validations = [
        candidate
        for candidate in cycle_runs
        if candidate.state_id == "validate"
        and candidate.status == RunStatus.succeeded
        and candidate.output
        and isinstance(candidate.output.payload, ValidationReport)
    ]
    proposal = None
    validation = None
    for candidate_validation in validations:
        linked_run_ids = {
            item.origin_run_id
            for item in candidate_validation.context_manifest.items
            if item.origin_run_id
        }
        candidate_proposal = next(
            (
                candidate
                for candidate in proposals
                if candidate.started_at < candidate_validation.started_at
                and (not linked_run_ids or candidate.id in linked_run_ids)
            ),
            None,
        )
        if candidate_proposal:
            proposal = candidate_proposal
            validation = candidate_validation
            break
    if proposal is None:
        return PublishReceipt(skipped_reason="no cycle-local validated memory proposal is available")
    assert validation is not None
    prior_receipts = [
        candidate.output.payload
        for candidate in store.list_runs(limit=50, state_id="publish", status=RunStatus.succeeded.value)
        if candidate.output and isinstance(candidate.output.payload, PublishReceipt)
    ]
    if any(receipt.proposal_run_id == proposal.id for receipt in prior_receipts):
        return PublishReceipt(
            proposal_run_id=proposal.id,
            validation_run_id=validation.id,
            skipped_reason="memory proposal was already published",
        )
    report = validation.output.payload
    if not report.sufficient_evidence:
        return PublishReceipt(
            proposal_run_id=proposal.id,
            validation_run_id=validation.id,
            skipped_reason="validation reported insufficient evidence",
        )
    accepted_indexes = {finding.change_index for finding in report.findings if finding.accepted}
    policy = next(policy for policy in policies if policy.state_id == "publish")
    applied: list[str] = []
    pending: list[str] = []
    for index, change in enumerate(proposal.output.payload.changes):
        if index not in accepted_indexes:
            continue
        tool_name, arguments = _memory_change_tool_call(change, proposal.id)
        execution = registry.execute(
            run_id=run_id,
            tool_name=tool_name,
            arguments=arguments,
            policy=policy,
            step_key=f"publish-{index}",
        )
        if execution.approval_id:
            pending.append(execution.approval_id)
        elif execution.result:
            applied.append(str(execution.result.get("id") or tool_name))
    return PublishReceipt(
        applied=applied,
        pending_approval_ids=pending,
        proposal_run_id=proposal.id,
        validation_run_id=validation.id,
    )


def _memory_change_tool_call(change: MemoryChange, proposal_run_id: str) -> tuple[str, dict[str, Any]]:
    if change.action in {"remember", "supersede"}:
        if not change.content:
            raise ValueError(f"{change.action} change requires content")
        arguments: dict[str, Any] = {
            "type": "artifact",
            "content": change.content,
            "source": "consciousness",
            "metadata": {"origin_run_id": proposal_run_id, "reason": change.reason},
        }
        if change.action == "supersede":
            if not change.memory_id:
                raise ValueError("supersede change requires memory_id")
            arguments["memory_id"] = change.memory_id
            return "only_memories.supersede", arguments
        return "only_memories.remember", arguments
    if change.action == "forget":
        if not change.memory_id:
            raise ValueError("forget change requires memory_id")
        return "only_memories.forget", {"memory_id": change.memory_id, "reason": change.reason}
    if change.action == "restore":
        if not change.memory_id:
            raise ValueError("restore change requires memory_id")
        return "only_memories.restore", {"memory_id": change.memory_id}
    if change.action == "reinforce":
        if not change.source_id or not change.target_id:
            raise ValueError("reinforce change requires source_id and target_id")
        return "only_memories.reinforce", {
            "source_id": change.source_id,
            "target_id": change.target_id,
            "amount": change.amount or 0.1,
            "reason": change.reason,
        }
    raise ValueError(f"unsupported memory change action: {change.action}")


def _execute_approved_actions(store: ConsciousnessStore, settings: Settings) -> None:
    approved = store.list_approvals(limit=20, status="approved")
    if not approved:
        return
    only_memories = OnlyMemoriesClient(settings.only_memories_url) if settings.only_memories_url else None
    registry = build_tool_registry(
        store,
        only_memories=only_memories,
        artifacts=ArtifactStore(settings.artifact_root, store),
    )
    for approval in approved:
        try:
            if approval.kind == "tool_call":
                call = store.get_tool_call_by_approval(approval.id)
                registry.execute_approved(call)
            elif approval.kind == "procedure_mutation":
                store.activate_version(
                    str(approval.proposed_action["version_id"]),
                    rationale="approved auditor mutation",
                    record_mutation=False,
                )
                store.mark_mutation_executed(str(approval.proposed_action["mutation_id"]))
                store.mark_approval_executed(approval.id, "Approved procedure version activated by the worker.")
        except Exception as exc:
            store.add_event(
                "approval.execution_failed",
                {"approval_id": approval.id, "error": str(exc)},
                run_id=approval.run_id,
            )


def _stage_audit_mutation(store: ConsciousnessStore, run_id: str, decision: AuditDecision) -> str | None:
    if not decision.mutation_patch:
        return None
    draft = store.create_draft(created_by_run_id=run_id)
    definition = apply_procedure_patch(draft.definition, decision.mutation_patch)
    updated = store.update_draft(draft.id, definition, expected_revision=draft.revision)
    errors = store.validate_version(updated.id)
    if errors:
        raise ValueError("auditor mutation produced an invalid procedure: " + "; ".join(errors))
    _, approval = store.propose_mutation(
        proposed_version_id=updated.id,
        proposer_run_id=run_id,
        rationale=decision.mutation_summary or "Auditor proposed a procedure mutation.",
    )
    return approval.id


def _calculate_cost(input_rate: float, output_rate: float, input_tokens: int, output_tokens: int) -> float:
    return round((input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate, 8)


def _add_recap(store: ConsciousnessStore, state_id: str, run_id: str, model_id: str, output) -> AuditorRecap:
    audit = output.payload if isinstance(output.payload, AuditDecision) else None
    return store.add_recap(
        run_id=run_id,
        auditor_model_id=model_id if state_id == "audit" else "loop-recorder",
        summary=output.summary,
        decision=audit.decision if audit else "continue",
        procedure_changes=audit.mutation_patch if audit else [],
    )


def _check_only_memories(store: ConsciousnessStore, client: OnlyMemoriesClient | None, base_url: str | None) -> None:
    if not client or not base_url:
        store.upsert_integration(IntegrationStatus(name="only-memories", status="disabled", endpoint=None, last_checked_at=utcnow()))
        return
    try:
        health = client.health()
        status = IntegrationStatus(name="only-memories", status="healthy", endpoint=base_url, last_checked_at=utcnow(), details=health)
    except Exception as exc:
        status = IntegrationStatus(name="only-memories", status="unreachable", endpoint=base_url, last_checked_at=utcnow(), details={"error": str(exc)})
    store.upsert_integration(status)


def _write_recap_to_only_memories(
    client: OnlyMemoriesClient,
    run,
    state_name: str,
    store: ConsciousnessStore,
    base_url: str | None,
    space_id: str,
) -> None:
    try:
        memory = client.remember_run_recap(run, state_name, space_id=space_id)
        store.upsert_integration(IntegrationStatus(name="only-memories", status="wrote_recap", endpoint=base_url, last_checked_at=utcnow(), details={"memory_id": memory.get("id")}))
    except Exception as exc:
        store.upsert_integration(IntegrationStatus(name="only-memories", status="write_failed", endpoint=base_url, last_checked_at=utcnow(), details={"error": str(exc)}))


def run_once_cli() -> None:
    configure_structured_logging()
    parser = argparse.ArgumentParser(description="Advance the consciousness loop once.")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    print(run_once(args.db).model_dump_json(indent=2))


def run_loop_cli() -> None:
    configure_structured_logging()
    parser = argparse.ArgumentParser(description="Run the durable consciousness worker.")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    try:
        run_worker(args.db)
    except KeyboardInterrupt:
        pass
