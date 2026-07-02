"""Bus consumer commands: handle_event routing + DB effects.

Handlers are exercised directly with bus Event envelopes (the MemoryBus
delivery loop is transport plumbing owned by stapel-core).
"""

import uuid

import pytest
from django.test import override_settings

from stapel_core.bus import Event
from stapel_core.kafka.events import EventType

from stapel_notifications.management.commands.consume_contacts import (
    Command as ContactsCommand,
)
from stapel_notifications.management.commands.consume_notifications import (
    Command as NotificationsCommand,
)
from stapel_notifications.management.commands.consume_profiles import (
    Command as ProfilesCommand,
)
from stapel_notifications.management.commands.consume_translations import (
    Command as TranslationsCommand,
)
from stapel_notifications.models import (
    NotificationLog,
    TranslationCache,
    UserContact,
    UserNotificationSettings,
)


def _event(event_type, payload, service="test"):
    return Event(event_type=event_type, service=service, payload=payload)


@pytest.mark.django_db
class TestConsumeNotifications:
    def test_dispatches_and_logs_with_event_id(self):
        event = _event(
            EventType.NOTIFICATION_REQUESTED,
            {
                "notification_type": "gdpr.export_ready",
                "email": "dest@example.com",
                "variables": {"download_url": "https://x/dl"},
            },
        )
        with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"}):
            NotificationsCommand().handle_event(event)
        log = NotificationLog.objects.get(notification_type="gdpr.export_ready")
        assert log.status == "sent"
        assert log.data["event_id"] == event.event_id  # idempotency key persisted

    def test_missing_notification_type_is_dropped(self, caplog):
        with caplog.at_level("ERROR"):
            NotificationsCommand().handle_event(
                _event(EventType.NOTIFICATION_REQUESTED, {"email": "x@example.com"})
            )
        assert NotificationLog.objects.count() == 0
        assert any("missing notification_type" in r.message for r in caplog.records)

    def test_unknown_event_type_is_ignored(self, caplog):
        with caplog.at_level("WARNING"):
            NotificationsCommand().handle_event(_event("something.else", {}))
        assert NotificationLog.objects.count() == 0
        assert any("Unknown event type" in r.message for r in caplog.records)


@pytest.mark.django_db
class TestConsumeContacts:
    def test_creates_and_updates_contact(self):
        uid = uuid.uuid4()
        ContactsCommand().handle_event(
            _event(
                EventType.USER_CONTACT_CHANGED,
                {"user_id": str(uid), "email": "a@example.com", "phone": "+451111"},
            )
        )
        contact = UserContact.objects.get(user_id=uid)
        assert contact.email == "a@example.com"
        assert contact.phone == "+451111"

        # partial update: only email present; None coerced to ""
        ContactsCommand().handle_event(
            _event(EventType.USER_CONTACT_CHANGED, {"user_id": str(uid), "email": None})
        )
        contact.refresh_from_db()
        assert contact.email == ""
        assert contact.phone == "+451111"  # untouched

    def test_missing_user_id_is_ignored(self):
        ContactsCommand().handle_event(
            _event(EventType.USER_CONTACT_CHANGED, {"email": "a@example.com"})
        )
        assert UserContact.objects.count() == 0

    def test_payload_without_fields_writes_nothing(self):
        ContactsCommand().handle_event(
            _event(EventType.USER_CONTACT_CHANGED, {"user_id": str(uuid.uuid4())})
        )
        assert UserContact.objects.count() == 0

    def test_unknown_event_type_is_ignored(self):
        ContactsCommand().handle_event(_event("something.else", {"user_id": "x"}))
        assert UserContact.objects.count() == 0


@pytest.mark.django_db
class TestConsumeProfiles:
    def test_syncs_language_and_preferences(self):
        uid = uuid.uuid4()
        ProfilesCommand().handle_event(
            _event(
                EventType.PROFILE_CHANGED,
                {
                    "user_id": str(uid),
                    "app_language": "de",
                    "auto_detected_language": "da",
                    "email_system": False,
                    "sms_messages": False,
                },
            )
        )
        settings_obj = UserNotificationSettings.objects.get(user_id=uid)
        assert settings_obj.language == "de"
        assert settings_obj.auto_detected_language == "da"
        assert settings_obj.email_system is False
        assert settings_obj.sms_messages is False
        assert settings_obj.email_messages is True  # untouched default

    def test_empty_app_language_becomes_null(self):
        uid = uuid.uuid4()
        ProfilesCommand().handle_event(
            _event(EventType.PROFILE_CHANGED, {"user_id": str(uid), "app_language": ""})
        )
        assert UserNotificationSettings.objects.get(user_id=uid).language is None

    def test_missing_user_id_is_ignored(self):
        ProfilesCommand().handle_event(
            _event(EventType.PROFILE_CHANGED, {"app_language": "de"})
        )
        assert UserNotificationSettings.objects.count() == 0

    def test_unknown_event_type_is_ignored(self):
        ProfilesCommand().handle_event(_event("something.else", {"user_id": "x"}))
        assert UserNotificationSettings.objects.count() == 0


@pytest.mark.django_db
class TestConsumeTranslations:
    def test_caches_notification_keys(self):
        TranslationsCommand().handle_event(
            _event(
                EventType.TRANSLATIONS_CHANGED,
                {
                    "key": "notification.otp_code.heading",
                    "values": {"en": "Code", "de": "Kode"},
                },
            )
        )
        row = TranslationCache.objects.get(key="notification.otp_code.heading")
        assert row.values == {"en": "Code", "de": "Kode"}

    def test_non_notification_keys_are_ignored(self):
        TranslationsCommand().handle_event(
            _event(
                EventType.TRANSLATIONS_CHANGED,
                {"key": "profile.title", "values": {"en": "x"}},
            )
        )
        assert TranslationCache.objects.count() == 0

    def test_invalid_payload_is_ignored(self, caplog):
        with caplog.at_level("WARNING"):
            TranslationsCommand().handle_event(
                _event(
                    EventType.TRANSLATIONS_CHANGED,
                    {"key": "notification.x", "values": "not-a-dict"},
                )
            )
        assert TranslationCache.objects.count() == 0
        assert any("Invalid translations-changed" in r.message for r in caplog.records)

    def test_unknown_event_type_is_ignored(self):
        TranslationsCommand().handle_event(_event("something.else", {}))
        assert TranslationCache.objects.count() == 0
