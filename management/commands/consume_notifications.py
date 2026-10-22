"""
Kafka consumer for notification request events.

Processes notification.requested events from source services
and dispatches to appropriate channels.
"""

import logging
import os

from stapel_core.bus import BaseBusConsumerCommand as BaseKafkaConsumerCommand, Event
from stapel_core.kafka.topics import TOPIC_NOTIFICATION_REQUESTED

from stapel_notifications.intake import handle_notification_requested

logger = logging.getLogger(__name__)


class Command(BaseKafkaConsumerCommand):
    help = "Consume notification request events from Kafka"

    topics = [TOPIC_NOTIFICATION_REQUESTED]
    consumer_group = os.getenv("NOTIFICATIONS_CONSUMER_GROUP", "stapel.notifications.processor")  # noqa: CFG001

    def handle_event(self, event: Event):
        # One handler for the broker command and the in-process subscriber.
        handle_notification_requested(event)
