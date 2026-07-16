"""
Kafka consumer for user-contact-changed events.

Syncs user email and phone from auth service.
"""

import logging
import os

from stapel_core.bus import BaseBusConsumerCommand as BaseKafkaConsumerCommand, Event
from stapel_core.kafka.events import EventType
from stapel_core.kafka.topics import TOPIC_USER_CONTACT_CHANGED

from stapel_notifications.models import UserContact

logger = logging.getLogger(__name__)


class Command(BaseKafkaConsumerCommand):
    help = "Consume user-contact-changed events to sync email/phone"

    topics = [TOPIC_USER_CONTACT_CHANGED]
    consumer_group = os.getenv("NOTIFICATIONS_CONSUMER_GROUP_CONTACTS", "stapel.notifications.contacts")  # noqa: CFG001

    def handle_event(self, event: Event):
        if event.event_type == EventType.USER_CONTACT_CHANGED:
            self._handle_contact_changed(event.payload)
        else:
            logger.warning("Unknown event type: %s", event.event_type)

    def _handle_contact_changed(self, payload: dict):
        user_id = payload.get("user_id")
        if not user_id:
            return

        defaults = {}
        if "email" in payload:
            defaults["email"] = payload["email"] or ""
        if "phone" in payload:
            defaults["phone"] = payload["phone"] or ""

        if defaults:
            # A fresh contact sync reactivates a contact that was soft-
            # deactivated during an account-closure grace period.
            defaults["is_active"] = True
            UserContact.objects.update_or_create(
                user_id=user_id,
                defaults=defaults,
            )
            logger.info("Synced contact info for user %s", user_id)
