"""The feed's emit half: what goes on the wire, and when.

Substrate-free on purpose — ``stapel_core.comm.signal()`` is the emitter and
it is stdlib, so these tests run on a bare install with no stapel-realtime and
no Channels. The serving half lives in ``tests/test_feed_stream.py``, which
skips without the optional extra.

``transaction=True`` throughout: ``signal()`` defers delivery to
``transaction.on_commit``, so inside pytest-django's default wrapping
transaction (which never commits) nothing would ever be emitted — and a test
asserting "no frame" under those conditions would pass for the wrong reason.
"""

import dataclasses
import uuid

import pytest
from django.test import override_settings
from django.utils import timezone

from stapel_notifications import realtime
from stapel_notifications.dto import FeedItemResponse
from stapel_notifications.models import NotificationLog, UserContact
from stapel_notifications.services import process_notification


class _Capture(list):
    """The frames a fake transport saw, as ``(stream_key, frame)`` pairs."""

    def transport(self, stream_key, frame):
        self.append((stream_key, frame))


@pytest.fixture
def captured():
    return _Capture()


def _comm(transport):
    return {
        "OUTBOX_ENABLED": False,
        "ACTION_TRANSPORT": "inprocess",
        "SIGNAL_TRANSPORT": transport,
    }


# ── the stream key and the payload ──────────────────────────────────────


def test_stream_key_is_the_canonical_shape():
    user_id = uuid.uuid4()
    assert realtime.user_stream(user_id) == f"notifications:user:{user_id}"


def test_signal_type_is_not_a_protocol_frame_type():
    """A signal that claims a protocol name is read as protocol by consumers."""
    from stapel_core.comm.signals import RESERVED_FRAME_TYPES

    assert realtime.SIGNAL_NEW not in RESERVED_FRAME_TYPES


def test_payload_is_field_for_field_the_rest_feed_item():
    """One notification type on the client, whether it arrived by socket or
    by page — a frame with a shape of its own is how a client grows two."""
    entry = NotificationLog(
        user_id=uuid.uuid4(),
        notification_type="listing_blocked",
        channel="push",
        status="sent",
        title="Blocked",
        body="Your listing was blocked",
        data={"deep_link": "/listings/1"},
    )
    entry.created_at = timezone.now()
    payload = realtime.feed_item_payload(entry)
    assert set(payload) == {f.name for f in dataclasses.fields(FeedItemResponse)}
    assert payload["data"] == {"deep_link": "/listings/1"}
    assert payload["created_at"] == entry.created_at.isoformat()


# ── the emit, from the real dispatch pipeline ───────────────────────────


@pytest.mark.django_db(transaction=True)
def test_a_sent_push_signals_the_recipients_stream(user, captured):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(
        STAPEL_COMM=_comm(captured.transport),
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"},
    ):
        process_notification(
            notification_type="report_reviewed",  # system: push + email
            user_id=str(user.id),
            variables={},
        )

    streams = [key for key, _ in captured]
    assert streams == [realtime.user_stream(user.id)], (
        "exactly one frame, on the recipient's own stream"
    )
    frame = captured[0][1]
    assert frame["v"] == 1
    assert frame["type"] == realtime.SIGNAL_NEW
    assert "seq" not in frame, "the feed stream is ephemeral, not a journal"

    row = NotificationLog.objects.get(user_id=user.id, channel="push")
    assert frame["payload"]["id"] == str(row.id)
    assert frame["payload"]["notification_type"] == "report_reviewed"


@pytest.mark.django_db(transaction=True)
def test_only_the_push_row_signals(user, captured):
    """The feed IS the push journal — an email row is not a feed item."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(
        STAPEL_COMM=_comm(captured.transport),
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"},
    ):
        process_notification(
            notification_type="new_device_login",  # auth: email only
            user_id=str(user.id),
            variables={},
        )

    assert NotificationLog.objects.filter(channel="email", status="sent").exists()
    assert captured == []


@pytest.mark.django_db(transaction=True)
def test_a_skipped_push_signals_nothing(user, captured):
    """The recipient turned push off. Nothing was sent, so nothing arrived."""
    from stapel_notifications.models import UserNotificationSettings

    UserNotificationSettings.objects.create(user_id=user.id, push_system=False)
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(
        STAPEL_COMM=_comm(captured.transport),
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"},
    ):
        process_notification(
            notification_type="report_reviewed", user_id=str(user.id), variables={}
        )

    assert NotificationLog.objects.get(user_id=user.id, channel="push").status == "skipped"
    assert captured == []


@pytest.mark.django_db(transaction=True)
def test_the_row_survives_a_transport_that_explodes(user):
    """Best-effort means best-effort: the notification was already delivered
    and logged before anybody tried to tell a screen about it."""

    def exploding(stream_key, frame):
        raise RuntimeError("redis is on fire")

    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(
        STAPEL_COMM=_comm(exploding),
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"},
    ):
        process_notification(
            notification_type="report_reviewed", user_id=str(user.id), variables={}
        )

    assert NotificationLog.objects.get(user_id=user.id, channel="push").status == "sent"


@pytest.mark.django_db(transaction=True)
def test_no_transport_configured_is_a_silent_no_op(user):
    """The default host: HTTP only, no signal transport, nothing breaks."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(
        STAPEL_COMM={"OUTBOX_ENABLED": False, "ACTION_TRANSPORT": "inprocess"},
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"},
    ):
        process_notification(
            notification_type="report_reviewed", user_id=str(user.id), variables={}
        )

    assert NotificationLog.objects.get(user_id=user.id, channel="push").status == "sent"


