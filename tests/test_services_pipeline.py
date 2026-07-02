"""process_notification pipeline: idempotency, routing, language, gating,
template rendering and List-Unsubscribe headers (via capturing providers)."""

import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import (
    NotificationLog,
    TranslationCache,
    UserContact,
    UserNotificationSettings,
)
from stapel_notifications.services import _should_send, process_notification


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _CapturingEmailProvider:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append(
            {
                "recipient": recipient,
                "subject": subject,
                "html": html_body,
                "headers": headers or {},
            }
        )


class _FailingEmailProvider:
    def send(self, recipient, subject, html_body, headers):
        raise RuntimeError("smtp is down")


class _CapturingSMSProvider:
    sent = []

    def send(self, phone, body):
        type(self).sent.append((phone, body))


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"
FAILING = f"{_FailingEmailProvider.__module__}._FailingEmailProvider"
CAPTURE_SMS = f"{_CapturingSMSProvider.__module__}._CapturingSMSProvider"


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        yield _CapturingEmailProvider.sent


# ── Idempotency ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_duplicate_event_id_is_processed_once(capture_email):
    for _ in range(2):
        process_notification(
            notification_type="gdpr.export_ready",
            user_id=None,
            variables={"download_url": "https://x/dl"},
            email="dest@example.com",
            event_id="evt-dup-1",
        )
    logs = NotificationLog.objects.filter(notification_type="gdpr.export_ready")
    assert logs.count() == 1
    assert logs.get().data["event_id"] == "evt-dup-1"
    assert len(capture_email) == 1  # provider hit exactly once


@pytest.mark.django_db
def test_failed_attempt_does_not_block_retry(user):
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": FAILING}):
        process_notification(
            notification_type="gdpr.export_ready",
            user_id=None,
            variables={},
            email="dest@example.com",
            event_id="evt-retry",
        )
    assert NotificationLog.objects.get(status="failed").error_message == "smtp is down"
    # Retry with a working provider succeeds (only "sent" blocks reprocessing)
    _CapturingEmailProvider.sent = []
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="gdpr.export_ready",
            user_id=None,
            variables={},
            email="dest@example.com",
            event_id="evt-retry",
        )
    assert NotificationLog.objects.filter(status="sent").count() == 1
    assert len(_CapturingEmailProvider.sent) == 1


# ── Unknown type ────────────────────────────────────────────────


@pytest.mark.django_db
def test_unknown_type_logs_error_and_sends_nothing(caplog):
    with caplog.at_level("ERROR", logger="stapel_notifications.services"):
        process_notification(
            notification_type="no_such_type",
            user_id=None,
            variables={},
            email="dest@example.com",
        )
    assert NotificationLog.objects.count() == 0
    assert any("Unknown notification type" in r.message for r in caplog.records)


# ── Language resolution ─────────────────────────────────────────


@pytest.mark.django_db
class TestLanguageResolution:
    TYPE = "new_device_login"  # auth group, email-only

    def _process(self, user=None, language=None):
        process_notification(
            notification_type=self.TYPE,
            user_id=str(user.id) if user else None,
            variables={},
            email="dest@example.com",
            language=language,
        )
        return NotificationLog.objects.get(notification_type=self.TYPE).language

    def test_profile_override_beats_event_language(self, user, capture_email):
        UserNotificationSettings.objects.create(
            user_id=user.id, language="de", auto_detected_language="es"
        )
        assert self._process(user, language="fr") == "de"

    def test_event_language_beats_auto_detected(self, user, capture_email):
        UserNotificationSettings.objects.create(
            user_id=user.id, language=None, auto_detected_language="es"
        )
        assert self._process(user, language="fr") == "fr"

    def test_auto_detected_fallback(self, user, capture_email):
        UserNotificationSettings.objects.create(
            user_id=user.id, language=None, auto_detected_language="es"
        )
        assert self._process(user) == "es"

    def test_defaults_to_english(self, capture_email):
        assert self._process() == "en"


@pytest.mark.django_db
def test_translation_cache_resolves_and_formats(user, capture_email):
    UserNotificationSettings.objects.create(user_id=user.id, language="de")
    UserContact.objects.create(user_id=user.id, email="de-user@example.com")
    TranslationCache.objects.create(
        key="notification.otp_code.subject",
        values={"de": "Dein Code: {code}", "en": "Your code: {code}"},
    )
    process_notification(
        notification_type="otp_code",
        user_id=str(user.id),
        variables={"code": "1234", "expiry_minutes": 10},
    )
    (mail,) = capture_email
    assert mail["recipient"] == "de-user@example.com"
    assert mail["subject"] == "Dein Code: 1234"  # cached de value + formatting


@pytest.mark.django_db
def test_variables_cannot_overwrite_translation_keys(capture_email):
    process_notification(
        notification_type="otp_code",
        user_id=None,
        variables={"code": "1234", "heading": "<script>injected</script>"},
        email="dest@example.com",
    )
    (mail,) = capture_email
    assert "injected" not in mail["html"]
    assert "Your verification code" in mail["html"]  # built-in default kept


# ── Preference gating ───────────────────────────────────────────


