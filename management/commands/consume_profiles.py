"""
Kafka consumer for profile-changed events.

Syncs user notification preferences and language from profiles service.
"""

import logging
import os

from stapel_core.bus import BaseBusConsumerCommand as BaseKafkaConsumerCommand, Event
from stapel_core.kafka.events import EventType
from stapel_core.kafka.topics import TOPIC_PROFILE_CHANGED

from stapel_notifications.intake import sync_profile_preferences

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
        # Same function as the ``profile.changed`` comm action (actions.py).
        sync_profile_preferences(payload)
