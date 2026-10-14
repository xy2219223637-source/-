"""Server-owned generation gates; disabling them never downgrades saved plans."""
from app.config import get_settings
from app.services.refinement_authorization import AUTHORIZATION_VERSION


def generation_policy(tenant_id):
    settings = get_settings()
    allowed = str(tenant_id) in {value.strip() for value in settings.refinement_rollout_tenants.split(",") if value.strip()}
    return {"authorization_version":AUTHORIZATION_VERSION,
            "timing_protocol":"v3" if allowed and settings.refinement_precise_timing_enabled else "v2",
            "localization":allowed and settings.refinement_localization_enabled,
            "content_reselection":allowed and settings.refinement_content_reselection_enabled,
            "display_tail_ms":100 if allowed and settings.refinement_display_tail_enabled else 0,
            "policy_version":"refinement-policy-1"}
