"""Endpoint smokes for all four API views: success + auth + validation."""

import uuid

import pytest

from stapel_notifications.models import DevicePushToken, NotificationLog
from stapel_notifications.translation_keys import NOTIFICATION_KEYS


@pytest.fixture
def staff_client(db):
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient

    staff = get_user_model().objects.create_user(
        username="staffer",
        email="staffer@example.com",
        password="x",
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=staff)
    return client


@pytest.mark.django_db
class TestDeviceTokenRegister:
    def test_requires_auth(self, api_client):
        resp = api_client.post(
            "/devices/", {"token": "t1", "platform": "ios"}, format="json"
        )
        assert resp.status_code in (401, 403)
        assert DevicePushToken.objects.count() == 0

    def test_register_returns_payload_and_persists(self, authed_client, user):
        resp = authed_client.post(
            "/devices/", {"token": "tok-a", "platform": "android"}, format="json"
        )
        assert resp.status_code == 201
        assert resp.json() == {"token": "tok-a", "platform": "android"}
        row = DevicePushToken.objects.get(token="tok-a")
        assert row.user_id == user.id
        assert row.platform == "android"
        assert row.is_active is True

    def test_invalid_platform_rejected(self, authed_client):
        resp = authed_client.post(
            "/devices/", {"token": "tok-b", "platform": "windows"}, format="json"
        )
        assert resp.status_code == 400
        assert "invalid_platform" in resp.content.decode()
        assert DevicePushToken.objects.count() == 0

    def test_missing_token_rejected(self, authed_client):
        resp = authed_client.post("/devices/", {"platform": "ios"}, format="json")
        assert resp.status_code == 400
        assert DevicePushToken.objects.count() == 0


@pytest.mark.django_db
class TestDeviceTokenDelete:
    def test_requires_auth(self, api_client, user):
        DevicePushToken.objects.create(user_id=user.id, token="tok-d", platform="ios")
        resp = api_client.delete("/devices/tok-d/")
        assert resp.status_code in (401, 403)
        assert DevicePushToken.objects.filter(token="tok-d").exists()

    def test_delete_own_token(self, authed_client, user):
        DevicePushToken.objects.create(user_id=user.id, token="tok-d", platform="ios")
        resp = authed_client.delete("/devices/tok-d/")
        assert resp.status_code == 204
        assert not DevicePushToken.objects.filter(token="tok-d").exists()

    def test_delete_unknown_token_404(self, authed_client):
        resp = authed_client.delete("/devices/no-such/")
        assert resp.status_code == 404
        assert "token_not_found" in resp.content.decode()

    def test_cannot_delete_other_users_token(self, authed_client, other_user):
        DevicePushToken.objects.create(
            user_id=other_user.id, token="tok-x", platform="ios"
        )
        resp = authed_client.delete("/devices/tok-x/")
        assert resp.status_code == 404
        assert DevicePushToken.objects.filter(token="tok-x").exists()


@pytest.mark.django_db
class TestNotificationKeys:
    def test_requires_staff_or_service(self, api_client, authed_client):
        assert api_client.get("/notification-keys/").status_code in (401, 403)
        assert authed_client.get("/notification-keys/").status_code == 403

    def test_staff_gets_all_keys(self, staff_client):
        resp = staff_client.get("/notification-keys/")
        assert resp.status_code == 200
        assert resp.json() == NOTIFICATION_KEYS


@pytest.mark.django_db
class TestNotificationFeed:
    def _log(self, user_id, *, channel="push", status="sent", **kwargs):
        defaults = {
            "notification_type": "new_message",
            "language": "en",
            "recipient": str(user_id),
            "title": "New message",
            "body": "You have mail",
            "data": {"notification_type": "new_message"},
        }
        defaults.update(kwargs)
        return NotificationLog.objects.create(
            user_id=user_id, channel=channel, status=status, **defaults
        )

    def test_requires_auth(self, api_client):
        assert api_client.get("/feed/").status_code in (401, 403)

    def test_feed_returns_only_own_sent_push_entries(
        self, authed_client, user, other_user
    ):
        mine = self._log(user.id)
        self._log(user.id, status="failed")  # excluded
        self._log(user.id, status="skipped")  # excluded
        self._log(user.id, channel="email")  # excluded
        self._log(other_user.id)  # excluded
        self._log(uuid.uuid4())  # excluded

        resp = authed_client.get("/feed/")
        assert resp.status_code == 200
        body = resp.json()
        items = body["items"]
        assert len(items) == 1
        item = items[0]
        assert item["id"] == str(mine.id)
        assert item["notification_type"] == "new_message"
        assert item["title"] == "New message"
        assert item["body"] == "You have mail"
        assert item["data"] == {"notification_type": "new_message"}
        assert item["created_at"] == mine.created_at.isoformat()

    def test_empty_feed(self, authed_client):
        resp = authed_client.get("/feed/")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
