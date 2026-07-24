"""Fork-free extension points: open type registry + pluggable channel providers."""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.routing import (
    get_channels,
    get_email_template,
    get_group,
    get_routing,
    registered_types,
)


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


def test_builtin_types_present():
    assert get_channels("otp_code") == ["email", "sms"]
    assert get_group("otp_code") == "auth"
    assert get_email_template("otp_code") == "notifications/email/otp_code.html"


def test_workspace_invitation_registered():
    assert get_channels("workspace.invitation") == ["email"]
    assert get_email_template("workspace.invitation") == "notifications/email/workspace_invitation.html"


# ── Org program types (workspaces-org-program.md §F) ─────────────


def test_workspace_invitation_new_user_registered():
    """Clean routing-override: separate type, not an override of
    "workspace.invitation", same group/channel shape."""
    assert get_channels("workspace.invitation.new_user") == ["email"]
    assert get_group("workspace.invitation.new_user") == "system"
    assert (
        get_email_template("workspace.invitation.new_user")
        == "notifications/email/workspace_invitation_new_user.html"
    )


def test_workspace_provisioned_account_registered():
    """Auth-class notification: mandatory, no unsubscribe (checked via
    _should_send/group in test_services_pipeline-style tests)."""
    assert get_channels("workspace.provisioned_account") == ["email"]
    assert get_group("workspace.provisioned_account") == "auth"
    assert (
        get_email_template("workspace.provisioned_account")
        == "notifications/email/workspace_provisioned_account.html"
    )


def test_workspace_mfa_suspension_and_restored_registered():
    for t in ("workspace.mfa_suspension", "workspace.mfa_restored"):
        assert get_channels(t) == ["email"]
        assert get_group(t) == "auth"
        assert get_email_template(t) == f"notifications/email/{t.replace('.', '_')}.html"


def test_custom_type_via_settings():
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {
                "invoice_ready": {
                    "channels": ["email", "push"],
                    "group": "system",
                    "template": "email/invoice_ready.html",
                }
            }
        }
    ):
        assert get_channels("invoice_ready") == ["email", "push"]
        assert get_group("invoice_ready") == "system"
        assert get_email_template("invoice_ready") == "email/invoice_ready.html"
        assert "invoice_ready" in registered_types()
    assert get_routing("invoice_ready") is None  # override gone after exit


def test_builtin_type_overridable():
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"new_message": {"channels": ["push"], "group": "messages"}}
        }
    ):
        assert get_channels("new_message") == ["push"]
        # template falls back to the built-in when the override has none
        assert get_email_template("new_message") == "notifications/email/new_message.html"


class _FakeProvider:
    sent = []

    def send(self, phone, body):
        type(self).sent.append((phone, body))


def test_sms_provider_dotted_path():
    from stapel_notifications.channels.sms import send_sms

    _FakeProvider.sent = []
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "SMS_PROVIDER": "tests.test_extensibility._FakeProvider"
        }
    ):
        send_sms("+100", "hi")
    assert _FakeProvider.sent == [("+100", "hi")]


def test_unknown_provider_falls_back_to_mock():
    from stapel_notifications.channels.sms import _MockSMSProvider, _get_provider

    with override_settings(STAPEL_NOTIFICATIONS={"SMS_PROVIDER": "does-not-exist"}):
        assert isinstance(_get_provider(), _MockSMSProvider)

