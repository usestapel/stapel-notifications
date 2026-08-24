"""
Push notification channel.

Dispatches to the provider configured via the ``PUSH_PROVIDER`` key of the
``STAPEL_NOTIFICATIONS`` namespace (or the ``PUSH_PROVIDER`` env var):

  fcm   — Firebase Cloud Messaging (default)
  mock  — Log only, no real sending

Besides the short names, any dotted path to a provider class with a
``send(user_id, title, body, data) -> int`` method is accepted — the same
fork-free escape hatch as the email/SMS channels and captcha backends.
"""

import logging
import threading

from django.core.exceptions import ImproperlyConfigured

from stapel_notifications.channels.sms import UNCONFIGURED, UNCONFIGURED_MESSAGE
from stapel_notifications.models import DevicePushToken

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
    ).values_list('token', 'platform')


# ──────────────────────────────────────────────────────────────────
# Provider classes
# ──────────────────────────────────────────────────────────────────

class _MockPushProvider:
    def send(self, user_id: str, title: str, body: str, data: dict | None) -> int:
        count = len(_active_tokens(user_id))
        logger.info(
            "[mock push] user=%s title=%r active_tokens=%d", user_id, title, count
        )
        return count


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

        sent_count = 0
        for token, platform in tokens:
            try:
                # Build platform-specific message
                notification = messaging.Notification(title=title, body=body)
                message = messaging.Message(
                    notification=notification,
                    token=token,
                    data={k: str(v) for k, v in (data or {}).items()},
                )

                # iOS-specific: set badge and sound
                if platform == 'ios':
                    message.apns = messaging.APNSConfig(
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(sound='default'),
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

#: Variables whose value is a deep link the push payload may carry.
_DEEP_LINK_KEYS = ("chat_url", "listing_url", "notifications_chat_url")


def _deliver_push(msg) -> bool:
    """Hand title/body/deep-links to the push provider for every device."""
    import logging

    if not msg.user_id:
        raise ValueError("No user_id for push notification")

    all_vars = msg.all_vars
    title = all_vars.get(
        "push_title", all_vars.get("heading", all_vars.get("company_name", ""))
    )
    body = all_vars.get("push_body", msg.body)
    data = {"notification_type": msg.notification_type}
    for key in _DEEP_LINK_KEYS:
        if key in all_vars:
            data[key] = all_vars[key]

    sent_count = send_push(msg.user_id, title, body, data)
    if sent_count == 0:
        # Not False: the provider WAS reached, the recipient simply has no
        # device registered. Returning False here would release the delivery
        # claim and journal a reachability gap for a channel that worked.
        logging.getLogger(__name__).warning(
            "No active push tokens for user %s, notification_type=%s",
            msg.user_id, msg.notification_type,
        )
    return True


#: The registry entry for this channel.
push_channel = Channel(name="push", deliver=_deliver_push)
