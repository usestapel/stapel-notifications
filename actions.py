"""Action subscriptions of the notifications module.

Handlers must be idempotent: delivery is at-least-once (outbox retries,
broker redelivery).

The GDPR pair below — ``gdpr.erasure.requested`` and ``gdpr.owner.probe`` —
lives in this one file on purpose. The probe is answered from the same
subscriber that erases, so ``gdpr.owner.alive`` is evidence that the erasure
path is *consumed*, not merely that a container is deployed (stapel-gdpr
MODULE.md, "Erasure parts", option 2).
"""
import logging

from django.core.exceptions import ValidationError

from stapel_core.comm import on_action

from .erasure import GDPR_OWNER, GDPR_SUBJECT_TYPES

logger = logging.getLogger(__name__)

ERASURE_REQUESTED_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "gdpr.erasure.requested",
    "type": "object",
    "required": ["correlation_id", "subject_type", "subject_key"],
    "properties": {
        "request_id": {"type": "integer"},
        "correlation_id": {"type": "string"},
        "subject_type": {"type": "string"},
        "subject_key": {"type": "string"},
        "workspace_id": {"type": "string"},
        "requested_by": {"type": "string"},
        "origin": {"type": "string"},
        "due_at": {"type": "string"},
    },
    "additionalProperties": False,
}

OWNER_PROBE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "gdpr.owner.probe",
    "type": "object",
    "required": ["correlation_id"],
    "properties": {"correlation_id": {"type": "string"}},
    "additionalProperties": False,
}


def _receipt(correlation_id, subject_type, subject_key, counts) -> None:
    """Emit ``gdpr.section.erased`` for work that has just committed.

    Callers hold an open transaction; the emit rides the outbox, so the
    receipt leaves iff the erasure commits. An owner that receipts a
    rollback is worse than one that stays silent — the orchestrator counts
    the receipt and finalizes.
    """
    from stapel_core.comm import emit

    emit(
        "gdpr.section.erased",
        {
            "correlation_id": str(correlation_id),
            "owner": GDPR_OWNER,
            "subject_type": subject_type,
            "subject_key": str(subject_key),
            "counts": counts,
        },
        key=str(subject_key),
    )


@on_action("gdpr.erasure.requested", schema=ERASURE_REQUESTED_SCHEMA)
def handle_erasure_requested(event):
    """Erase the named subject and confirm with counts (stapel-gdpr 0.5.0).

    A subject type this module does not claim is not ours to answer: the
    orchestrator creates a part only for owners that declared the type, and
    a receipt against a part that does not exist teaches it nothing.
    """
    from django.db import transaction

    from .erasure import erase_subject

    payload = event.payload
    subject_type = payload.get("subject_type")
    subject_key = payload.get("subject_key")
    correlation_id = payload.get("correlation_id")
    if subject_type not in GDPR_SUBJECT_TYPES:
        return
    if not subject_key or not correlation_id:
        logger.error(
            "malformed gdpr.erasure.requested: %s", getattr(event, "event_id", "?"),
        )
        return

    try:
        with transaction.atomic():
            counts = erase_subject(subject_type, subject_key)
            _receipt(correlation_id, subject_type, subject_key, counts)
    except (TypeError, ValueError, ValidationError):
        # An unparseable key names no row here. Receipting would claim an
        # erasure that never happened; raising would retry forever.
        logger.error(
            "gdpr.erasure.requested with unusable %s key %r [correlation=%s]",
            subject_type, subject_key, correlation_id,
        )
        return
    logger.info(
        "notifications erased %s %s: %s [correlation=%s]",
        subject_type, subject_key, counts, correlation_id,
    )


@on_action("gdpr.owner.probe", schema=OWNER_PROBE_SCHEMA)
def handle_owner_probe(event):
    """Answer the liveness probe — see this module's docstring for why here."""
    from stapel_core.comm import emit

    emit(
        "gdpr.owner.alive",
        {
            "owner": GDPR_OWNER,
            "subject_types": list(GDPR_SUBJECT_TYPES),
            "correlation_id": str(event.payload.get("correlation_id") or ""),
        },
        key=GDPR_OWNER,
    )


@on_action("user.deleted")
def handle_user_deleted(event):
    """Erase this module's PII when an account deletion is executed.

    Deprecated upstream: stapel-gdpr emits ``user.deleted`` alongside
    ``gdpr.erasure.requested`` for account subjects until 0.6.0. Both land
    here, both run the same
    :func:`~stapel_notifications.erasure.erase_account`, and both receipt —
    until now this handler erased and said nothing, so the orchestrator's
    part for this owner stayed unconfirmed until it timed out thirty days
    later. The part flips once and ignores the second receipt, so the two
    paths cannot drift into disagreeing.
    """
    from django.db import transaction

    from .erasure import erase_account

    user_id = event.payload.get("user_id")
    if not user_id:
        logger.error("user.deleted event without user_id: %s", event.event_id)
        return
    correlation_id = event.payload.get("correlation_id")
    with transaction.atomic():
        counts = erase_account(user_id)
        if correlation_id:
            _receipt(correlation_id, "account", user_id, counts)
    logger.info(
        "notifications data erased for deleted user %s: %s", user_id, counts,
    )


@on_action("user.deletion_initiated")
def handle_user_deletion_initiated(event):
    """Account-closure grace period started: stop notifying the user.

    Soft and reversible — the contact and the push tokens are only
    deactivated, not erased (full erasure stays on ``user.deleted``).
    Reactivation happens through the normal sync paths (contact-changed
    events, device re-registration); there is currently no dedicated
    "closure cancelled" event to subscribe to (see CHANGELOG).
    """
    from .models import DevicePushToken, UserContact

    user_id = event.payload.get("user_id")
    if not user_id:
        logger.error(
            "user.deletion_initiated event without user_id: %s", event.event_id
        )
        return
    contacts = UserContact.objects.filter(user_id=user_id).update(is_active=False)
    tokens = DevicePushToken.objects.filter(user_id=user_id).update(is_active=False)
    logger.info(
        "deactivated %d contact(s) and %d push token(s) for user %s "
        "(deletion grace period)", contacts, tokens, user_id,
    )


@on_action("translations.changed")
def handle_translations_changed(event):
    """Refresh cached ``notification.*`` translations on invalidation.

    The event is a thin invalidation ({language, keys_changed}); the values
    are pulled through the ``translate.resolve`` comm Function. Errors
    propagate so at-least-once delivery retries the sync.
    """
    from .translations import resolve_and_cache

    language = event.payload.get("language")
    keys_changed = event.payload.get("keys_changed") or []
    keys = [
        k for k in keys_changed
        if isinstance(k, str) and k.startswith("notification.")
    ]
    if not language or not keys:
        return
    resolve_and_cache(keys, language)
