"""GDPR provider, user.deleted action handler, admin registration, __str__."""

import types
import uuid

import pytest

from stapel_notifications.gdpr import NotificationsGDPRProvider
from stapel_notifications.models import (
    DevicePushToken,
    NotificationLog,
    TranslationCache,
    UserContact,
    UserNotificationSettings,
)


def _seed(user_id):
    UserContact.objects.create(user_id=user_id, email="u@example.com", phone="+45999")
    UserNotificationSettings.objects.create(
        user_id=user_id, language="de", email_system=False
    )
    DevicePushToken.objects.create(user_id=user_id, token=f"tok-{user_id}", platform="ios")
    return NotificationLog.objects.create(
        user_id=user_id,
        notification_type="new_message",
        channel="email",
        status="sent",
        language="de",
        recipient="u@example.com",
    )


@pytest.mark.django_db
class TestGDPRExport:
    def test_export_with_full_data(self):
        uid = uuid.uuid4()
        _seed(uid)
        data = NotificationsGDPRProvider().export(uid)
        assert data["contact"] == {"email": "u@example.com", "phone": "+45999"}
        assert data["settings"]["language"] == "de"
        assert data["settings"]["email_system"] is False
        assert len(data["devices"]) == 1
        assert data["devices"][0]["platform"] == "ios"
        assert isinstance(data["devices"][0]["created_at"], str)  # serialized
        assert len(data["log"]) == 1
        assert data["log"][0]["channel"] == "email"
        assert "recipient" not in data["log"][0]  # log export carries no PII

    def test_export_without_data(self):
        data = NotificationsGDPRProvider().export(uuid.uuid4())
        assert data == {"contact": {}, "settings": {}, "devices": [], "log": []}


@pytest.mark.django_db
class TestGDPRDelete:
    def test_delete_erases_pii_but_keeps_audit_log(self):
        uid = uuid.uuid4()
        log = _seed(uid)
        NotificationsGDPRProvider().delete(uid)
        assert not UserContact.objects.filter(user_id=uid).exists()
        assert not UserNotificationSettings.objects.filter(user_id=uid).exists()
        assert not DevicePushToken.objects.filter(user_id=uid).exists()
        log.refresh_from_db()
        assert log.recipient == ""  # anonymised, not deleted
        assert log.user_id is None
        assert log.status == "sent"  # delivery audit trail preserved

    def test_anonymize_is_noop(self):
        assert NotificationsGDPRProvider().anonymize(uuid.uuid4()) is None


@pytest.mark.django_db
class TestUserDeletedAction:
    def _event(self, payload):
        return types.SimpleNamespace(payload=payload, event_id="evt-act-1")

    def test_handle_user_deleted_erases_pii(self):
        from stapel_notifications.actions import handle_user_deleted

        uid = uuid.uuid4()
        _seed(uid)
        handle_user_deleted(self._event({"user_id": uid}))
        assert not UserContact.objects.filter(user_id=uid).exists()

    def test_handle_user_deleted_without_user_id_logs_and_returns(self, caplog):
        from stapel_notifications.actions import handle_user_deleted

        with caplog.at_level("ERROR", logger="stapel_notifications.actions"):
            handle_user_deleted(self._event({}))
        assert any("without user_id" in r.message for r in caplog.records)


def test_admin_registers_all_models():
    from django.contrib import admin

    from stapel_notifications import admin as notifications_admin  # noqa: F401

    for model in (
        UserNotificationSettings,
        UserContact,
        TranslationCache,
        NotificationLog,
        DevicePushToken,
    ):
        assert model in admin.site._registry


@pytest.mark.django_db
def test_model_str_representations():
    uid = uuid.uuid4()
    log = _seed(uid)
    tc = TranslationCache.objects.create(key="notification.x.y", values={"en": "x"})
    assert str(uid) in str(UserContact.objects.get(user_id=uid))
    assert str(uid) in str(UserNotificationSettings.objects.get(user_id=uid))
    assert str(tc) == "notification.x.y"
    assert "new_message/email" in str(log)
    token = DevicePushToken.objects.get(user_id=uid)
    assert "ios:" in str(token)
