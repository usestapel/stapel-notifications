"""
Push notification channel.

Dispatches to the provider configured via the ``PUSH_PROVIDER`` key of the
``STAPEL_NOTIFICATIONS`` namespace (or the flat ``PUSH_PROVIDER`` setting /
env var):

  fcm   — Firebase Cloud Messaging (default)
  mock  — Log only, no real sending

Besides the short names, any dotted path to a provider class with a
``send(user_id, title, body, data) -> int`` method is accepted — the same
fork-free escape hatch as the email/SMS channels and captcha backends.
"""

import logging
import threading


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
    'fcm':  _FCMPushProvider,
    'mock': _MockPushProvider,
}


def _get_provider():
    from stapel_notifications.channels.sms import _resolve_provider
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.PUSH_PROVIDER, _PROVIDERS, _MockPushProvider, "push"
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
