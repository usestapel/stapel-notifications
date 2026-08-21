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


class _CapturingTelegramProvider:
    """The app-layer bot client the telegram channel always needs — this
    library ships no delivering one. Same duck type as the SMS provider:
    one address, one string."""

    sent = []

    def send(self, chat_id, text):
        type(self).sent.append((chat_id, text))


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"
FAILING = f"{_FailingEmailProvider.__module__}._FailingEmailProvider"
CAPTURE_SMS = f"{_CapturingSMSProvider.__module__}._CapturingSMSProvider"
CAPTURE_TELEGRAM = (
    f"{_CapturingTelegramProvider.__module__}._CapturingTelegramProvider"
)

#: A host registration: nothing built-in routes to telegram, so every
#: telegram test declares the type the way a host would.
TELEGRAM_TYPES = {
    "shift_reminder": {"channels": ["telegram"], "group": "system"},
    "shift_reminder_auth": {"channels": ["telegram"], "group": "auth"},
}


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

    def _source(self):
        return NotificationLog.objects.get(
            notification_type=self.TYPE, status="sent"
        ).data["language_source"]

    def test_the_recipients_own_choice_beats_the_caller(
        self, user, capture_email, profiles_language
    ):
        """Nobody outranks a person's stated preference about their own mail.

        The caller passes the language of the REQUEST, which for one person
        notifying another is the sender's — so it must not overwrite what
        the recipient said in their settings.
        """
        profiles_language[str(user.id)] = ("de", "es")
        assert self._process(user, language="fr") == "de"
        assert self._source() == "recipient_choice"

    def test_the_caller_beats_a_merely_observed_language(
        self, user, capture_email, profiles_language
    ):
        """The caller knows something about THIS message we cannot: an
        anonymous OTP answers a request the recipient just made."""
        profiles_language[str(user.id)] = (None, "es")
        assert self._process(user, language="fr") == "fr"
        assert self._source() == "caller"

    def test_a_registered_user_who_chose_nothing_gets_what_profiles_saw(
        self, user, capture_email, profiles_language
    ):
        profiles_language[str(user.id)] = (None, "es")
        assert self._process(user) == "es"
        assert self._source() == "recipient_detected"

    def test_an_unregistered_invitee_gets_the_senders_language(
        self, capture_email, profiles_language
    ):
        """A decision, not a fallthrough.

        The invitee has no profile and no preference and will not have one
        until they accept. The only fact in the system about how to address
        them is that someone who presumably knows them wrote to them from a
        UI in this language — so that is what they get, and the delivery row
        says ``sender`` so nobody mistakes it for their preference.
        """
        from django.utils import translation

        with translation.override("ru"):
            assert self._process() == "ru"
        assert self._source() == "sender"

    def test_a_known_recipient_with_nothing_known_also_gets_the_sender(
        self, user, capture_email, profiles_language
    ):
        """profiles answered, and the answer was "they never said" — which
        is a real answer, not a broken sync."""
        from django.utils import translation

        with translation.override("ru"):
            assert self._process(user) == "ru"
        log = NotificationLog.objects.get(notification_type=self.TYPE, status="sent")
        assert log.data["language_source"] == "sender"
        assert "recipient_language_unaskable" not in log.data

    def test_the_project_default_is_the_last_resort(self, capture_email):
        """With no active translation and nothing else, the project's
        configured default language — never a hardcoded "en"."""
        from django.utils import translation

        with translation.override(None):
            assert self._process().startswith("en")
        assert self._source() == "default"

    def test_an_unaskable_language_plane_is_recorded_not_swallowed(
        self, user, capture_email, function_registry_sandbox, caplog
    ):
        """No provider for ``profiles.language`` at all: the letter still
        goes out, in the sender's language, and SAYS SO — the delivery row
        carries the flag and the log carries a greppable line. This is the
        state that used to be indistinguishable from "the user has no
        preference", and the whole defect lived in that ambiguity."""
        from django.utils import translation

        from stapel_notifications.language import PROFILES_LANGUAGE

        with function_registry_sandbox._lock:
            function_registry_sandbox._providers.pop(PROFILES_LANGUAGE, None)

        with caplog.at_level("WARNING", logger="stapel_notifications.language"):
            with translation.override("ru"):
                assert self._process(user) == "ru"

        log = NotificationLog.objects.get(notification_type=self.TYPE, status="sent")
        assert log.data["language_source"] == "sender"
        assert log.data["recipient_language_unaskable"] is True
        assert any(
            "RECIPIENT LANGUAGE UNASKABLE" in r.message for r in caplog.records
        )


