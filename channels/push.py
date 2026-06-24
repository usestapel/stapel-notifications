"""
Push notification channel via Firebase Cloud Messaging (FCM).
"""

import logging
import threading

from django.conf import settings

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

            cred_path = getattr(settings, 'GOOGLE_APPLICATION_CREDENTIALS', None)
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
    if not _ensure_firebase():
        raise RuntimeError("Firebase not configured")

    from firebase_admin import messaging

    tokens = DevicePushToken.objects.filter(
        user_id=user_id,
        is_active=True,
    ).values_list('token', 'platform')

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
