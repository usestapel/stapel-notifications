"""N4 — the push payload is a declared contract, and the sender conforms to it.

``docs/push.json`` is what a client generates its types from; these tests
validate what the mock sender actually captured against that committed file,
so a sender change the contract does not describe fails here.
"""
import json
import uuid
from pathlib import Path

import jsonschema
import pytest
from django.test import override_settings

from stapel_notifications.channels import push as push_channel
from stapel_notifications.models import DevicePushToken, NotificationLog
from stapel_notifications.services import process_notification

pytestmark = pytest.mark.django_db

CONTRACT = json.loads(
    (Path(__file__).resolve().parent.parent / "docs" / "push.json").read_text()
)
MOCK = {"PUSH_PROVIDER": "mock"}


@pytest.fixture
def outbox():
    push_channel.mock_outbox.clear()
    yield push_channel.mock_outbox
    push_channel.mock_outbox.clear()


@pytest.fixture
def device(user):
    return DevicePushToken.objects.create(user_id=user.id, token="tok-1", platform="ios")


def _send(user, notification_type="new_message", **variables):
    with override_settings(STAPEL_NOTIFICATIONS=MOCK):
        process_notification(
            notification_type=notification_type, user_id=str(user.id), variables=variables,
        )


def _validate(message):
    errors = sorted(
        jsonschema.Draft202012Validator(
            CONTRACT, format_checker=jsonschema.FormatChecker()
        ).iter_errors(message),
        key=lambda e: list(e.path),
    )
    assert not errors, "\n".join(f"at {list(e.path)}: {e.message}" for e in errors)


def test_the_captured_message_conforms_to_the_committed_contract(user, device, outbox):
    _send(user, chat_url="https://shop.example/chat/42")
    assert len(outbox) == 1
    _validate(outbox[0]["message"])


def test_every_contract_field_is_filled(user, device, outbox):
    _send(user, chat_url="https://shop.example/chat/42")
    message = outbox[0]["message"]
    data = message["data"]
    feed_row = NotificationLog.objects.get(channel="push", status="sent")
    assert data["notification_id"] == str(feed_row.id)
    assert data["notification_type"] == "new_message"
    assert data["group"] == "messages"
    assert data["url"] == "https://shop.example/chat/42"
    assert data["chat_url"] == "https://shop.example/chat/42"
    assert data["v"] == "1"
    assert message["android"] == {"channel_id": "messages"}
    assert message["apns"]["thread_id"] == "messages"


def test_the_notification_id_is_the_feed_item_a_tap_marks_read(user, device, outbox, authed_client):
    _send(user)
    notification_id = outbox[0]["message"]["data"]["notification_id"]
    feed_ids = [item["id"] for item in authed_client.get("/feed/").json()["items"]]
    assert feed_ids == [notification_id]
    response = authed_client.post(
        "/feed/read/", {"ids": [notification_id]}, format="json",
    )
    assert response.json()["marked"] == 1


def test_the_badge_counts_unread_rows_including_this_one(user, device, outbox):
    _send(user)
    _send(user)
    assert [e["message"]["apns"]["badge"] for e in outbox] == [1, 2]
    assert [e["message"]["data"]["badge"] for e in outbox] == ["1", "2"]


def test_no_link_means_no_url(user, device, outbox):
    _send(user)
    assert "url" not in outbox[0]["message"]["data"]
    _validate(outbox[0]["message"])


def test_push_url_wins_and_routing_can_name_the_link(user, device, outbox):
    types = {"order_ready": {"channels": ["push"], "group": "system", "link": "order_url"}}
    with override_settings(STAPEL_NOTIFICATIONS={**MOCK, "TYPES": types}):
        process_notification(
            notification_type="order_ready", user_id=str(user.id),
            variables={"order_url": "https://shop.example/o/1"},
        )
        process_notification(
            notification_type="order_ready", user_id=str(user.id),
            variables={"order_url": "https://shop.example/o/1", "push_url": "app://o/1"},
        )
    assert [e["message"]["data"]["url"] for e in outbox] == ["https://shop.example/o/1", "app://o/1"]


def test_inversion_a_payload_without_the_feed_id_is_refused():
    """The gate is alive: the contract rejects what the sender sent before N4."""
    legacy = {
        "title": "t", "body": "b",
        "data": {"notification_type": "new_message"},
        "android": {"channel_id": "messages"},
        "apns": {"badge": 0, "sound": "default", "thread_id": "messages"},
    }
    with pytest.raises(AssertionError):
        _validate(legacy)


def test_the_fcm_message_is_built_from_the_same_payload(user, device, monkeypatch):
    """The real provider sends the composed payload field for field."""
    messaging = pytest.importorskip("firebase_admin.messaging")
    sent = []
    monkeypatch.setattr(push_channel, "_ensure_firebase", lambda: True)
    monkeypatch.setattr(messaging, "send", lambda message: sent.append(message))
    data = {
        "v": "1", "notification_id": str(uuid.uuid4()), "notification_type": "new_message",
        "group": "messages", "badge": "3", "url": "https://shop.example/c/1",
    }
    assert push_channel._FCMPushProvider().send(str(user.id), "T", "B", data) == 1
    message = sent[0]
    assert message.data == data
    assert message.android.notification.channel_id == "messages"
    assert message.apns.payload.aps.badge == 3
    assert message.apns.payload.aps.thread_id == "messages"
