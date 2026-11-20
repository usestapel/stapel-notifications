"""Device rows follow the session and the installation that registered them.

* N2 — a row is bound to the auth session that registered it, and
  ``user.session_revoked`` for that session deletes it: a handset that signed
  out (or was signed out from elsewhere) stops receiving the account's pushes
  even when its own unregister never arrived. Unbound rows behave as before.
* N3 — ``device_id`` names the installation: a new token from it refreshes
  the same row, and a token another account still holds on the SAME
  installation is handed over instead of refused; a different installation
  still gets 409.
* N6 — unregister takes the token (or the installation id) in the body.
"""
import uuid

import pytest
from django.test import override_settings

from stapel_core.comm import emit

from stapel_notifications.actions import handle_user_session_revoked
from stapel_notifications.channels import push as push_channel
from stapel_notifications.models import DevicePushToken
from stapel_notifications.services import process_notification

pytestmark = pytest.mark.django_db

DEVICES = "/devices/"
UNREGISTER = "/devices/unregister/"
INSTALL_A = "inst-7f3c2a9e4b1d4c5e"
INSTALL_B = "inst-0a1b2c3d4e5f6a7b"


def _register(client, token, *, session=None, device_id=None, platform="android"):
    body = {"token": token, "platform": platform}
    if session is not None:
        body["session_id"] = session
    if device_id is not None:
        body["device_id"] = device_id
    return client.post(DEVICES, body, format="json")


def _revoked(user_id, session_id):
    class _Event:
        event_id = "evt-revoked"
        payload = {"user_id": str(user_id), "session_id": session_id}

    return _Event()


@pytest.fixture
def outbox():
    push_channel.mock_outbox.clear()
    yield push_channel.mock_outbox
    push_channel.mock_outbox.clear()


