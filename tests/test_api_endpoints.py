"""Endpoint smokes for every API view: success + auth + validation."""

import hashlib
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
class TestDeviceList:
    """GET /devices/ — the read that lets a push toggle know its own state."""

    def test_requires_auth(self, api_client):
        assert api_client.get("/devices/").status_code in (401, 403)

    def test_lists_only_the_callers_devices(self, authed_client, user, other_user):
        DevicePushToken.objects.create(user_id=user.id, token="mine", platform="ios")
        DevicePushToken.objects.create(
            user_id=other_user.id, token="theirs", platform="web"
        )
        resp = authed_client.get("/devices/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["platform"] == "ios"

    def test_never_echoes_the_raw_token(self, authed_client, user):
        DevicePushToken.objects.create(
            user_id=user.id, token="super-secret-token", platform="android"
        )
        resp = authed_client.get("/devices/")
        assert "super-secret-token" not in resp.content.decode()

    def test_fingerprint_lets_a_client_find_its_own_device(self, authed_client, user):
        """The whole point: this browser holds the token, not the id."""
        DevicePushToken.objects.create(
            user_id=user.id, token="tok-fp", platform="web"
        )
        mine = hashlib.sha256(b"tok-fp").hexdigest()
        row = authed_client.get("/devices/").json()[0]
        assert row["token_fingerprint"] == mine

    def test_inactive_devices_are_listed_and_flagged(self, authed_client, user):
        """A token the provider rejected is registered but undelivered-to —
        hiding it would make a toggle render ON for a dead device."""
        DevicePushToken.objects.create(
            user_id=user.id, token="dead", platform="ios", is_active=False
        )
        row = authed_client.get("/devices/").json()[0]
        assert row["is_active"] is False

    def test_row_carries_id_timestamps_and_platform(self, authed_client, user):
        device = DevicePushToken.objects.create(
            user_id=user.id, token="tok-full", platform="android"
        )
        row = authed_client.get("/devices/").json()[0]
        assert row["id"] == device.pk
        assert row["created_at"] == device.created_at.isoformat()
        assert row["last_seen"] == device.updated_at.isoformat()
        assert row["platform"] == "android"

    def test_a_reregistration_moves_last_seen(self, authed_client, user):
        device = DevicePushToken.objects.create(
            user_id=user.id, token="tok-seen", platform="ios"
        )
        before = authed_client.get("/devices/").json()[0]["last_seen"]
        authed_client.post(
            "/devices/", {"token": "tok-seen", "platform": "ios"}, format="json"
        )
        after = authed_client.get("/devices/").json()[0]["last_seen"]
        assert after >= before
        device.refresh_from_db()
        assert after == device.updated_at.isoformat()

    def test_empty_for_a_caller_with_no_devices(self, authed_client):
        assert authed_client.get("/devices/").json() == []


@pytest.mark.django_db
class TestDeviceUnregisterById:
    """DELETE /devices/by-id/<id>/ — unregister a row read from the list."""

    def test_requires_auth(self, api_client, user):
        device = DevicePushToken.objects.create(
            user_id=user.id, token="tok-i", platform="ios"
        )
        assert api_client.delete(f"/devices/by-id/{device.pk}/").status_code in (
            401,
            403,
        )
        assert DevicePushToken.objects.filter(pk=device.pk).exists()

    def test_deletes_own_device(self, authed_client, user):
        device = DevicePushToken.objects.create(
            user_id=user.id, token="tok-i", platform="ios"
        )
        resp = authed_client.delete(f"/devices/by-id/{device.pk}/")
        assert resp.status_code == 204
        assert not DevicePushToken.objects.filter(pk=device.pk).exists()

    def test_unknown_id_404(self, authed_client):
        resp = authed_client.delete("/devices/by-id/999999/")
        assert resp.status_code == 404
        assert "device_not_found" in resp.content.decode()

    def test_cannot_delete_another_users_device(self, authed_client, other_user):
        device = DevicePushToken.objects.create(
            user_id=other_user.id, token="tok-other", platform="web"
        )
        resp = authed_client.delete(f"/devices/by-id/{device.pk}/")
        assert resp.status_code == 404
        assert DevicePushToken.objects.filter(pk=device.pk).exists()

    def test_the_id_route_does_not_shadow_the_token_route(self, authed_client, user):
        """A token that looks like the id segment still deletes as a token."""
        DevicePushToken.objects.create(user_id=user.id, token="by-id", platform="web")
        assert authed_client.delete("/devices/by-id/").status_code == 204
        assert not DevicePushToken.objects.filter(token="by-id").exists()


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
