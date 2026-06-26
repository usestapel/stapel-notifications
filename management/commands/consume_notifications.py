"""
Kafka consumer for notification request events.

Processes notification.requested events from source services
and dispatches to appropriate channels.
"""

import logging

from stapel_core.bus import BaseBusConsumerCommand as BaseKafkaConsumerCommand, Event
from stapel_core.kafka.events import EventType
from stapel_core.kafka.topics import TOPIC_NOTIFICATION_REQUESTED

from stapel_notifications.services import process_notification

logger = logging.getLogger(__name__)


class Command(BaseKafkaConsumerCommand):
    help = "Consume notification request events from Kafka"

    topics = [TOPIC_NOTIFICATION_REQUESTED]
    consumer_group = "iron.notifications.processor"

    def handle_event(self, event: Event):
        if event.event_type == EventType.NOTIFICATION_REQUESTED:
            self._handle_notification(event.payload, event.event_id)
        else:
            logger.warning("Unknown event type: %s (event_id=%s)", event.event_type, event.event_id)

    def _handle_notification(self, payload: dict, event_id: str):
        notification_type = payload.get("notification_type")
        if not notification_type:
            logger.error("Notification event missing notification_type (event_id=%s)", event_id)
            return

        logger.info("Processing notification: type=%s, event_id=%s", notification_type, event_id)

        process_notification(
            notification_type=notification_type,
            user_id=payload.get("user_id"),
            variables=payload.get("variables", {}),
            email=payload.get("email"),
            phone=payload.get("phone"),
            language=payload.get("language"),
            event_id=event_id,
        )
