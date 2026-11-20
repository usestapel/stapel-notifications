"""
Push notification channel.

Dispatches to the provider configured via the ``PUSH_PROVIDER`` key of the
``STAPEL_NOTIFICATIONS`` namespace (or the ``PUSH_PROVIDER`` env var):

  fcm   — Firebase Cloud Messaging (default)
  mock  — Log only, no real sending

Besides the short names, any dotted path to a provider class with a
``send(user_id, title, body, data) -> int`` method is accepted — the same
fork-free escape hatch as the email/SMS channels and captcha backends.

Every provider receives the same ``data`` map — the push payload contract of
``push_payload`` (``docs/push.json``) — and the built-in ones turn it into one
message per device with ``push_payload.compose``.
"""

import logging
import threading

from django.core.exceptions import ImproperlyConfigured

from stapel_notifications.channels.sms import UNCONFIGURED, UNCONFIGURED_MESSAGE
from stapel_notifications.models import DevicePushToken
from stapel_notifications.push_payload import LEGACY_LINK_KEYS, PAYLOAD_VERSION, compose, link_of

logger = logging.getLogger(__name__)

_firebase_lock = threading.Lock()
_app_initialized = False


def _ensure_firebase():
    """Initialize Firebase app once (thread-safe)."""
    global _app_initialized
    if _app_initialized:
        return True

    with _firebase_lock:
        # Double-check after acquiring lock
        if _app_initialized:
            return True
        try:
            import firebase_admin
            from firebase_admin import credentials

            from stapel_notifications.conf import notifications_settings

            cred_path = notifications_settings.GOOGLE_APPLICATION_CREDENTIALS
            if not cred_path:
                logger.warning("GOOGLE_APPLICATION_CREDENTIALS not set, push disabled")
                return False

            if not firebase_admin._apps:
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)

            _app_initialized = True
            return True
        except Exception as e:
            logger.error("Firebase initialization failed: %s", e)
            return False


def _active_tokens(user_id: str):
    return DevicePushToken.objects.filter(
        user_id=user_id,
        is_active=True,
    ).order_by('pk').values_list('pk', 'token', 'platform')


# ──────────────────────────────────────────────────────────────────
# Provider classes
# ──────────────────────────────────────────────────────────────────

#: What the mock provider "sent": one entry per device, in order —
#: ``{"user_id", "device": DevicePushToken.pk, "platform", "message"}`` where
#: ``message`` is exactly what FCM would have been handed (``compose``). The
#: push twin of Django's ``mail.outbox``; tests read and clear it.
mock_outbox: list[dict] = []


class _MockPushProvider:
    def send(self, user_id: str, title: str, body: str, data: dict | None) -> int:
        devices = list(_active_tokens(user_id))
        for pk, _token, platform in devices:
            mock_outbox.append({
                "user_id": str(user_id),
                "device": pk,
                "platform": platform,
                "message": compose(title=title, body=body, data=data or {}),
            })
        logger.info(
            "[mock push] user=%s title=%r active_tokens=%d", user_id, title, len(devices)
        )
        return len(devices)


class _UnconfiguredPushProvider:
    """Push's half of the "nobody chose a backend" contract.

    Not the shipped default here — ``PUSH_PROVIDER`` still defaults to
    ``fcm``, which already refuses loudly without credentials. It exists so
    ``unconfigured`` means the same thing on every channel and a host can
    close push explicitly.
    """

    def send(self, user_id: str, title: str, body: str, data: dict | None) -> int:
        raise ImproperlyConfigured(UNCONFIGURED_MESSAGE.format(setting="PUSH_PROVIDER"))