@pytest.mark.django_db
def test_translation_cache_resolves_and_formats(user, capture_email, profiles_language):
    profiles_language[str(user.id)] = ("de", None)
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

    def test_unknown_pref_field_refuses_to_send(self, user):
        """An unrecognised channel+group pair must not mean "send".

        It used to log a warning and return True, so a type whose pair has
        no field on UserNotificationSettings became mail the recipient could
        not switch off anywhere in the API — the harm E001 refuses at boot
        for the group half. Both halves of the pair are covered:
        """
        obj = UserNotificationSettings.objects.create(user_id=user.id)
        # unknown GROUP half (no email_weird_group field)
        assert _should_send("weird_group", "email", obj) is False
        # unknown CHANNEL half (no webhook_system field) — checks.E004
        assert _should_send("system", "webhook", obj) is False
        # the known pairs still answer the recipient's own preference
        assert _should_send("system", "email", obj) is True

    def test_settings_object_missing_a_known_field_refuses_to_send(self, user):
        """A settings row that has drifted from the preference vocabulary.

        ``getattr(settings_obj, pref_field, True)`` defaulted to send on a
        model that no longer carried the field — an unreadable preference
        read as consent.
        """
        class _DriftedSettings:
            email_messages = True  # every other pref field is gone

        assert _should_send("system", "email", _DriftedSettings()) is False


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


@pytest.mark.django_db
def test_channel_with_no_address_is_skipped_not_reported_as_sent():
    """A channel with nobody to deliver to must never be logged "sent".

    The shape that exposed this is the commonest one in the whole library:
    an OTP requested for an EMAIL ONLY (an unauthenticated or anonymous
    guest signing in — no account, no phone). ``otp_code`` routes to
    email+sms, ``_dispatch`` found no phone number, returned silently at
    DEBUG level, and the caller stamped the row ``sent`` to recipient
    ``"unknown"``. Nothing was sent to anybody, the audit trail said
    otherwise, and ``process_notification``'s own idempotency guard (which
    keys on status="sent") then treated that non-delivery as a completed
    one. A delivery log that cannot distinguish "delivered" from "there was
    no address" is worse than no log.
    """
    _CapturingSMSProvider.sent = []
    with override_settings(
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "SMS_PROVIDER": CAPTURE_SMS}
    ):
        process_notification(
            notification_type="otp_code",
            user_id=None,
            variables={"code": "4321", "expiry_minutes": 3},
            email="guest@example.com",
        )
    assert _CapturingSMSProvider.sent == []
    sms = NotificationLog.objects.get(channel="sms")
    assert sms.status == "skipped", sms.status
    assert "no sms address" in sms.error_message
    # The channel that DID have an address is unaffected.
    assert NotificationLog.objects.get(channel="email").status == "sent"


