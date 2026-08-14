"""Delivery claims: at-least-once in, exactly-once out.

The three verbs ``services`` uses around a dispatch. The rule they implement
is the database's unique constraint on
``(event_id, channel, recipient, template_version)`` — see
``models.NotificationDelivery`` for why a ``.exists()`` check was not one.
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .conf import notifications_settings
from .models import NotificationDelivery

logger = logging.getLogger(__name__)


def claim(event_id: str | None, channel: str, recipient: str, template_version: str) -> bool:
    """Take the right to deliver this, or report that somebody else has it.

    A caller without an ``event_id`` gets True: there is no idempotency key
    to deduplicate on, so a direct ``process_notification`` call keeps
    behaving as it always has (the bus consumer always passes one).
    """
    if not event_id:
        return True

    key = {
        "event_id": event_id,
        "channel": channel,
        "recipient": recipient,
        "template_version": template_version,
    }

    for _attempt in range(2):
        try:
            # Wrapped: an IntegrityError marks the surrounding atomic block
            # as broken, and this runs inside the host's transaction.
            with transaction.atomic():
                NotificationDelivery.objects.create(**key)
            return True
        except IntegrityError:
            pass

        held = NotificationDelivery.objects.filter(**key).first()
        if held is None:
            continue  # released between the create and the read — try again
        if held.state == NotificationDelivery.DELIVERED:
            return False
        if timezone.now() - held.created_at < _claim_ttl():
            return False  # another worker is sending this right now
        # The holder died between claiming and sending. Take the slot over —
        # a crashed consumer must not silence a notification forever. The
        # filter on state is what keeps this safe under a race: whoever
        # deletes the stale row is the only one that gets to re-create it.
        deleted, _ = NotificationDelivery.objects.filter(
            pk=held.pk, state=NotificationDelivery.CLAIMED
        ).delete()
        if not deleted:
            return False
        logger.warning(
            "Reclaimed a stale delivery claim: event_id=%s channel=%s "
            "(older than %s)", event_id, channel, _claim_ttl(),
        )
    return False


def confirm(event_id: str | None, channel: str, recipient: str, template_version: str) -> None:
    """The provider took it: this claim now suppresses duplicates for good."""
    if not event_id:
        return
    NotificationDelivery.objects.filter(
        event_id=event_id, channel=channel, recipient=recipient,
        template_version=template_version,
    ).update(state=NotificationDelivery.DELIVERED)


def release(event_id: str | None, channel: str, recipient: str, template_version: str) -> None:
    """Nothing was delivered — let a redelivery of this event try again.

    Only a claim is released; a row already marked delivered is left alone,
    so a late failure cannot resurrect a duplicate send.
    """
    if not event_id:
        return
    NotificationDelivery.objects.filter(
        event_id=event_id, channel=channel, recipient=recipient,
        template_version=template_version, state=NotificationDelivery.CLAIMED,
    ).delete()


def _claim_ttl():
    from datetime import timedelta

    try:
        seconds = int(notifications_settings.DELIVERY_CLAIM_TTL)
    except (TypeError, ValueError):
        seconds = 900
    return timedelta(seconds=max(seconds, 0))
