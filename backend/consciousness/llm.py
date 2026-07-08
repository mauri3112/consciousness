from __future__ import annotations

from .models import ModelProfile, ProcedureState


def choose_model(state: ProcedureState, models: list[ModelProfile]) -> ModelProfile:
    """Choose the lowest-cost model that satisfies the state policy."""
    eligible = [
        model
        for model in models
        if model.enabled and model.context_window >= state.context_minimum
    ]
    if not eligible:
        raise RuntimeError(f"no enabled model can satisfy {state.context_minimum} context tokens")

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
