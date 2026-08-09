"""
Kafka consumer for profile-changed events.

Syncs user notification preferences and language from profiles service.
"""

import logging
import os

from stapel_core.bus import BaseBusConsumerCommand as BaseKafkaConsumerCommand, Event
from stapel_core.kafka.events import EventType
from stapel_core.kafka.topics import TOPIC_PROFILE_CHANGED

from stapel_notifications.models import UserNotificationSettings

logger = logging.getLogger(__name__)


class Command(BaseKafkaConsumerCommand):
    help = "Consume profile-changed events to sync notification preferences"

    topics = [TOPIC_PROFILE_CHANGED]
    consumer_group = os.getenv("NOTIFICATIONS_CONSUMER_GROUP_PROFILES", "stapel.notifications.profiles")  # noqa: CFG001

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

        # The recipient's LANGUAGE is deliberately not synced here any more.
        # It is asked of profiles at send time (``profiles.language``, see
        # language.py): this consumer cannot run at all on an in-process bus,
        # so in a monolith the mirror it fed stayed empty and every recipient
        # was written to in the sender's language. The channel preferences
        # below are the same shape and have the same exposure — they are the
        # next thing to move onto the comm plane.

        # Sync notification preferences
        for field in (
            "email_messages",
            "email_system",
            "push_messages",
            "push_system",
            "sms_messages",
            "sms_system",
        ):
            if field in payload:
                defaults[field] = payload[field]

        if defaults:
            UserNotificationSettings.objects.update_or_create(
                user_id=user_id,
                defaults=defaults,
            )
            logger.info("Synced notification settings for user %s", user_id)
