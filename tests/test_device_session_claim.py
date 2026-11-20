"""The device row is bound to the session the access token names (``sid``).

This service verifies tokens without the auth database (stapel-auth is not
installed here), the split deployment the claim exists for:

* a token with ``sid`` binds the row to it; a ``session_id`` in the body is
  then ignored — not refused — including someone else's;
* a token without the claim (issued before it existed) falls back to the
  declared ``session_id``, as in 0.31.0;
* a forged claim is no credential at all;
* ``user.session_expired`` removes the session's rows like ``session_revoked``;
* a guest is refused with the 403 the schema declares.
"""
import base64
import json
import uuid
from pathlib import Path

import pytest
from django.apps import apps
from django.test import override_settings
from rest_framework.test import APIClient

from stapel_core.comm import emit
from stapel_core.core.jwt_handler import SESSION_CLAIM

from stapel_notifications.models import DevicePushToken

pytestmark = pytest.mark.django_db

DEVICES = "/devices/"


@pytest.fixture(autouse=True)
def _production_auth(monkeypatch):
    """The suite runs DRF's defaults; these tests need what production runs:
    bearer JWT authentication and core's error envelope."""
    from stapel_core.django.jwt.authentication import JWTCookieAuthentication
    from stapel_core.django.jwt.provider import jwt_provider

    from stapel_notifications import views

    for view in (views.DeviceTokenView, views.DeviceTokenDeleteView,
                 views.DeviceUnregisterBodyView, views.DeviceUnregisterView):
        monkeypatch.setattr(view, "authentication_classes", [JWTCookieAuthentication])
    jwt_provider.reset()
    with override_settings(REST_FRAMEWORK={
        "EXCEPTION_HANDLER": "stapel_core.django.api.errors.stapel_exception_handler",
    }):
        yield
    jwt_provider.reset()


def _claims(user_id, **extra):
    return {
        "user_id": str(user_id),
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "username": f"u{uuid.uuid4().hex[:8]}",
        "is_staff": False,
        "is_superuser": False,
        "is_active": True,
        **extra,
    }


def _access(user_id, sid=None):
    from stapel_core.django.jwt.provider import jwt_provider

    extra = {SESSION_CLAIM: sid} if sid is not None else {}
    access, _ = jwt_provider.create_tokens_from_data(_claims(user_id, **extra))
    return access


def _client(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _register(client, token, **body):
    return client.post(DEVICES, {"token": token, "platform": "android", **body}, format="json")


def test_this_service_has_no_auth_database():
    assert not apps.is_installed("stapel_auth")


class TestTheClaimBindsTheRow:
    def test_row_takes_the_tokens_session(self, user):
        sid = str(uuid.uuid4())
        response = _register(_client(_access(user.id, sid)), "tok-1")
        assert response.status_code == 201, response.content
        assert response.json()["session_bound"] is True
        assert DevicePushToken.objects.get(token="tok-1").session_id == sid

    def test_someone_elses_session_id_in_the_body_binds_to_the_tokens_session(self, user):
        mine, theirs = str(uuid.uuid4()), str(uuid.uuid4())
        response = _register(_client(_access(user.id, mine)), "tok-1", session_id=theirs)
        assert response.status_code == 201, response.content
        assert DevicePushToken.objects.get(token="tok-1").session_id == mine

    def test_mirrored_user_on_a_split_deployment(self):
        """The account exists only in the token; the row still gets its session."""
        from django.contrib.auth import get_user_model

        uid, sid = uuid.uuid4(), str(uuid.uuid4())
        assert not get_user_model().objects.filter(pk=uid).exists()
        with override_settings(JWT_CREATE_USERS_FROM_TOKEN=True):
            response = _register(_client(_access(uid, sid)), "tok-m")
        assert response.status_code == 201, response.content
        row = DevicePushToken.objects.get(token="tok-m")
        assert (str(row.user_id), row.session_id) == (str(uid), sid)

    def test_revoking_the_claimed_session_removes_the_row(self, user):
        sid = str(uuid.uuid4())
        _register(_client(_access(user.id, sid)), "tok-1", session_id=str(uuid.uuid4()))
        emit("user.session_revoked", {"user_id": str(user.id), "session_id": sid}, key=str(user.id))
        assert not DevicePushToken.objects.exists()


class TestTokensWithoutTheClaim:
    def test_declared_session_is_used(self, user):
        declared = str(uuid.uuid4())
        response = _register(_client(_access(user.id)), "tok-1", session_id=declared)
        assert response.status_code == 201, response.content
        assert DevicePushToken.objects.get(token="tok-1").session_id == declared

    def test_nothing_declared_leaves_the_row_unbound(self, user):
        response = _register(_client(_access(user.id)), "tok-1")
        assert response.status_code == 201
        assert response.json()["session_bound"] is False


class TestForgedClaim:
    def test_an_edited_sid_is_no_credential(self, user):
        head, body, sig = _access(user.id, "mine").split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload[SESSION_CLAIM] = "theirs"
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        response = _register(_client(f"{head}.{forged}.{sig}"), "tok-f", session_id="theirs")
        assert response.status_code in (401, 403)
        assert not DevicePushToken.objects.exists()


class TestExpiry:
    def test_an_expired_session_takes_its_rows(self, user):
        expired, live = str(uuid.uuid4()), str(uuid.uuid4())
        _register(_client(_access(user.id, expired)), "tok-expired")
        _register(_client(_access(user.id, live)), "tok-live")
        emit(
            "user.session_expired",
            {"user_id": str(user.id), "session_id": expired, "expired_at": "2026-11-20T10:00:00+00:00"},
            key=str(user.id),
        )
        assert list(DevicePushToken.objects.values_list("token", flat=True)) == ["tok-live"]

    def test_the_event_is_scoped_to_its_account(self, user, other_user):
        sid = str(uuid.uuid4())
        DevicePushToken.objects.create(user_id=other_user.id, token="theirs", platform="ios", session_id=sid)
        emit(
            "user.session_expired",
            {"user_id": str(user.id), "session_id": sid, "expired_at": "2026-11-20T10:00:00+00:00"},
            key=str(user.id),
        )
        assert DevicePushToken.objects.filter(token="theirs").exists()


class TestGuestForbiddenIsDeclared:
    OPS = [
        ("get", "/notifications/api/v1/devices/"),
        ("post", "/notifications/api/v1/devices/"),
        ("delete", "/notifications/api/v1/devices/{token}/"),
        ("delete", "/notifications/api/v1/devices/by-id/{device_id}/"),
        ("post", "/notifications/api/v1/devices/unregister/"),
    ]

    def test_guest_gets_the_declared_key(self):
        from stapel_core.django.users.models import User

        client = APIClient()
        client.force_authenticate(user=User.create_anonymous_user())
        response = _register(client, "tok-guest")
        assert response.status_code == 403
        assert response.json()["localizable_error"] == "error.403.forbidden"

    @pytest.mark.parametrize("method,path", OPS)
    def test_every_device_operation_declares_it(self, method, path):
        docs = Path(__file__).resolve().parent.parent / "docs"
        v2 = json.loads((docs / "schema.v2.json").read_text())
        assert "error.403.forbidden" in v2["paths"][path][method]["responses"]["403"]["x-stapel-errors"]
        v1 = json.loads((docs / "schema.json").read_text())
        assert "403" in v1["paths"][path][method]["responses"]
