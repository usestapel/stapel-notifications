"""The two inbound seams that have a bus-command shape AND an in-process shape.

``notification.requested`` (``stapel_core.notifications.request_notification``)
is a bus publish, not a comm action. On a broker the ``consume_notifications``
command reads it; on an in-process bus no command can run (core refuses to
start one), so this module subscribes the same handler to the bus itself.
``profile.changed`` arrives as a comm action (``actions.py``) and from the
legacy ``consume_profiles`` topic; both call :func:`sync_profile_preferences`.

One function per seam, called by every transport, so the paths cannot drift.
"""
from __future__ import annotations

import logging

from stapel_core.kafka.events import EventType
from stapel_core.kafka.topics import TOPIC_NOTIFICATION_REQUESTED

logger = logging.getLogger(__name__)

_DEDUP_PREFIX = "stapel_notifications:dedup"


def dedup_cache_key(notification_type: str, dedup_key: str) -> str:
    """The cache key a ``(notification_type, dedup_key)`` claim is held under."""
    return f"{_DEDUP_PREFIX}:{notification_type}:{dedup_key}"


def _dedup_window(payload: dict) -> int:
    from .conf import notifications_settings

    ttl = payload.get("dedup_ttl")
    if isinstance(ttl, int) and not isinstance(ttl, bool) and ttl > 0:
        return ttl
    return int(notifications_settings.DEDUP_DEFAULT_TTL)


def _claim_dedup(notification_type: str, payload: dict) -> str | None:
    """Claim the request's dedup key. Returns the cache key, ``""`` when the
    request carries none, or ``None`` when the key is already claimed."""
    from django.core.cache import cache

    dedup_key = payload.get("dedup_key")
    if not (isinstance(dedup_key, str) and dedup_key):
        return ""
    key = dedup_cache_key(notification_type, dedup_key)
    if cache.add(key, 1, _dedup_window(payload)):
        return key
    return None


def handle_notification_requested(event) -> None:
    """Dispatch one ``notification.requested`` envelope (any transport)."""
    from .services import process_notification

    if event.event_type != EventType.NOTIFICATION_REQUESTED:
        logger.warning(
            "Unknown event type: %s (event_id=%s)", event.event_type, event.event_id
        )
        return
    payload = event.payload or {}
    notification_type = payload.get("notification_type")
    if not notification_type:
        logger.error(
            "Notification event missing notification_type (event_id=%s)",
            event.event_id,
        )
        return
    claim = _claim_dedup(notification_type, payload)
    if claim is None:
        logger.info(
            "Skipping notification: type=%s, dedup_key already delivered "
            "within its window (event_id=%s)",
            notification_type, event.event_id,
        )
        return
    logger.info(
        "Processing notification: type=%s, event_id=%s",
        notification_type, event.event_id,
    )
    try:
        _process(process_notification, notification_type, payload, event)
    except Exception:
        if claim:
            # A request that was not delivered must stay deliverable: a
            # redelivery or retry inside the window would otherwise be
            # swallowed as a duplicate of nothing.
            from django.core.cache import cache

            cache.delete(claim)
        raise


def _process(process_notification, notification_type: str, payload: dict, event) -> None:
    process_notification(
        notification_type=notification_type,
        user_id=payload.get("user_id"),
        variables=payload.get("variables", {}),
        email=payload.get("email"),
        phone=payload.get("phone"),
        telegram_chat_id=payload.get("telegram_chat_id"),
        language=payload.get("language"),
        event_id=event.event_id,
        content_html=payload.get("content_html"),
        content_text=payload.get("content_text"),
    )


def _deliver_after_commit(event) -> None:
    """The in-process subscriber: send once the publisher's work is durable.

    MemoryBus calls subscribers synchronously inside ``publish()``, i.e.
    inside the caller's request and often inside its transaction. Deferring
    to ``on_commit`` keeps a rolled-back request from mailing anybody and
    keeps the provider call out of the caller's transaction; outside a
    transaction it runs at once. ``robust`` logs a failed send instead of
    raising it into the caller's commit.
    """
    from django.db import transaction

    transaction.on_commit(
        lambda: handle_notification_requested(event), robust=True
    )


def ensure_in_process_consumer(bus=None) -> bool:
    """Subscribe the notification handler for an in-process bus.

    Uses core's persistent subscription (``subscribe_in_process``): it
    survives ``reset_bus()`` and ``MemoryBus.clear()`` and is inert on a
    broker, where the ``consume_notifications`` command reads the topic.
    Idempotent. Returns True when the topic is delivered in this process.
    """
    from stapel_core.bus import carries_in_process, subscribe_in_process

    subscribe_in_process(TOPIC_NOTIFICATION_REQUESTED, _deliver_after_commit)
    return carries_in_process(TOPIC_NOTIFICATION_REQUESTED, bus)


def sync_profile_preferences(payload: dict) -> bool:
    """Apply a profile's channel switches to ``UserNotificationSettings``.

    Returns True when a row was written. The pairs are the channel
    REGISTRY's, not a literal list: a host channel's switch is synced too.
    The recipient's language is not mirrored (asked of profiles at send
    time, see language.py).
    """
    from django.core.exceptions import ValidationError

    from .models import UserNotificationSettings
    from .services import _MODEL_PREF_FIELDS, valid_pref_fields

    user_id = (payload or {}).get("user_id")
    if not user_id:
        return False

    defaults = {}
    channel_preferences = {}
    for field in sorted(valid_pref_fields()):
        if field not in payload:
            continue
        if field in _MODEL_PREF_FIELDS:
            defaults[field] = bool(payload[field])
        else:
            channel_preferences[field] = bool(payload[field])
    if not defaults and not channel_preferences:
        return False

    try:
        if channel_preferences:
            existing = (
                UserNotificationSettings.objects
                .filter(user_id=user_id)
                .values_list("channel_preferences", flat=True)
                .first()
            ) or {}
            defaults["channel_preferences"] = {**existing, **channel_preferences}
        UserNotificationSettings.objects.update_or_create(
            user_id=user_id, defaults=defaults,
        )
    except (TypeError, ValueError, ValidationError):
        logger.error("profile.changed with an unusable user id")
        return False
    logger.info("Synced notification settings for user %s", user_id)
    return True
