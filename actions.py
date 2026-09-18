"""Action subscriptions of the notifications module.

Handlers must be idempotent: delivery is at-least-once (outbox retries,
broker redelivery).

The erasure protocol is not written here. ``apps.ready()`` registers this
module as a data owner with :func:`stapel_core.gdpr.register_gdpr_owner`,
which subscribes ``gdpr.erasure.requested``, ``gdpr.owner.probe`` and the
deprecated ``user.deleted`` around
:func:`~stapel_notifications.erasure.erase_subject` — one implementation of
the protocol for the fleet, and the probe still answered from the subscriber
that erases.

The account life cycle is still answered as a pair — that registration
erases on ``user.deleted``, ``user.merged`` below re-parents — which core
0.52.x requires of every subscriber of either
(``stapel_core.lifecycle.E001``).
"""
import logging

from django.core.exceptions import ValidationError

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


@on_action("user.merged")
def handle_user_merged(event):
    """Carry a merged-away account's notification data to the survivor.

    stapel-auth absorbs an anonymous guest into an existing account and then
    DELETES the guest row. Nothing here is an FK — every user column is a
    bare ``UUIDField`` — so the rows are not cascaded away; they are
    stranded, addressed to an account that can no longer sign in. The person
    signs in and their bell is empty, their device stops receiving pushes,
    and the preferences they set moments earlier are gone.

    Four models carry a user, and they do NOT all move the same way:

    * ``NotificationLog`` — **re-parented**. The feed is the person's own
      history and their unread count; ``user_id`` carries no uniqueness, so
      this is a plain bulk update.
    * ``DevicePushToken`` — **re-parented**. The same physical device is now
      the survivor's. ``token`` is unique across the whole table, not per
      user, so two rows for one device cannot exist and nothing can collide.
    * ``UserNotificationSettings`` — **survivor wins**. ``user_id`` is the
      primary key: one row per person, and a merge is the case where both
      have one. The survivor's preferences are an account's settled choices;
      a guest session's are not allowed to overwrite them, so the guest's row
      is dropped. It is carried over only when the survivor has no row at
      all, where it is the person's most recent explicit choice and the
      alternative is silently reverting them to defaults.
    * ``UserContact`` — **dropped, never carried**. ``user_id`` is the
      primary key here too, but this table is a *projection of auth*, not
      this module's own data: the survivor's address arrives on their own
      contact sync and is authoritative. Re-parenting a guest's row onto an
      account with no synced contact yet would file a stale address under
      the survivor's id and then write to it — sending a person's
      notifications somewhere they do not own. A projection is repaired by
      its source, not by a consumer's guess.

    ``NotificationDelivery`` and ``TranslationCache`` name no user.
    Delivery claims are keyed by ``(event_id, channel, recipient,
    template_version)`` — the address, which a merge does not change — so
    there is nothing there to move and no double-send to cause.

    Idempotent: a redelivery finds nothing left under the guest and reports
    zeroes. There is no "survivor not projected yet" case to retry, unlike
    the modules that hold a real FK — a ``UUIDField`` needs no user row to
    exist before it can be written.
    """
    from django.db import transaction

    from .models import (
        DevicePushToken,
        NotificationLog,
        UserContact,
        UserNotificationSettings,
    )

    payload = event.payload or {}
    from_user_id = payload.get("from_user_id")
    into_user_id = payload.get("into_user_id")
    if not from_user_id or not into_user_id:
        logger.error(
            "user.merged without from/into user id: %s",
            getattr(event, "event_id", "?"),
        )
        return
    if str(from_user_id) == str(into_user_id):
        return

    try:
        with transaction.atomic():
            logs = NotificationLog.objects.filter(user_id=from_user_id).update(
                user_id=into_user_id
            )
            tokens = DevicePushToken.objects.filter(user_id=from_user_id).update(
                user_id=into_user_id
            )
            # One row per person, so the merge is a choice, not an update.
            settings_kept = 0
            if not UserNotificationSettings.objects.filter(
                user_id=into_user_id
            ).exists():
                settings_kept = UserNotificationSettings.objects.filter(
                    user_id=from_user_id
                ).update(user_id=into_user_id)
            else:
                UserNotificationSettings.objects.filter(
                    user_id=from_user_id
                ).delete()
            # Auth owns this row; the survivor's own sync fills it.
            contacts, _ = UserContact.objects.filter(user_id=from_user_id).delete()
    except (TypeError, ValueError, ValidationError):
        # A UUIDField raises ValidationError — NOT a ValueError — for an id
        # that is not a UUID, and an id that cannot address a row here names
        # nothing. Saying so quietly beats a redelivery loop over a payload
        # no retry can fix.
        logger.error(
            "user.merged with unusable user ids: %s",
            getattr(event, "event_id", "?"),
        )
        return

    logger.info(
        "user.merged %s -> %s: %s log row(s), %s push token(s), %s settings "
        "row(s) carried over, %s guest contact row(s) dropped",
        from_user_id, into_user_id, logs, tokens, settings_kept, contacts,
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
