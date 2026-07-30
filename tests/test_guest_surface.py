"""The guest (anonymous session) surface of stapel-notifications.

With ``AUTH_ANONYMOUS`` on, a guest session is ``is_authenticated``, so a
bare ``IsAuthenticated`` gate lets it through, and until now nothing in the
source said whether that was wanted (``stapel_core.adoption`` W002).
``views.py`` now states it; these tests keep the statement true:

    a guest may read its own (empty) feed, and may not touch the
    device-token registry.

The registry half is not a hygiene rule, it is a live defect the anonymous
axis opens: ``DeviceTokenView`` deliberately removes another account's
binding when the same physical device's token arrives under a new user. With
guest sessions on, "user logs out → app mints an anonymous session → app
re-registers the same token" silently detaches push from the real account.
``test_guest_cannot_steal_a_real_users_device_binding`` is that scenario,
written out.
"""

import pytest

from stapel_core.django.api.permissions import (
    ANONYMOUS_ALLOWED,
    IsNotAnonymousUser,
)
from stapel_notifications import views
from stapel_notifications.models import DevicePushToken, NotificationLog


@pytest.fixture
def guest(db):
    """A guest session's user — what ``POST /auth/api/v1/anonymous/`` mints:
    authenticated, ``is_anonymous=True``."""
    from stapel_core.django.users.models import User

    return User.create_anonymous_user()


@pytest.fixture
def guest_client(guest):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=guest)
    return client


# ---------------------------------------------------------------------------
# The device-token registry is closed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGuestCannotTouchTheDeviceRegistry:
    def test_register_is_refused(self, guest_client):
        resp = guest_client.post(
            "/devices/", {"token": "tok-guest", "platform": "web"}, format="json"
        )
        assert resp.status_code == 403, resp.content
        assert DevicePushToken.objects.count() == 0

    def test_unregister_is_refused(self, guest_client, user):
        DevicePushToken.objects.create(
            token="tok-real", user_id=user.id, platform="ios", is_active=True
        )
        resp = guest_client.delete("/devices/tok-real/")
        assert resp.status_code == 403, resp.content
        assert DevicePushToken.objects.filter(token="tok-real").exists()

    def test_guest_cannot_steal_a_real_users_device_binding(self, guest_client, user):
        """The shared-device scenario, written out.

        A real account holds this device's push token. A logout leaves an
        anonymous session behind, the app re-registers the same token — and
        before this gate, the rebinding branch deleted the real account's row
        and pushed the device onto a throwaway identity that will never be
        logged into again.
        """
        DevicePushToken.objects.create(
            token="device-1", user_id=user.id, platform="android", is_active=True
        )
        resp = guest_client.post(
            "/devices/", {"token": "device-1", "platform": "android"}, format="json"
        )
        assert resp.status_code == 403, resp.content
        row = DevicePushToken.objects.get(token="device-1")
        assert row.user_id == user.id, "the real account must keep its device"

    def test_a_registered_user_is_unaffected(self, authed_client, user):
        """The gate is about *anonymous*, not about *authenticated*."""
        resp = authed_client.post(
            "/devices/", {"token": "tok-a", "platform": "android"}, format="json"
        )
        assert resp.status_code == 201, resp.content
        assert DevicePushToken.objects.get(token="tok-a").user_id == user.id
        assert authed_client.delete("/devices/tok-a/").status_code == 204


# ---------------------------------------------------------------------------
# The feed stays readable, and stays empty
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGuestReadsAnEmptyFeed:
    def test_feed_is_empty_not_forbidden(self, guest_client, user):
        NotificationLog.objects.create(
            user_id=user.id,
            notification_type="meeting_invite",
            title="Someone else's notification",
            body="not for the guest",
            status="sent",
            channel="push",
        )
        resp = guest_client.get("/feed/")
        assert resp.status_code == 200, resp.content
        assert resp.json()["items"] == []

    def test_declared_allowed_in_the_source(self):
        assert views.NotificationFeedView.stapel_anonymous_access == ANONYMOUS_ALLOWED


def test_closed_views_carry_the_permission_class():
    for view in (views.DeviceTokenView, views.DeviceTokenDeleteView):
        assert IsNotAnonymousUser in view.permission_classes, view.__name__


def test_no_view_is_left_silent():
    """The question ``stapel_core.adoption`` E001/W002 asks a consumer's
    deployment, asked here — where it can be answered."""
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.views import APIView

    from stapel_core.django.api.permissions import ANONYMOUS_DECLARATIONS

    silent = [
        name
        for name, obj in vars(views).items()
        if isinstance(obj, type)
        and issubclass(obj, APIView)
        and set(getattr(obj, "permission_classes", ()) or ()) == {IsAuthenticated}
        and getattr(obj, "stapel_anonymous_access", None) not in ANONYMOUS_DECLARATIONS
    ]
    assert silent == []