class _FCMPushProvider:
    def send(self, user_id: str, title: str, body: str, data: dict | None) -> int:
        if not _ensure_firebase():
            raise RuntimeError("Firebase not configured")

        from firebase_admin import messaging

        tokens = _active_tokens(user_id)

        if not tokens:
            logger.info("No active push tokens for user %s", user_id)
            return 0

        payload = compose(title=title, body=body, data=data or {})
        sent_count = 0
        for _pk, token, _platform in tokens:
            try:
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=payload["title"], body=payload["body"],
                    ),
                    token=token,
                    data=payload["data"],
                    android=messaging.AndroidConfig(
                        notification=messaging.AndroidNotification(
                            channel_id=payload["android"]["channel_id"],
                        ),
                    ),
                    apns=messaging.APNSConfig(
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(
                                badge=payload["apns"]["badge"],
                                sound=payload["apns"]["sound"],
                                thread_id=payload["apns"]["thread_id"],
                            ),
                        ),
                    ),
                )
                messaging.send(message)
                sent_count += 1

            except messaging.UnregisteredError:
                logger.info("Deactivating unregistered token for user %s", user_id)
                DevicePushToken.objects.filter(token=token).update(is_active=False)

            except Exception as e:
                logger.error("Push failed for user %s token %s...: %s", user_id, token[:20], e)

        return sent_count


# ──────────────────────────────────────────────────────────────────
# Registry + facade
# ──────────────────────────────────────────────────────────────────

_PROVIDERS: dict[str, type] = {
    'fcm':         _FCMPushProvider,
    'mock':        _MockPushProvider,
    UNCONFIGURED:  _UnconfiguredPushProvider,
}


def _get_provider():
    from stapel_notifications.channels.sms import _resolve_provider
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.PUSH_PROVIDER, _PROVIDERS, "push", "PUSH_PROVIDER"
    )


def send_push(user_id: str, title: str, body: str, data: dict | None = None) -> int:
    """
    Send push notification to all active devices for a user.

    Args:
        user_id: Target user UUID
        title: Notification title
        body: Notification body
        data: Optional data payload (deep links, etc.)

    Returns:
        Number of successfully sent messages.
    """
    return _get_provider().send(user_id, title, body, data)


# ─── The channel object (registry seam) ─────────────────────

from .registry import Channel  # noqa: E402  (kept beside its use)

def _unread_after_this(user_id) -> int:
    """The badge: the recipient's unread feed rows plus the one being sent
    (its row is written after the provider returns)."""
    from stapel_notifications.models import NotificationLog

    return NotificationLog.objects.filter(
        user_id=user_id, status="sent", channel="push", read_at__isnull=True,
    ).count() + 1


def _deliver_push(msg) -> bool:
    """Hand the push payload contract (``docs/push.json``) to the provider."""
    import logging

    if not msg.user_id:
        raise ValueError("No user_id for push notification")

    all_vars = msg.all_vars
    title = all_vars.get(
        "push_title", all_vars.get("heading", all_vars.get("company_name", ""))
    )
    body = all_vars.get("push_body", msg.body)
    data = {
        "v": PAYLOAD_VERSION,
        "notification_type": msg.notification_type,
        "group": msg.routing.get("group") or "system",
        "badge": str(_unread_after_this(msg.user_id)),
    }
    if msg.log_id is not None:
        data["notification_id"] = str(msg.log_id)
    url = link_of(all_vars, msg.routing)
    if url:
        data["url"] = url
    for key in LEGACY_LINK_KEYS:
        if key in all_vars:
            data[key] = str(all_vars[key])

    sent_count = send_push(msg.user_id, title, body, data)
    # The count, always — not only when it is zero. `True` means "the provider
    # was reached", and on this channel that is not the same as "a handset got
    # it": the in-app feed IS this journal (views.NotificationFeedView reads
    # `status="sent", channel="push"`), so a web-only user must keep the row.
    # Returning False would delete their feed item to make a number honest.
    #
    # So the row stays `sent` and carries the number instead. `device_count=0`
    # is "it is in the feed and on no handset" — a state the journal could not
    # previously express, which made delivery dashboards overcount and hid the
    # gap from the undeliverable escalation. See services._dispatch and
    # NotificationLog.device_count.
    msg.facts["device_count"] = sent_count
    if sent_count == 0:
        logging.getLogger(__name__).warning(
            "No active push tokens for user %s, notification_type=%s — the "
            "feed row is written, no handset was reached",
            msg.user_id, msg.notification_type,
        )
    return True


#: The registry entry for this channel.
push_channel = Channel(name="push", deliver=_deliver_push)