# ── the read half: the badge a sibling screen has to correct ────────────


def _read(client, payload):
    return client.post("/feed/read/", payload, format="json")


def _push_row(user_id, **kwargs):
    defaults = {
        "notification_type": "new_message",
        "channel": "push",
        "status": "sent",
        "language": "en",
        "recipient": str(user_id),
        "title": "New message",
        "body": "You have mail",
    }
    defaults.update(kwargs)
    return NotificationLog.objects.create(user_id=user_id, **defaults)


def test_the_read_signal_type_is_not_a_protocol_frame_type():
    from stapel_core.comm.signals import RESERVED_FRAME_TYPES

    assert realtime.SIGNAL_READ not in RESERVED_FRAME_TYPES


def test_read_payload_carries_the_ids_and_the_new_count():
    ids = [uuid.uuid4(), uuid.uuid4()]

    payload = realtime.feed_read_payload(ids, False, 3)

    assert payload == {"ids": [str(i) for i in ids], "all": False, "unread_count": 3}


def test_read_payload_of_a_clear_all_carries_no_id_list():
    """A frame the size of somebody's whole history is not a courtesy."""
    payload = realtime.feed_read_payload([], True, 0)

    assert payload == {"ids": [], "all": True, "unread_count": 0}


@pytest.mark.django_db(transaction=True)
def test_marking_ids_read_signals_the_recipients_stream(authed_client, user, captured):
    row = _push_row(user.id)
    _push_row(user.id)  # left unread, so the count that travels is not 0

    with override_settings(STAPEL_COMM=_comm(captured.transport)):
        assert _read(authed_client, {"ids": [str(row.id)]}).status_code == 200

    assert [key for key, _ in captured] == [realtime.user_stream(user.id)]
    frame = captured[0][1]
    assert frame["v"] == 1
    assert frame["type"] == realtime.SIGNAL_READ
    assert "seq" not in frame
    assert frame["payload"] == {
        "ids": [str(row.id)],
        "all": False,
        "unread_count": 1,
    }


@pytest.mark.django_db(transaction=True)
def test_marking_all_read_signals_all_not_a_list(authed_client, user, captured):
    _push_row(user.id)
    _push_row(user.id)

    with override_settings(STAPEL_COMM=_comm(captured.transport)):
        assert _read(authed_client, {"all": True}).status_code == 200

    assert captured[0][1]["payload"] == {"ids": [], "all": True, "unread_count": 0}


@pytest.mark.django_db(transaction=True)
def test_a_read_that_changes_nothing_signals_nothing(authed_client, user, captured):
    """Idempotent on the wire too — a no-op frame is how two tabs start
    correcting each other forever."""
    row = _push_row(user.id)
    payload = {"ids": [str(row.id)]}

    with override_settings(STAPEL_COMM=_comm(captured.transport)):
        _read(authed_client, payload)
        captured.clear()
        assert _read(authed_client, payload).json()["marked"] == 0

    assert captured == []


@pytest.mark.django_db(transaction=True)
def test_a_rejected_read_signals_nothing(authed_client, user, captured):
    _push_row(user.id)

    with override_settings(STAPEL_COMM=_comm(captured.transport)):
        assert _read(authed_client, {}).status_code == 400

    assert captured == []


@pytest.mark.django_db(transaction=True)
def test_the_read_survives_a_transport_that_explodes(authed_client, user):
    """Best-effort: the columns are the truth, the frame is the courtesy."""

    def exploding(stream_key, frame):
        raise RuntimeError("redis is on fire")

    row = _push_row(user.id)
    with override_settings(STAPEL_COMM=_comm(exploding)):
        assert _read(authed_client, {"ids": [str(row.id)]}).json()["marked"] == 1

    row.refresh_from_db()
    assert row.read_at is not None
