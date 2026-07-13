from __future__ import annotations

from .models import ModelProfile, ProcedureState


def choose_model(
    state: ProcedureState,
    models: list[ModelProfile],
    *,
    daily_spend: float = 0,
    daily_budget_cap: float | None = None,
    required_capabilities: set[str] | None = None,
    local_only: bool = False,
) -> ModelProfile:
    """Choose the cheapest eligible model after applying hard constraints."""
    required = required_capabilities or {"structured-output"}
    eligible = [
        model
        for model in models
        if model.enabled
        and model.context_window >= state.context_minimum
        and required <= set(model.capabilities)
        and (not local_only or model.provider in {"ollama", "local"})
        and (state.max_run_budget is None or model.max_run_budget <= state.max_run_budget)
        and (daily_budget_cap is None or model.provider in {"ollama", "local"} or daily_spend + model.max_run_budget <= daily_budget_cap)
    ]
    if not eligible:
        raise RuntimeError(f"no enabled model can satisfy {state.context_minimum} context tokens")

    if state.preferred_model_id:
        preferred = next((model for model in eligible if model.id == state.preferred_model_id), None)
        if preferred:
            return preferred
        if not state.allow_model_fallback:
            raise RuntimeError(
                f"pinned model {state.preferred_model_id!r} cannot satisfy state {state.id!r}"
            )

    policy = state.model_policy
    if policy == "auditor":
        auditor_models = [model for model in eligible if "procedure-design" in model.strengths or model.quality_tier >= 5]
        if auditor_models:
            return sorted(auditor_models, key=lambda model: (model.relative_cost, -model.context_window))[0]

    if policy == "maintenance":
        maintenance_models = [model for model in eligible if "maintenance" in model.strengths or "curation" in model.strengths]
        if maintenance_models:
            return sorted(maintenance_models, key=lambda model: (model.relative_cost, model.quality_tier))[0]

    if policy == "balanced":
        balanced_models = [model for model in eligible if model.quality_tier >= 3]
        if balanced_models:
            return sorted(balanced_models, key=lambda model: (model.relative_cost, model.quality_tier))[0]

    return sorted(eligible, key=lambda model: (model.relative_cost, model.quality_tier, model.context_window))[0]