@pytest.mark.django_db
class TestTelegramChannel:
    """The telegram channel through the full pipeline.

    Nothing built-in routes to it and it has no delivering provider, so a
    zero-config deployment never writes to Telegram at all — every test here
    opens the channel the way a host does: a TYPES registration plus a
    dotted-path provider.
    """

    @pytest.fixture(autouse=True)
    def _reset(self):
        _CapturingTelegramProvider.sent = []

    def test_sends_to_the_chat_id_on_the_contact_projection(self, user):
        UserContact.objects.create(user_id=user.id, telegram_chat_id="123456789")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TELEGRAM_PROVIDER": CAPTURE_TELEGRAM,
                "TYPES": TELEGRAM_TYPES,
                "TEXT": {"notification.shift_reminder.telegram": "Shift at {at}"},
            }
        ):
            process_notification(
                notification_type="shift_reminder",
                user_id=str(user.id),
                variables={"at": "09:00"},
            )
        assert _CapturingTelegramProvider.sent == [("123456789", "Shift at 09:00")]
        log = NotificationLog.objects.get(channel="telegram")
        assert log.status == "sent"
        assert log.recipient == "123456789"

    def test_a_directly_passed_chat_id_wins_over_the_projection(self, user):
        """Same precedence as email/phone: the caller's address beats the
        synced one, which is what an unauthenticated flow relies on."""
        UserContact.objects.create(user_id=user.id, telegram_chat_id="111")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TELEGRAM_PROVIDER": CAPTURE_TELEGRAM,
                "TYPES": TELEGRAM_TYPES,
                "TEXT": {"notification.shift_reminder.telegram": "hi"},
            }
        ):
            process_notification(
                notification_type="shift_reminder",
                user_id=str(user.id),
                variables={},
                telegram_chat_id="222",
            )
        assert _CapturingTelegramProvider.sent == [("222", "hi")]

    def test_no_chat_id_is_skipped_not_reported_as_sent(self, user):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TELEGRAM_PROVIDER": CAPTURE_TELEGRAM,
                "TYPES": TELEGRAM_TYPES,
            }
        ):
            process_notification(
                notification_type="shift_reminder",
                user_id=str(user.id),
                variables={},
            )
        assert _CapturingTelegramProvider.sent == []
        log = NotificationLog.objects.get(channel="telegram")
        assert log.status == "skipped"
        assert "no telegram address" in log.error_message

    def test_the_closed_default_journals_failed_not_sent(self, user):
        """The channel is inert until a host names a bot client, and says so
        the way email/SMS do: status="failed", not a silent "sent"."""
        UserContact.objects.create(user_id=user.id, telegram_chat_id="123456789")
        with override_settings(STAPEL_NOTIFICATIONS={"TYPES": TELEGRAM_TYPES}):
            process_notification(
                notification_type="shift_reminder",
                user_id=str(user.id),
                variables={},
            )
        log = NotificationLog.objects.get(channel="telegram")
        assert log.status == "failed"
        assert "TELEGRAM_PROVIDER" in log.error_message

    def test_the_recipient_can_switch_it_off_like_sms(self, user):
        UserNotificationSettings.objects.create(
            user_id=user.id, telegram_system=False
        )
        UserContact.objects.create(user_id=user.id, telegram_chat_id="123456789")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TELEGRAM_PROVIDER": CAPTURE_TELEGRAM,
                "TYPES": TELEGRAM_TYPES,
            }
        ):
            process_notification(
                notification_type="shift_reminder",
                user_id=str(user.id),
                variables={},
            )
        assert _CapturingTelegramProvider.sent == []
        assert NotificationLog.objects.get(channel="telegram").status == "skipped"

    def test_the_auth_group_ignores_the_opt_out(self, user):
        UserNotificationSettings.objects.create(
            user_id=user.id, telegram_system=False, telegram_messages=False
        )
        UserContact.objects.create(user_id=user.id, telegram_chat_id="123456789")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TELEGRAM_PROVIDER": CAPTURE_TELEGRAM,
                "TYPES": TELEGRAM_TYPES,
                "TEXT": {"notification.shift_reminder_auth.telegram": "code 1234"},
            }
        ):
            process_notification(
                notification_type="shift_reminder_auth",
                user_id=str(user.id),
                variables={},
            )
        assert _CapturingTelegramProvider.sent == [("123456789", "code 1234")]

    def test_the_body_is_the_fallback_when_the_type_has_no_telegram_copy(self, user):
        """Same two-step as SMS — and deliberately NOT the `sms` string: a
        host shortens that one for the carrier, not for a chat window."""
        UserContact.objects.create(user_id=user.id, telegram_chat_id="123456789")
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TELEGRAM_PROVIDER": CAPTURE_TELEGRAM,
                "TYPES": TELEGRAM_TYPES,
                "TEXT": {
                    "notification.shift_reminder.body": "The long body",
                    "notification.shift_reminder.sms": "short",
                },
            }
        ):
            process_notification(
                notification_type="shift_reminder",
                user_id=str(user.id),
                variables={},
            )
        assert _CapturingTelegramProvider.sent == [("123456789", "The long body")]


