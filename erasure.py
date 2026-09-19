"""Subject-scoped erasure — what this module removes, and how it is counted.

stapel-gdpr 0.5.0 made the subject of an erasure a parameter. This module
holds data about exactly one subject: the ``account``. Notifications are
addressed to a person, never partitioned by workspace or entity, so there is
no second subject to claim — and claiming one this module cannot erase would
be worse than claiming none, because the orchestrator would then wait for a
receipt that means nothing.

:func:`erase_account` is idempotent (delivery is at-least-once) and returns a
``counts`` dict, which is the difference between an owner saying "it ran" and
an owner saying what it did.
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

#: The name this module answers to in ``STAPEL_GDPR["DATA_OWNERS"]`` — the
#: same name the in-process provider has always registered as, so a host
#: that already declares this owner needs no settings change.
GDPR_OWNER = "notifications"

#: The subject types this module claims.
GDPR_SUBJECT_TYPES = ("account",)

#: Log columns that carry the person rather than the delivery. ``recipient``
#: and ``user_id`` are the identifiers; ``title``/``body`` quote what was
#: written TO them; ``error_message`` is a transport's reply, which routinely
#: quotes the address back ("550 no such user <...>").
_LOG_SCRUB = {
    "recipient": "",
    "user_id": None,
    "title": "",
    "body": "",
    "error_message": "",
}


@transaction.atomic
def erase_account(user_id) -> dict[str, int]:
    """Erase everything this module holds about one account.

    Two shapes, deliberately different:

    * The **addressable** rows — contact, push tokens, per-channel settings —
      are destroyed. Nothing is left that could reach the person again.
    * The **journal** is anonymised, not deleted: a delivery audit trail is
      the answer to "did this deployment mail that person about that", and a
      hole in it is not an erasure, it is a missing record. The identifiers
      and every column that quotes the person go; the fact that a
      notification of some type went out on some channel stays.

    ``NotificationDelivery`` is the exception inside that rule. Its rows are
    keyed by the raw ``recipient`` address and carry no ``user_id`` at all,
    so they cannot be anonymised — blanking the address would collide on the
    claim's uniqueness constraint. They are looked up through the contact
    that is about to be deleted, and removed. A resurrected claim cannot
    cause a re-send: the contact it addressed is gone in the same
    transaction.
    """
    from .models import (
        DevicePushToken,
        NotificationDelivery,
        NotificationLog,
        ParkedDispatch,
        UserContact,
        UserNotificationSettings,
    )

    # Read the addresses before the row that holds them is destroyed — this
    # is the only link from the person to their delivery claims.
    addresses = {
        value
        for contact in UserContact.objects.filter(user_id=user_id)
        for value in (contact.email, contact.phone, contact.telegram_chat_id)
        if value
    }
    deliveries = 0
    if addresses:
        deliveries, _ = NotificationDelivery.objects.filter(
            recipient__in=sorted(addresses),
        ).delete()

    # Deleted outright, never anonymised: a parked dispatch holds the
    # caller's raw template variables (the only thing it can be rendered
    # from later), so there is nothing in it worth keeping about a person
    # who asked to be forgotten — and an erasure that left it would leave a
    # letter queued for an address that is being destroyed in the same
    # transaction.
    parked, _ = ParkedDispatch.objects.filter(user_id=user_id).delete()
    contacts, _ = UserContact.objects.filter(user_id=user_id).delete()
    tokens, _ = DevicePushToken.objects.filter(user_id=user_id).delete()
    settings, _ = UserNotificationSettings.objects.filter(user_id=user_id).delete()
    logs = NotificationLog.objects.filter(user_id=user_id).update(**_LOG_SCRUB)

    return {
        "contacts": int(contacts),
        "push_tokens": int(tokens),
        "settings": int(settings),
        "delivery_claims": int(deliveries),
        "parked_dispatches": int(parked),
        "log_rows_anonymized": int(logs),
    }


#: subject_type -> the callable that erases it.
ERASERS = {"account": erase_account}


def erase_subject(subject_type: str, subject_key, workspace_id=None) -> dict[str, int] | None:
    """Erase one subject. ``None`` for a type this module does not claim.

    The shape :func:`stapel_core.gdpr.register_gdpr_owner` calls, and the
    contract it states: ``None`` receipts nothing, because an erasure the
    orchestrator opened no part for is not this owner's to confirm.

    ``workspace_id`` is accepted and ignored — a person's contacts, devices
    and preferences are not partitioned by workspace, so narrowing by one
    would leave their rows behind.
    """
    eraser = ERASERS.get(subject_type)
    if eraser is None:
        return None
    return eraser(subject_key)