@pytest.mark.django_db
class TestPreferenceGating:
    def test_email_system_opt_out_skips_email_only(self, user):
        UserNotificationSettings.objects.create(user_id=user.id, email_system=False)
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(
            STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"}
        ):
            process_notification(
                notification_type="report_reviewed",  # system: push + email
                user_id=str(user.id),
                variables={},
            )
        statuses = {
            log.channel: log.status
            for log in NotificationLog.objects.filter(user_id=user.id)
        }
        assert statuses == {"email": "skipped", "push": "sent"}

    def test_push_messages_opt_out_skips_push_only(self, user):
        UserNotificationSettings.objects.create(user_id=user.id, push_messages=False)
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(
            STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"}
        ):
            process_notification(
                notification_type="new_message",  # messages: push + email
                user_id=str(user.id),
                variables={},
            )
        statuses = {
            log.channel: log.status
            for log in NotificationLog.objects.filter(user_id=user.id)
        }
        assert statuses == {"email": "sent", "push": "skipped"}

    def test_auth_group_ignores_all_opt_outs(self, user, capture_email):
        UserNotificationSettings.objects.create(
            user_id=user.id,
            email_messages=False,
            email_system=False,
            push_messages=False,
            push_system=False,
            sms_messages=False,
            sms_system=False,
        )
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type="new_device_login",  # auth group
            user_id=str(user.id),
            variables={},
        )
        assert NotificationLog.objects.get(channel="email").status == "sent"
        assert len(capture_email) == 1

    def test_unknown_pref_field_defaults_to_send(self, user):
        obj = UserNotificationSettings.objects.create(user_id=user.id)
        assert _should_send("weird_group", "email", obj) is True


# ── Rendering + unsubscribe headers ─────────────────────────────


@pytest.mark.django_db
def test_non_auth_email_carries_list_unsubscribe_headers(user):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    _CapturingEmailProvider.sent = []
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_PROVIDER": CAPTURE,
            "FRONTEND_URL": "https://app.example",
        }
    ):
        process_notification(
            notification_type="new_message",
            user_id=str(user.id),
            variables={"sender_name": "Ada"},
        )
    (mail,) = _CapturingEmailProvider.sent
    unsub = mail["headers"]["List-Unsubscribe"]
    assert unsub.startswith(
        "<https://app.example/profiles/notifications/unsubscribe/?token="
    )
    assert unsub.endswith(">")
    assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # the token is verifiable and scoped to (user, group, channel)
    from stapel_core.notifications.tokens import verify_unsubscribe_token

    token = unsub[1:-1].split("token=")[1]
    claims = verify_unsubscribe_token(token)
    assert claims == {
        "user_id": str(user.id),
        "group": "messages",
        "channel": "email",
    }


@pytest.mark.django_db
def test_auth_email_has_no_unsubscribe_header(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="new_device_login",
        user_id=str(user.id),
        variables={},
    )
    (mail,) = capture_email
    assert "List-Unsubscribe" not in mail["headers"]
    assert "List-Unsubscribe-Post" not in mail["headers"]


@pytest.mark.django_db
def test_rendered_template_includes_branding_variables(capture_email):
    with override_settings(
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE, "COMPANY_NAME": "AcmeCo"}
    ):
        process_notification(
            notification_type="otp_code",
            user_id=None,
            variables={"code": "9999", "expiry_minutes": 5},
            email="dest@example.com",
        )
    (mail,) = capture_email
    assert "9999" in mail["html"]
    assert "AcmeCo" in mail["subject"]


# ── Dispatch edge cases ─────────────────────────────────────────


@pytest.mark.django_db
def test_missing_email_recipient_is_logged_without_provider_call(user, capture_email):
    process_notification(
        notification_type="new_device_login",
        user_id=str(user.id),  # no UserContact, no direct email
        variables={},
    )
    log = NotificationLog.objects.get(channel="email")
    assert log.recipient == "unknown"
    assert capture_email == []  # provider never called


@pytest.mark.django_db
def test_push_without_user_id_fails_and_is_logged():
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "PUSH_PROVIDER": "mock",
            "TYPES": {"push_only": {"channels": ["push"], "group": "system"}},
        }
    ):
        process_notification(
            notification_type="push_only",
            user_id=None,
            variables={},
        )
    log = NotificationLog.objects.get(channel="push")
    assert log.status == "failed"
    assert "user_id" in log.error_message


@pytest.mark.django_db
def test_unknown_channel_fails_and_is_logged(user):
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"faxable": {"channels": ["fax"], "group": "system"}},
        }
    ):
        process_notification(
            notification_type="faxable",
            user_id=str(user.id),
            variables={},
        )
    log = NotificationLog.objects.get(channel="fax")
    assert log.status == "failed"
    assert "Unknown channel" in log.error_message


@pytest.mark.django_db
def test_sms_channel_sends_formatted_text(user):
    _CapturingSMSProvider.sent = []
    UserContact.objects.create(user_id=user.id, phone="+4512345678")
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_PROVIDER": "mock",
            "SMS_PROVIDER": CAPTURE_SMS,
        }
    ):
        process_notification(
            notification_type="otp_code",
            user_id=str(user.id),
            variables={"code": "4321", "expiry_minutes": 3},
            email="dest@example.com",
        )
    assert _CapturingSMSProvider.sent == [
        ("+4512345678", "Your Stapel code: 4321. Expires in 3 min.")
    ]
    assert NotificationLog.objects.get(channel="sms").status == "sent"
