"""Error constants for notifications service."""
from stapel_core.django.api.errors import register_service_errors

ERR_400_INVALID_PLATFORM = 'error.400.invalid_platform'
ERR_404_TOKEN_NOT_FOUND = 'error.404.token_not_found'

SERVICE_ERRORS = {
    ERR_400_INVALID_PLATFORM: 'Platform must be one of: ios, android, web.',
    ERR_404_TOKEN_NOT_FOUND: 'Device token not found.',
}

# Machine-readable recovery hints (remediation) — the canonical "what to do"
# for each key, emitted into the errors.json codegen artifact and consumed by the
# frontend/LLM (frontend-core-architecture §2.5). Vocabulary: retry |
# wait_and_retry | reauthenticate | verify | fix_input | contact_support | bug.
# Declared here (backend = canon) rather than left to the status+name heuristic:
# both keys are caused by a bad request argument (a platform value outside the
# {ios, android, web} set; a device-token path that matches no active token), so
# the honest recovery is "correct the input" — not the heuristic's default of
# `retry` for a 404 `not_found`, which would loop the same failing request.
SERVICE_REMEDIATION = {
    ERR_400_INVALID_PLATFORM: 'fix_input',
    ERR_404_TOKEN_NOT_FOUND: 'fix_input',
}

register_service_errors(SERVICE_ERRORS, remediation=SERVICE_REMEDIATION)
