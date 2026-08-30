"""``user.merged`` — the bell, the device and the preferences follow the person.

stapel-auth absorbs an anonymous guest into an existing account and then
DELETES the guest row. Nothing in this module is an FK — every user column is
a bare ``UUIDField`` — so the rows are not cascaded away, they are stranded,
addressed to an account that can no longer sign in. The person signs in, their
bell is empty, their device stops receiving pushes, and the preferences they
set moments earlier are gone. What is pinned here:

* the feed history and the registered devices are re-parented;
* the two tables with ``user_id`` as their PRIMARY KEY cannot be blindly
  updated, and each has its own documented answer: the survivor's settings
  win, and the guest's contact is dropped because that table is a projection
  of auth, not this module's data;
* the delivery ledger is keyed by address and is deliberately untouched, so a
  merge can neither lose a claim nor cause a second send;
* the handler is idempotent, and a no-op for ids it has never seen;
* a malformed id does not raise — ``UUIDField`` answers ``"not-a-uuid"`` with
  ``ValidationError``, which is NOT a ``ValueError``, and an escaping
  exception is a poison pill on an at-least-once bus.
"""
import types
import uuid

import pytest

from stapel_notifications.actions import handle_user_merged
from stapel_notifications.models import (
    DevicePushToken,
    NotificationDelivery,
    NotificationLog,
    UserContact,
    UserNotificationSettings,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def guest():
    return uuid.uuid4()


@pytest.fixture
def survivor():
    return uuid.uuid4()


def _event(from_user_id, into_user_id, event_id="evt-merge"):
    return types.SimpleNamespace(
        payload={
            "from_user_id": str(from_user_id),
            "into_user_id": str(into_user_id),
            "reason": "anonymous_promotion",
        },
        event_id=event_id,
    )


def _merge(from_user_id, into_user_id):
    handle_user_merged(_event(from_user_id, into_user_id))


def _log(user_id, notification_type="listing_published"):
    return NotificationLog.objects.create(
        user_id=user_id,
        notification_type=notification_type,
        channel="push",
        status="sent",
        recipient="device",
        title="Your listing is live",
    )


def _token(user_id, token=None):
    return DevicePushToken.objects.create(
        user_id=user_id, token=token or uuid.uuid4().hex, platform="ios"
    )


# ── what moves ──────────────────────────────────────────────────────────


def test_the_feed_follows_the_person(guest, survivor):
    mine = _log(guest)
    theirs = _log(uuid.uuid4())

    _merge(guest, survivor)

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.user_id == survivor
    assert theirs.user_id != survivor
    assert not NotificationLog.objects.filter(user_id=guest).exists()


def test_unread_rows_stay_unread_after_the_move(guest, survivor):
    """The badge is the person's attention, and a merge is not a read."""
    row = _log(guest)

    _merge(guest, survivor)

    row.refresh_from_db()
    assert row.user_id == survivor
    assert row.read_at is None
    assert NotificationLog.objects.filter(
        user_id=survivor, read_at__isnull=True
    ).count() == 1


def test_the_device_follows_the_person(guest, survivor):
    """The same physical handset is now the survivor's."""
    token = _token(guest)

    _merge(guest, survivor)

    token.refresh_from_db()
    assert token.user_id == survivor
    assert token.is_active is True


def test_a_deactivated_token_moves_too(guest, survivor):
    """``is_active=False`` is a grace-period state, not a change of owner."""
    token = _token(guest)
    DevicePushToken.objects.filter(pk=token.pk).update(is_active=False)

    _merge(guest, survivor)

    token.refresh_from_db()
    assert token.user_id == survivor
    assert token.is_active is False


# ── the two primary-key collisions ──────────────────────────────────────


def test_the_survivors_settings_win_and_the_guest_row_is_dropped(guest, survivor):
    """``user_id`` is the PRIMARY KEY: one row per person, and a merge is the
    case where both have one. An account's settled choices are not overwritten
    by a guest session's."""
    UserNotificationSettings.objects.create(user_id=survivor, email_messages=False)
    UserNotificationSettings.objects.create(user_id=guest, email_messages=True)

    _merge(guest, survivor)

    assert UserNotificationSettings.objects.count() == 1
    kept = UserNotificationSettings.objects.get(user_id=survivor)
    assert kept.email_messages is False
    assert not UserNotificationSettings.objects.filter(user_id=guest).exists()


def test_guest_settings_are_carried_when_the_survivor_has_none(guest, survivor):
    """Nothing to lose and something to keep: the guest's row is the person's
    most recent explicit choice, and the alternative is silently reverting
    them to defaults."""
    UserNotificationSettings.objects.create(
        user_id=guest, push_messages=False, channel_preferences={"webhook_system": False}
    )

    _merge(guest, survivor)

    assert UserNotificationSettings.objects.count() == 1
    kept = UserNotificationSettings.objects.get(user_id=survivor)
    assert kept.push_messages is False
    assert kept.channel_preferences == {"webhook_system": False}


def test_the_guest_contact_is_dropped_never_carried(guest, survivor):
    """``UserContact`` is a projection of auth. Filing a guest's address under
    the survivor's id would then WRITE to it — a person's notifications sent
    somewhere they do not own. A projection is repaired by its source."""
    UserContact.objects.create(user_id=guest, email="guest@example.com")

    _merge(guest, survivor)

    assert not UserContact.objects.filter(user_id=guest).exists()
    assert not UserContact.objects.filter(user_id=survivor).exists()


def test_the_survivors_contact_is_left_alone(guest, survivor):
    UserContact.objects.create(user_id=guest, email="guest@example.com")
    UserContact.objects.create(user_id=survivor, email="real@example.com")

    _merge(guest, survivor)

    assert UserContact.objects.count() == 1
    assert UserContact.objects.get(user_id=survivor).email == "real@example.com"


def test_the_delivery_ledger_is_untouched(guest, survivor):
    """Claims are keyed by (event_id, channel, recipient, template_version) —
    the address, which a merge does not change. Moving or dropping one would
    either lose the idempotency key or cause a second send."""
    claim = NotificationDelivery.objects.create(
        event_id="e-1",
        channel="email",
        recipient="guest@example.com",
        template_version="v1",
        state=NotificationDelivery.DELIVERED,
    )

    _merge(guest, survivor)

    claim.refresh_from_db()
    assert claim.state == NotificationDelivery.DELIVERED
    assert NotificationDelivery.objects.count() == 1


# ── idempotency and the quiet paths ─────────────────────────────────────


def test_second_delivery_changes_nothing(guest, survivor):
    row = _log(guest)
    token = _token(guest)
    UserNotificationSettings.objects.create(user_id=guest, email_messages=False)

    _merge(guest, survivor)
    _merge(guest, survivor)  # at-least-once delivery

    row.refresh_from_db()
    token.refresh_from_db()
    assert row.user_id == survivor
    assert token.user_id == survivor
    assert NotificationLog.objects.count() == 1
    assert DevicePushToken.objects.count() == 1
    assert UserNotificationSettings.objects.count() == 1
    assert UserNotificationSettings.objects.get(user_id=survivor).email_messages is False


def test_a_guest_with_nothing_here_is_a_clean_no_op(guest, survivor):
    _merge(guest, survivor)
    assert NotificationLog.objects.count() == 0
    assert UserNotificationSettings.objects.count() == 0


def test_an_event_naming_users_this_module_has_no_rows_for_does_nothing(
    guest, survivor
):
    row = _log(guest)

    _merge(uuid.uuid4(), uuid.uuid4())

    row.refresh_from_db()
    assert row.user_id == guest


def test_merge_into_self_is_a_no_op(guest):
    row = _log(guest)
    settings_row = UserNotificationSettings.objects.create(user_id=guest)

    _merge(guest, guest)

    row.refresh_from_db()
    settings_row.refresh_from_db()
    assert row.user_id == guest
    assert UserNotificationSettings.objects.filter(user_id=guest).exists()


def test_a_survivor_this_module_has_never_seen_needs_no_retry(guest):
    """Every user column here is a bare ``UUIDField``, not an FK: nothing has
    to exist before the id can be written, so there is no ordering lag to
    raise about."""
    row = _log(guest)
    never_seen = uuid.uuid4()

    _merge(guest, never_seen)

    row.refresh_from_db()
    assert row.user_id == never_seen


# ── malformed and missing payloads ──────────────────────────────────────


def test_missing_ids_are_reported_and_ignored(guest, survivor):
    row = _log(guest)

    handle_user_merged(
        types.SimpleNamespace(payload={"into_user_id": str(survivor)}, event_id="e1")
    )
    handle_user_merged(
        types.SimpleNamespace(payload={"from_user_id": str(guest)}, event_id="e2")
    )
    handle_user_merged(types.SimpleNamespace(payload={}, event_id="e3"))

    row.refresh_from_db()
    assert row.user_id == guest


def test_a_malformed_id_does_not_raise_and_moves_nothing(guest, survivor):
    """A ``UUIDField`` answers ``"not-a-uuid"`` with ``ValidationError``,
    which is NOT a ``ValueError``. Catching only ``ValueError`` here would
    make every such payload a poison pill the bus redelivers forever."""
    row = _log(guest)

    handle_user_merged(_event("not-a-uuid", survivor))
    handle_user_merged(_event(guest, "not-a-uuid"))

    row.refresh_from_db()
    assert row.user_id == guest


def test_a_malformed_into_id_leaves_the_first_writes_rolled_back(guest, survivor):
    """The whole transfer is one transaction, so a payload that only fails on
    the second table must not leave the first one half-moved."""
    _log(guest)
    _token(guest)

    handle_user_merged(_event(guest, "not-a-uuid"))

    assert NotificationLog.objects.filter(user_id=guest).count() == 1
    assert DevicePushToken.objects.filter(user_id=guest).count() == 1


# ── wiring ──────────────────────────────────────────────────────────────


def test_the_subscription_is_registered():
    from stapel_core.comm import action_registry

    assert handle_user_merged in action_registry.handlers("user.merged")


def test_the_lifecycle_pair_check_is_green():
    """``stapel_core.lifecycle.E001`` — one half of an account's life cycle
    answered and not the other is an ERROR as of core 0.52.x."""
    from stapel_core.comm.lifecycle_checks import check_lifecycle_pairs

    assert check_lifecycle_pairs() == []


def test_the_consumes_schema_is_committed():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "schemas" / "consumes" / "user.merged.json"
    )
    schema = json.loads(path.read_text())
    assert schema["title"] == "user.merged"
    assert set(schema["required"]) == {"from_user_id", "into_user_id"}


def test_every_user_column_in_this_module_has_an_answer():
    """The handler names four models. A fifth user column would be silently
    stranded — fail here, not in production."""
    from django.apps import apps

    answered = {
        "NotificationLog.user_id",
        "DevicePushToken.user_id",
        "UserNotificationSettings.user_id",
        "UserContact.user_id",
    }
    found = {
        f"{model.__name__}.{field.name}"
        for model in apps.get_app_config("notifications").get_models()
        for field in model._meta.get_fields()
        if getattr(field, "name", "") == "user_id"
    }
    assert found == answered
