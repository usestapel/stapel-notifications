"""
Kafka consumer for profile-changed events.

Syncs user notification preferences and language from profiles service.
"""

import logging

from stapel_core.kafka import BaseKafkaConsumerCommand, Event, EventType
from stapel_core.kafka.topics import TOPIC_PROFILE_CHANGED

from stapel_notifications.models import UserNotificationSettings

logger = logging.getLogger(__name__)


class Command(BaseKafkaConsumerCommand):
    help = "Consume profile-changed events to sync notification preferences"

    topics = [TOPIC_PROFILE_CHANGED]
    consumer_group = "iron.notifications.profiles"

    def handle_event(self, event: Event):
        if event.event_type == EventType.PROFILE_CHANGED:
            self._handle_profile_changed(event.payload)
        else:
            logger.warning("Unknown event type: %s", event.event_type)

    def _handle_profile_changed(self, payload: dict):
        user_id = payload.get("user_id")
        if not user_id:
            return

        defaults = {}

        # Sync language preferences
        if "app_language" in payload:
            defaults["language"] = payload["app_language"] or None
        if "auto_detected_language" in payload:
            defaults["auto_detected_language"] = payload["auto_detected_language"] or ''

        # Sync notification preferences
        for field in ("email_messages", "email_system", "push_messages", "push_system"):
            if field in payload:
                defaults[field] = payload[field]

        if defaults:
            UserNotificationSettings.objects.update_or_create(
                user_id=user_id,
                defaults=defaults,
            )
            logger.info("Synced notification settings for user %s", user_id)
