"""Action subscriptions of the notifications module.

Handlers must be idempotent: delivery is at-least-once (outbox retries,
broker redelivery).
"""
import logging

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


@on_action("user.deleted")
def handle_user_deleted(event):
    """Erase this module's PII when an account deletion is executed."""
    from .gdpr import NotificationsGDPRProvider

    user_id = event.payload.get("user_id")
    if not user_id:
        logger.error("user.deleted event without user_id: %s", event.event_id)
        return
    NotificationsGDPRProvider().delete(user_id)
    logger.info("notifications data erased for deleted user %s", user_id)


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