@pytest.mark.django_db
class TestUndeliverableIsLoud:
    """A caller that queues a notification gets no synchronous signal at
    all — dispatch happens later, off a Kafka consumer, with nothing to
    hand a failure back to. Before this, "nobody could be reached" sat at
    WARNING on a per-channel NotificationLog row that nothing ever read:
    a workspace invitation got its 201, the invite was created, and the
    letter never left the building — found live on the meettoday sandbox.
    process_notification now escalates to ERROR, with a distinct greppable
    prefix, whenever NONE of a notification's routed channels reached the
    recipient for a reachability reason (as opposed to the recipient
    having opted out, which is the system working as designed)."""

    def test_total_delivery_failure_is_escalated_to_error(self, user, caplog):
        with caplog.at_level("ERROR", logger="stapel_notifications.services"):
            process_notification(
                notification_type="otp_code",  # routes to email + sms
                user_id=str(user.id),
                variables={"code": "1234", "expiry_minutes": 3},
            )
        assert any(
            "NOTIFICATION UNDELIVERABLE" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]
        # Both channels really did end up with nobody to deliver to.
        statuses = {log.channel: log.status for log in NotificationLog.objects.all()}
        assert statuses == {"email": "skipped", "sms": "skipped"}

    def test_partial_delivery_is_not_escalated(self, user, caplog):
        """One reachable channel is enough to stay quiet — the recipient
        WAS told, just not on every channel the type could have used.

        The email provider is named explicitly because the shipped default
        no longer delivers: EMAIL_PROVIDER defaults to 'unconfigured', which
        raises rather than logging a line and calling it a delivery.
        """
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with (
            override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}),
            caplog.at_level("ERROR", logger="stapel_notifications.services"),
        ):
            process_notification(
                notification_type="otp_code",
                user_id=str(user.id),
                variables={"code": "1234", "expiry_minutes": 3},
            )
        assert not [
            r for r in caplog.records if "UNDELIVERABLE" in r.getMessage()
        ]
        assert NotificationLog.objects.get(channel="email").status == "sent"

    def test_preference_opt_out_alone_is_not_escalated(self, user, caplog):
        """Every channel skipped by the recipient's OWN preference (not a
        missing address) must never look like a delivery failure."""
        UserNotificationSettings.objects.create(
            user_id=user.id, email_system=False, push_system=False
        )
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(
            STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock"}
        ):
            with caplog.at_level("ERROR", logger="stapel_notifications.services"):
                process_notification(
                    notification_type="report_reviewed",  # system: push + email
                    user_id=str(user.id),
                    variables={},
                )
        assert not [
            r for r in caplog.records if "UNDELIVERABLE" in r.getMessage()
        ]
        statuses = {log.channel: log.status for log in NotificationLog.objects.all()}
        assert statuses == {"email": "skipped", "push": "skipped"}

    def test_dispatch_exception_alone_is_also_escalated(self, user, caplog):
        """A provider that raises (not merely "no address") counts toward
        the same total-failure signal."""
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(
            STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": FAILING}
        ):
            with caplog.at_level("ERROR", logger="stapel_notifications.services"):
                process_notification(
                    notification_type="workspace.invitation",
                    user_id=str(user.id),
                    variables={
                        "workspace_name": "Acme",
                        "inviter_name": "Ada",
                        "accept_url": "https://app.example.com/invite/tok",
                    },
                )
        assert any(
            "NOTIFICATION UNDELIVERABLE" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]
        assert NotificationLog.objects.get(channel="email").status == "failed"


@pytest.mark.django_db
def test_last_resort_language_follows_the_project_not_a_hardcoded_en(
    capture_email, settings
):
    """A service built for a Russian-speaking market wants `ru` as its
    last resort; every English string it falls back to is a defect."""
    from django.utils import translation

    settings.STAPEL_LANGUAGE = {"DEFAULT": "ru"}
    with translation.override(None):
        process_notification(
            notification_type="new_device_login",
            user_id=None,
            variables={},
            email="dest@example.com",
        )
    assert NotificationLog.objects.get(
        notification_type="new_device_login"
    ).language == "ru"


# ── Zero config must not be able to claim a delivery ─────────────


@pytest.mark.django_db
def test_zero_config_otp_is_journalled_as_failed_not_sent(user, caplog):
    """The whole point of dropping the "mock" default.

    With EMAIL_PROVIDER defaulting to "mock", this exact call — a
    zero-config deployment sending a passcode to a reachable address —
    wrote a NotificationLog row reading status="sent" and stayed at INFO.
    The passcode had gone to a log line. Now the send raises, the row reads
    "failed", and the undeliverable escalation fires.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(STAPEL_NOTIFICATIONS={}):
        with caplog.at_level("ERROR", logger="stapel_notifications.services"):
            process_notification(
                notification_type="otp_code",
                user_id=str(user.id),
                variables={"code": "1234", "expiry_minutes": 3},
            )
    log = NotificationLog.objects.get(channel="email")
    assert log.status == "failed"
    assert "EMAIL_PROVIDER" in log.error_message
    assert NotificationLog.objects.filter(status="sent").count() == 0
    assert any(
        "NOTIFICATION UNDELIVERABLE" in r.getMessage() for r in caplog.records
    )
