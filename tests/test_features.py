"""Push provider registry, GDPR types, SMS opt-out, token rebinding."""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import (
    DevicePushToken,
    NotificationLog,
    UserContact,
    UserNotificationSettings,
)
from stapel_notifications.routing import get_channels, get_email_template, get_group
from stapel_notifications.services import _should_send, process_notification


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


# ── Push provider registry ──────────────────────────────────────


class _FakePushProvider:
    calls = []

    def send(self, user_id, title, body, data):
        type(self).calls.append((user_id, title, body, data))
        return 7


@pytest.mark.django_db
def test_push_provider_dotted_path():
    from stapel_notifications.channels.push import send_push

    _FakePushProvider.calls = []
    with override_settings(
        STAPEL_NOTIFICATIONS={"PUSH_PROVIDER": "tests.test_features._FakePushProvider"}
    ):
        assert send_push("u1", "t", "b", {"x": 1}) == 7
    assert _FakePushProvider.calls == [("u1", "t", "b", {"x": 1})]


@pytest.mark.django_db
def test_push_provider_short_name_mock(user):
    from stapel_notifications.channels.push import _MockPushProvider, _get_provider, send_push

    with override_settings(STAPEL_NOTIFICATIONS={"PUSH_PROVIDER": "mock"}):
        assert isinstance(_get_provider(), _MockPushProvider)
        DevicePushToken.objects.create(user_id=user.id, token="tok-1", platform="ios")
        assert send_push(str(user.id), "t", "b") == 1


def test_push_provider_default_is_fcm():
    from stapel_notifications.channels.push import _FCMPushProvider, _get_provider

    assert isinstance(_get_provider(), _FCMPushProvider)


# ── EMAIL_TEMPLATES conf override ───────────────────────────────


def test_email_templates_conf_override():
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_TEMPLATES": {"otp_code": "myapp/email/custom_otp.html"}
        }
    ):
        assert get_email_template("otp_code") == "myapp/email/custom_otp.html"
        # unrelated types keep the namespaced built-ins
        assert get_email_template("new_message") == "notifications/email/new_message.html"
    assert get_email_template("otp_code") == "notifications/email/otp_code.html"


# ── GDPR / workspace notification types ─────────────────────────


def test_gdpr_types_registered():
    for t in ("gdpr.export_ready", "gdpr.inactivity_warning", "gdpr.inactivity_closed"):
        assert get_channels(t) == ["email"]
        assert get_group(t) == "auth"  # mandatory, no unsubscribe
        assert get_email_template(t).startswith("notifications/email/gdpr_")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "ntype,variables",
    [
        ("gdpr.export_ready", {"download_url": "https://x.example/dl/1"}),
        ("gdpr.inactivity_warning", {"days_remaining": 14}),
        ("gdpr.inactivity_closed", {}),
        (
            "workspace.invitation",
            {
                "workspace_name": "Acme",
                "inviter_name": "Ada",
                "accept_url": "https://x.example/accept/1",
            },
        ),
    ],
)
def test_new_types_render_and_send(ntype, variables):
    """Each new type renders its email template through the mock provider."""
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"}):
        process_notification(
            notification_type=ntype,
            user_id=None,
            variables=variables,
            email="dest@example.com",
        )
    log = NotificationLog.objects.get(notification_type=ntype, channel="email")
    assert log.status == "sent", log.error_message


@pytest.mark.django_db
def test_inactivity_warning_formats_days_remaining(user):
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"}):
        process_notification(
            notification_type="gdpr.inactivity_warning",
            user_id=None,
            variables={"days_remaining": 14},
            email="dest@example.com",
        )
    log = NotificationLog.objects.get(notification_type="gdpr.inactivity_warning")
    assert log.status == "sent"


# ── SMS opt-out preferences ─────────────────────────────────────


@pytest.mark.django_db
class TestSmsOptOut:
    def _settings_obj(self, user, **overrides):
        return UserNotificationSettings.objects.create(user_id=user.id, **overrides)

    def test_sms_pref_fields_are_valid(self, user):
        obj = self._settings_obj(user, sms_system=False, sms_messages=True)
        assert _should_send("system", "sms", obj) is False
        assert _should_send("messages", "sms", obj) is True

    def test_sms_pref_defaults_to_send(self, user):
        obj = self._settings_obj(user)
        assert _should_send("system", "sms", obj) is True
        assert _should_send("messages", "sms", obj) is True

    def test_auth_group_ignores_sms_opt_out(self, user):
        obj = self._settings_obj(user, sms_system=False, sms_messages=False)
        assert _should_send("auth", "sms", obj) is True

    def test_process_notification_skips_opted_out_sms(self, user):
        self._settings_obj(user, sms_system=False)
        UserContact.objects.create(user_id=user.id, phone="+15550001111")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "SMS_PROVIDER": "mock",
                "TYPES": {
                    "billing.low_balance": {"channels": ["sms"], "group": "system"}
                },
            }
        ):
            process_notification(
                notification_type="billing.low_balance",
                user_id=str(user.id),
                variables={},
            )
        log = NotificationLog.objects.get(notification_type="billing.low_balance")
        assert log.status == "skipped"


# ── Device token rebinding ──────────────────────────────────────


@pytest.mark.django_db
class TestDeviceTokenRebinding:
    def _register(self, client, token="tok-shared", platform="ios"):
        return client.post(
            "/devices/", {"token": token, "platform": platform}, format="json"
        )

    def test_register_creates_token(self, authed_client, user):
        resp = self._register(authed_client)
        assert resp.status_code == 201
        row = DevicePushToken.objects.get(token="tok-shared")
        assert row.user_id == user.id and row.is_active

    def test_rebinding_removes_other_users_binding(
        self, authed_client, user, other_user, caplog
    ):
        assert self._register(authed_client).status_code == 201

        from rest_framework.test import APIClient

        other_client = APIClient()
        other_client.force_authenticate(user=other_user)
        with caplog.at_level("WARNING", logger="stapel_notifications.views"):
            resp = self._register(other_client, platform="android")
        assert resp.status_code == 201

        rows = DevicePushToken.objects.filter(token="tok-shared")
        assert rows.count() == 1  # no silent duplicate, no silent re-bind
        row = rows.get()
        assert row.user_id == other_user.id
        assert row.platform == "android"
        # audit trail for the ownership change
        assert any("rebinding" in r.message for r in caplog.records)

    def test_same_user_reregister_is_quiet(self, authed_client, user, caplog):
        self._register(authed_client)
        DevicePushToken.objects.filter(token="tok-shared").update(is_active=False)
        with caplog.at_level("WARNING", logger="stapel_notifications.views"):
            resp = self._register(authed_client)
        assert resp.status_code == 201
        row = DevicePushToken.objects.get(token="tok-shared")
        assert row.user_id == user.id and row.is_active
        assert not any("rebinding" in r.message for r in caplog.records)
