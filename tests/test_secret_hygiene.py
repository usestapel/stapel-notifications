"""The delivery journal must not be able to hold a credential.

NOTIFY-01 (security audit 2026-08-11): ``NotificationLog.data`` persisted
every scalar caller variable, which for this library's own types means the
one-time passcode, the sign-in link with its token, the invitation URL that
creates an account, and the initial password of a provisioned account — in a
table the Django admin renders.
"""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog, UserContact
from stapel_notifications.routing import registered_types
from stapel_notifications.services import process_notification
from stapel_notifications.telemetry import REDACTED, looks_secret, redact_text


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


MOCKS = {"EMAIL_PROVIDER": "mock", "SMS_PROVIDER": "mock", "PUSH_PROVIDER": "mock"}

#: One value per credential shape this library actually sends. Each is
#: searched for verbatim in every column of every row the journal writes.
SECRETS = {
    "code": "483920",
    "otp": "4839",
    "password": "Xk7-tR29fmQz",
    "temporary_password": "9fQ2vLb8xZa4",
    "magic_link_url": "https://app.example/auth/magic?token=eyJhbGciOiJIUzI1NiJ9.abcdef123456.sig",
    "reset_url": "https://app.example/reset#token=8f2c1d4e9a7b3c5d6e8f0a1b",
    "invitation_url": "https://app.example/invite/7f3a9c2b8e1d4f6a0c5b2e9d",
    "download_url": "https://app.example/export?sig=3f8a1c9e2b7d4506a1c3e5f7",
    "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.QWxhZGRpbjpvcGVuc2VzYW1l",
    "recovery_code": "a1b2c3d4e5f60718",
}


def _all_journal_text() -> str:
    return "\n".join(
        f"{row.data}\n{row.title}\n{row.body}\n{row.error_message}"
        for row in NotificationLog.objects.all()
    )


@pytest.mark.django_db
class TestNoRegisteredTypeCanJournalASecret:
    """The contract test the audit asked for: EVERY registered type."""

    @pytest.mark.parametrize("notification_type", registered_types())
    def test_no_secret_shaped_value_reaches_the_journal(self, notification_type, user):
        UserContact.objects.create(
            user_id=user.id, email="dest@example.com", phone="+15550000001"
        )
        with override_settings(STAPEL_NOTIFICATIONS=MOCKS):
            process_notification(
                notification_type=notification_type,
                user_id=str(user.id),
                variables={**SECRETS, "expiry_minutes": 5, "workspace_name": "Acme"},
                email="dest@example.com",
                phone="+15550000001",
            )
        assert NotificationLog.objects.exists(), "nothing was journalled at all"
        journalled = _all_journal_text()
        leaked = [name for name, value in SECRETS.items() if value in journalled]
        assert leaked == [], f"{notification_type} journalled {leaked}"


@pytest.mark.django_db
def test_the_table_refuses_a_secret_even_when_written_directly():
    """The guarantee belongs to the table, not to one call site.

    Host code, a future channel and a data migration all write rows; a rule
    that lived only in ``services`` would be one forgotten call away from
    being no rule at all.
    """
    row = NotificationLog.objects.create(
        notification_type="otp_code",
        channel="email",
        status="sent",
        recipient="dest@example.com",
        title="Your code",
        body="Sign in: https://app.example/magic?token=abc123def456ghi789",
        data={"code": "483920", "chat_url": "https://app.example/chat/42"},
    )
    row.refresh_from_db()
    assert "code" not in row.data              # never declared as telemetry
    assert row.data["chat_url"] == "https://app.example/chat/42"  # deep link kept
    assert "token=abc123def456ghi789" not in row.body
    assert REDACTED in row.body


@pytest.mark.django_db
def test_declared_telemetry_survives_and_is_still_shape_checked():
    with override_settings(STAPEL_NOTIFICATIONS={
        **MOCKS,
        "TYPES": {"invoice_ready": {
            "channels": ["email"], "group": "system",
            "template": "notifications/email/gdpr_export_ready.html",
            "telemetry": ["invoice_number", "pay_url"],
        }},
    }):
        process_notification(
            notification_type="invoice_ready",
            user_id=None,
            variables={
                "invoice_number": 17,
                "pay_url": "https://app.example/pay?token=9c2b8e1d4f6a0c5b2e9d",
                "secret_note": "not declared",
            },
            email="dest@example.com",
        )
    data = NotificationLog.objects.get(status="sent").data
    assert data["invoice_number"] == 17
    assert data["pay_url"] == REDACTED  # declared, but the value is a token link
    assert "secret_note" not in data


@pytest.mark.django_db
def test_a_host_can_declare_telemetry_without_owning_the_routing_entry():
    with override_settings(STAPEL_NOTIFICATIONS={
        **MOCKS, "TELEMETRY": {"gdpr.export_ready": ["export_id"], "*": ["tenant"]},
    }):
        process_notification(
            notification_type="gdpr.export_ready",
            user_id=None,
            variables={"export_id": "exp-17", "tenant": "acme", "download_url": "https://x/dl"},
            email="dest@example.com",
        )
    data = NotificationLog.objects.get(status="sent").data
    assert data["export_id"] == "exp-17"
    assert data["tenant"] == "acme"
    assert "download_url" not in data


@pytest.mark.django_db
def test_the_journals_own_facts_are_kept():
    """Deny-by-default must not empty the columns operators actually read."""
    with override_settings(STAPEL_NOTIFICATIONS=MOCKS):
        process_notification(
            notification_type="gdpr.export_ready",
            user_id=None,
            variables={"download_url": "https://x/dl"},
            email="dest@example.com",
            event_id="evt-telemetry-1",
        )
    data = NotificationLog.objects.get(status="sent").data
    assert data["notification_type"] == "gdpr.export_ready"
    assert data["language_source"]
    assert data["event_id"] == "evt-telemetry-1"


class TestLooksSecret:
    @pytest.mark.parametrize("value", [
        "483920",                                   # OTP
        "Xk7tR29fmQz4",                             # generated password
        "https://app.example/auth?token=abc",       # link carrying a token
        "https://app.example/r#t=abc",              # …in the fragment
        "https://app.example/invite/7f3a9c2b8e1d4f6a",  # …in the last segment
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",  # JWT
        "a1b2c3d4e5f60718",                         # hex blob
    ])
    def test_credential_shapes(self, value):
        assert looks_secret(value) is True

    @pytest.mark.parametrize("value", [
        5, True, 3.5, None,
        "Acme Corporation",
        "workspace-invitation",
        "https://app.example/chat/42",
        "550e8400-e29b-41d4-a716-446655440000",     # an identifier
        "sender",
        "",
    ])
    def test_identifiers_and_prose_are_not_secrets(self, value):
        assert looks_secret(value) is False


class TestRedactText:
    def test_a_link_with_a_token_goes(self):
        assert "token=" not in redact_text("Sign in: https://x.example/a?token=abc123")

    def test_ordinary_copy_survives(self):
        for text in ("Anna sent you a message", "Zoom-Meeting-2026 starts soon",
                     "Open https://app.example/chat/42"):
            assert redact_text(text) == text
