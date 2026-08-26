"""Error constants for notifications service."""
from stapel_core.django.api.errors import register_service_errors

ERR_400_INVALID_PLATFORM = 'error.400.invalid_platform'
ERR_404_TOKEN_NOT_FOUND = 'error.404.token_not_found'
# Distinct from the token key on purpose: the caller who reaches this one
# passed a device id read out of GET /devices/, not a token it holds. Telling
# it "device token not found" would send a client looking for a token it never
# sent, and the two conditions have different recoveries — re-list the devices
# vs re-register this device.
ERR_404_DEVICE_NOT_FOUND = 'error.404.device_not_found'
# Mark-as-read takes a target: a list of ids, or the whole feed. Sending
# neither (an empty body, an empty `ids`) and sending both are the same class
# of mistake — the request does not say what to mark — and answering 200 with
# `marked: 0` would let a "mark all read" button that forgot its flag look
# like a feed that was already read.
ERR_400_READ_TARGET_REQUIRED = 'error.400.read_target_required'
# The id list is bounded so one request cannot ask the database for an
# unbounded IN (...). A client with more than a page of ids to clear wants
# `all: true`, which is one UPDATE regardless of size.
ERR_400_TOO_MANY_IDS = 'error.400.too_many_ids'

SERVICE_ERRORS = {
    ERR_400_INVALID_PLATFORM: 'Platform must be one of: ios, android, web.',
    ERR_404_TOKEN_NOT_FOUND: 'Device token not found.',
    ERR_404_DEVICE_NOT_FOUND: 'Device not found, or it is not registered to you.',
    ERR_400_READ_TARGET_REQUIRED: 'Send exactly one of: a non-empty "ids" list, or "all": true.',
    ERR_400_TOO_MANY_IDS: 'Too many ids in one request. Send fewer, or "all": true.',
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
    # A device id that answers nothing means the caller's list is stale (the
    # row was already removed here, or by an account switch on that device).
    # Re-reading GET /devices/ is the recovery, not resending the same id.
    ERR_404_DEVICE_NOT_FOUND: 'verify',
    # Both read-target keys are the request saying the wrong thing, and the
    # client can say the right thing without asking anybody: name the ids, or
    # set the flag.
    ERR_400_READ_TARGET_REQUIRED: 'fix_input',
    ERR_400_TOO_MANY_IDS: 'fix_input',
}

register_service_errors(SERVICE_ERRORS, remediation=SERVICE_REMEDIATION)
