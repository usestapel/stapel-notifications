"""
Kafka consumer for translations-changed events.

Syncs notification translation keys from translate service.
"""

import logging

from stapel_core.bus import BaseBusConsumerCommand as BaseKafkaConsumerCommand, Event
from stapel_core.kafka.events import EventType
from stapel_core.kafka.topics import TOPIC_TRANSLATIONS_CHANGED

from stapel_notifications.models import TranslationCache

logger = logging.getLogger(__name__)


class Command(BaseKafkaConsumerCommand):
    help = "Consume translations-changed events to sync notification translations"

    topics = [TOPIC_TRANSLATIONS_CHANGED]
    consumer_group = "iron.notifications.translations"

    def handle_event(self, event: Event):
        if event.event_type == EventType.TRANSLATIONS_CHANGED:
            self._handle_translation_changed(event.payload)
        else:
            logger.warning("Unknown event type: %s", event.event_type)

    def _handle_translation_changed(self, payload: dict):
        key = payload.get("key")
        values = payload.get("values")

        if not key or not isinstance(values, dict):
            logger.warning("Invalid translations-changed payload: %s", payload)
            return

        # Only cache notification-related keys
        if not key.startswith("notification."):
            return

        TranslationCache.objects.update_or_create(
            key=key,
            defaults={"values": values},
        )
        logger.info("Synced translation key: %s", key)