@pytest.fixture
def other_client(other_user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


class TestSessionBinding:
    def test_the_row_carries_the_registering_session(self, authed_client, user):
        session = str(uuid.uuid4())
        response = _register(authed_client, "tok-1", session=session)
        assert response.status_code == 201, response.content
        assert response.json()["session_bound"] is True
        assert DevicePushToken.objects.get(token="tok-1").session_id == session

    def test_revoking_the_session_deletes_only_its_rows(self, authed_client, user, other_user):
        mine, other_session = str(uuid.uuid4()), str(uuid.uuid4())
        _register(authed_client, "tok-mine", session=mine)
        _register(authed_client, "tok-other-session", session=other_session)
        _register(authed_client, "tok-unbound")
        # The same session id under another account names nothing of ours.
        DevicePushToken.objects.create(
            user_id=other_user.id, token="tok-foreign", platform="ios", session_id=mine,
        )

        handle_user_session_revoked(_revoked(user.id, mine))

        assert set(DevicePushToken.objects.values_list("token", flat=True)) == {
            "tok-other-session", "tok-unbound", "tok-foreign",
        }

    def test_a_redelivery_is_a_no_op(self, authed_client, user):
        session = str(uuid.uuid4())
        _register(authed_client, "tok-1", session=session)
        handle_user_session_revoked(_revoked(user.id, session))
        handle_user_session_revoked(_revoked(user.id, session))
        assert not DevicePushToken.objects.exists()

    def test_a_malformed_event_does_not_raise(self, caplog):
        handle_user_session_revoked(_revoked("not-a-uuid", "s"))
        handle_user_session_revoked(_revoked("", ""))

    def test_refresh_under_a_new_session_rebinds_the_row(self, authed_client, user):
        old, new = str(uuid.uuid4()), str(uuid.uuid4())
        _register(authed_client, "tok-1", session=old)
        _register(authed_client, "tok-1", session=new)
        handle_user_session_revoked(_revoked(user.id, old))
        assert DevicePushToken.objects.get(token="tok-1").session_id == new

    def test_an_unbound_row_is_listed_as_unbound(self, authed_client, user):
        _register(authed_client, "tok-1")
        item = authed_client.get(DEVICES).json()[0]
        assert item["session_bound"] is False
        assert item["device_id"] is None


class TestRegisterRevokeSendEndToEnd:
    """register → revoke the session → the sender does not reach that device."""

    def test_a_revoked_sessions_device_gets_nothing(self, authed_client, user, outbox):
        signed_out, still_in = str(uuid.uuid4()), str(uuid.uuid4())
        _register(authed_client, "tok-signed-out", session=signed_out, device_id=INSTALL_A)
        _register(authed_client, "tok-still-in", session=still_in, device_id=INSTALL_B)
        gone = DevicePushToken.objects.get(token="tok-signed-out").pk
        kept = DevicePushToken.objects.get(token="tok-still-in").pk

        # Through the comm plane, the way auth's outbox delivers it.
        emit(
            "user.session_revoked",
            {"user_id": str(user.id), "session_id": signed_out},
            key=str(user.id),
        )
        with override_settings(STAPEL_NOTIFICATIONS={"PUSH_PROVIDER": "mock"}):
            process_notification(
                notification_type="new_message", user_id=str(user.id), variables={},
            )

        reached = [entry["device"] for entry in outbox]
        assert gone not in reached
        assert reached == [kept]


class TestInstallationId:
    def test_rotation_refreshes_the_same_row(self, authed_client, user):
        _register(authed_client, "tok-old", device_id=INSTALL_A)
        pk = DevicePushToken.objects.get(token="tok-old").pk
        response = _register(authed_client, "tok-new", device_id=INSTALL_A)
        assert response.status_code == 201
        rows = list(DevicePushToken.objects.filter(user_id=user.id))
        assert [(r.pk, r.token, r.device_id) for r in rows] == [(pk, "tok-new", INSTALL_A)]

    def test_same_installation_takes_over_a_live_token(self, authed_client, other_client, user, other_user):
        """Another account signed in on this installation and its sign-out
        never reached the server: the same device_id hands the token over."""
        assert _register(other_client, "tok-shared", device_id=INSTALL_A).status_code == 201
        response = _register(authed_client, "tok-shared", device_id=INSTALL_A)
        assert response.status_code == 201, response.content
        row = DevicePushToken.objects.get(token="tok-shared")
        assert row.user_id == user.id

    def test_another_installation_is_still_refused(self, authed_client, other_client, other_user):
        _register(other_client, "tok-shared", device_id=INSTALL_A)
        response = _register(authed_client, "tok-shared", device_id=INSTALL_B)
        assert response.status_code == 409
        assert response.json()["localizable_error"] == "error.409.conflict"
        assert DevicePushToken.objects.get(token="tok-shared").user_id == other_user.id

    def test_no_installation_id_is_still_refused(self, authed_client, other_client, other_user):
        _register(other_client, "tok-shared", device_id=INSTALL_A)
        assert _register(authed_client, "tok-shared").status_code == 409

    def test_account_switch_drops_the_previous_accounts_row(self, authed_client, other_client, other_user, user):
        """Same installation, new account, new token: the old account's row
        on this installation goes, so it stops reaching the handset."""
        _register(other_client, "tok-a", device_id=INSTALL_A)
        _register(authed_client, "tok-b", device_id=INSTALL_A)
        assert list(DevicePushToken.objects.values_list("user_id", "token")) == [(user.id, "tok-b")]

    @pytest.mark.parametrize("bad", ["short", "has spaces in it ok", "x" * 256])
    def test_a_weak_installation_id_is_refused(self, authed_client, bad):
        response = _register(authed_client, "tok-1", device_id=bad)
        assert response.status_code == 400
        assert response.json()["localizable_error"] == "error.400.invalid_device_id"
        assert not DevicePushToken.objects.exists()


class TestUnregisterInTheBody:
    def test_by_token(self, authed_client, user):
        _register(authed_client, "tok-1")
        response = authed_client.post(UNREGISTER, {"token": "tok-1"}, format="json")
        assert response.status_code == 204
        assert not DevicePushToken.objects.exists()

    def test_by_installation(self, authed_client, user):
        _register(authed_client, "tok-1", device_id=INSTALL_A)
        _register(authed_client, "tok-2", device_id=INSTALL_B)
        response = authed_client.post(UNREGISTER, {"device_id": INSTALL_A}, format="json")
        assert response.status_code == 204
        assert list(DevicePushToken.objects.values_list("token", flat=True)) == ["tok-2"]

    @pytest.mark.parametrize("body", [{}, {"token": "t", "device_id": INSTALL_A}])
    def test_exactly_one_target(self, authed_client, body):
        response = authed_client.post(UNREGISTER, body, format="json")
        assert response.status_code == 400
        assert response.json()["localizable_error"] == "error.400.device_target_required"

    def test_only_the_callers_rows(self, authed_client, other_user):
        DevicePushToken.objects.create(user_id=other_user.id, token="theirs", platform="ios")
        response = authed_client.post(UNREGISTER, {"token": "theirs"}, format="json")
        assert response.status_code == 404
        assert response.json()["localizable_error"] == "error.404.token_not_found"
        assert DevicePushToken.objects.filter(token="theirs").exists()

    def test_unknown_installation_404(self, authed_client):
        response = authed_client.post(UNREGISTER, {"device_id": INSTALL_A}, format="json")
        assert response.status_code == 404
        assert response.json()["localizable_error"] == "error.404.device_not_found"

    def test_guest_refused(self, api_client):
        assert api_client.post(UNREGISTER, {"token": "t"}, format="json").status_code in (401, 403)

    def test_the_url_form_is_kept_and_deprecated(self, authed_client):
        import json
        from pathlib import Path

        _register(authed_client, "tok-1")
        assert authed_client.delete(f"{DEVICES}tok-1/").status_code == 204
        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "docs" / "schema.v2.json").read_text()
        )
        assert schema["paths"]["/notifications/api/v1/devices/{token}/"]["delete"]["deprecated"] is True
        assert "post" in schema["paths"]["/notifications/api/v1/devices/unregister/"]
