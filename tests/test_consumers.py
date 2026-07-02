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
class TestTranslationsChangedAction:
    """@on_action("translations.changed"): thin invalidation + resolve pull."""

    @pytest.fixture
    def fake_resolve(self, function_registry_sandbox):
        """Register a fake translate.resolve provider (exact loop contract:
        {keys, language} → {"values": {key: text}}, missing keys omitted)."""
        from stapel_core.comm import register_function

        store = {
            ("notification.otp_code.heading", "de"): "Kode",
            ("notification.otp_code.heading", "en"): "Code",
        }
        calls = []

        def resolve(payload):
            calls.append(payload)
            values = {
                key: store[(key, payload["language"])]
                for key in payload["keys"]
                if (key, payload["language"]) in store
            }
            return {"values": values}

        with function_registry_sandbox._lock:
            function_registry_sandbox._providers.pop("translate.resolve", None)
        register_function("translate.resolve", resolve)
        return calls

    def _deliver(self, payload):
        from stapel_notifications.actions import handle_translations_changed

        handle_translations_changed(_event("translations.changed", payload))

    def test_pulls_values_and_merges_language(self, fake_resolve):
        TranslationCache.objects.create(
            key="notification.otp_code.heading", values={"en": "Code"}
        )
        self._deliver(
            {"language": "de", "keys_changed": ["notification.otp_code.heading"]}
        )
        row = TranslationCache.objects.get(key="notification.otp_code.heading")
        assert row.values == {"en": "Code", "de": "Kode"}  # merged, not replaced

    def test_non_notification_keys_are_filtered_out(self, fake_resolve):
        self._deliver(
            {
                "language": "de",
                "keys_changed": ["profile.title", "notification.otp_code.heading"],
            }
        )
        (call_payload,) = fake_resolve
        assert call_payload["keys"] == ["notification.otp_code.heading"]
        assert TranslationCache.objects.count() == 1

    def test_no_notification_keys_means_no_call(self, fake_resolve):
        self._deliver({"language": "de", "keys_changed": ["profile.title"]})
        assert fake_resolve == []
        assert TranslationCache.objects.count() == 0

    def test_keys_missing_in_translate_are_omitted(self, fake_resolve):
        self._deliver(
            {"language": "de", "keys_changed": ["notification.unknown.key"]}
        )
        assert TranslationCache.objects.count() == 0

    def test_resolve_failure_propagates_for_retry(self, function_registry_sandbox):
        from stapel_core.comm.exceptions import FunctionNotRegistered

        with function_registry_sandbox._lock:
            function_registry_sandbox._providers.pop("translate.resolve", None)
        with pytest.raises(FunctionNotRegistered):
            self._deliver(
                {"language": "de", "keys_changed": ["notification.otp_code.heading"]}
            )


@pytest.mark.django_db
class TestUserDeletionInitiatedAction:
    def test_soft_deactivates_contact_and_push_tokens(self):
        import uuid as _uuid

        from stapel_notifications.actions import handle_user_deletion_initiated
        from stapel_notifications.models import DevicePushToken

        uid = _uuid.uuid4()
        UserContact.objects.create(user_id=uid, email="u@example.com", phone="+451")
        DevicePushToken.objects.create(user_id=uid, token="tok-1", platform="ios")

        handle_user_deletion_initiated(
            _event("user.deletion_initiated", {"user_id": str(uid), "trigger": "manual"})
        )
        contact = UserContact.objects.get(user_id=uid)
        assert contact.is_active is False
        assert contact.email == "u@example.com"  # soft: PII kept until user.deleted
        assert DevicePushToken.objects.get(token="tok-1").is_active is False

    def test_missing_user_id_is_logged_and_ignored(self, caplog):
        from stapel_notifications.actions import handle_user_deletion_initiated

        with caplog.at_level("ERROR"):
            handle_user_deletion_initiated(
                _event("user.deletion_initiated", {"trigger": "manual"})
            )
        assert any("without user_id" in r.message for r in caplog.records)

    def test_inactive_contact_is_not_used_as_recipient(self, user):
        from django.test import override_settings

        from stapel_notifications.services import process_notification

        UserContact.objects.create(
            user_id=user.id, email="u@example.com", is_active=False
        )
        with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"}):
            process_notification(
                notification_type="new_device_login",
                user_id=str(user.id),
                variables={},
            )
        # contact deactivated → no recipient resolved → nothing sent
        assert NotificationLog.objects.get(channel="email").recipient == "unknown"

    def test_contact_sync_reactivates(self):
        import uuid as _uuid

        uid = _uuid.uuid4()
        UserContact.objects.create(user_id=uid, email="a@example.com", is_active=False)
        ContactsCommand().handle_event(
            _event(EventType.USER_CONTACT_CHANGED, {"user_id": str(uid), "email": "b@example.com"})
        )
        contact = UserContact.objects.get(user_id=uid)
        assert contact.is_active is True
        assert contact.email == "b@example.com"
