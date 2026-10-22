"""The inbound seams work on an in-process bus, with no consumer process.

* ``request_notification`` (stapel-core) is a bus publish. On MemoryBus the
  ``consume_notifications`` command cannot run, so before the in-process
  subscriber every OTP, receipt and invitation returned True and went
  nowhere.
* ``profile.changed`` is a comm action from stapel-profiles; only the legacy
  topic command applied it, so an unsubscribe never reached
  ``UserNotificationSettings``.
* ``user.deletion_cancelled`` turns back on exactly what the closure grace
  period paused — not a token the push provider rejected.
"""
import types
import uuid

import pytest
from django.db import transaction
from django.test import override_settings

from stapel_core.bus import get_bus
from stapel_core.comm import emit
from stapel_core.kafka.topics import TOPIC_NOTIFICATION_REQUESTED
from stapel_core.notifications import request_notification

from stapel_notifications.intake import (
    _deliver_after_commit,
    ensure_in_process_consumer,
)
from stapel_notifications.models import (
    DevicePushToken,
    NotificationLog,
    UserContact,
    UserNotificationSettings,
)

pytestmark = pytest.mark.django_db

MOCK = {"EMAIL_PROVIDER": "mock"}


def _gdpr_export_request():
    return request_notification(
        "gdpr.export_ready",
        email="dest@example.com",
        variables={"download_url": "https://x/dl"},
        source_service="test",
    )


class TestRequestNotificationInProcess:
    def test_app_ready_subscribed_the_consumer(self):
        bus = get_bus()
        assert bus.in_process
        assert _deliver_after_commit in bus._subscribers[TOPIC_NOTIFICATION_REQUESTED]

    def test_request_is_dispatched_after_commit(self, django_capture_on_commit_callbacks):
        with override_settings(STAPEL_NOTIFICATIONS=MOCK):
            with django_capture_on_commit_callbacks(execute=True):
                assert _gdpr_export_request() is True
                # Nothing is sent before the publisher's work is durable.
                assert NotificationLog.objects.count() == 0
        log = NotificationLog.objects.get(notification_type="gdpr.export_ready")
        assert log.status == "sent"

    def test_rolled_back_request_sends_nothing(self, django_capture_on_commit_callbacks):
        with override_settings(STAPEL_NOTIFICATIONS=MOCK):
            with django_capture_on_commit_callbacks(execute=True):
                try:
                    with transaction.atomic():
                        _gdpr_export_request()
                        raise RuntimeError("caller failed")
                except RuntimeError:
                    pass
        assert NotificationLog.objects.count() == 0

    def test_subscription_is_idempotent(self):
        bus = get_bus()
        before = list(bus._subscribers[TOPIC_NOTIFICATION_REQUESTED])
        assert ensure_in_process_consumer() is True
        assert ensure_in_process_consumer(bus) is True
        assert bus._subscribers[TOPIC_NOTIFICATION_REQUESTED] == before

    def test_broker_bus_is_left_to_the_command(self):
        class BrokerBus:
            in_process = False

            def subscribe(self, *a):  # pragma: no cover - must not be called
                raise AssertionError("a broker bus is consumed by the command")

        assert ensure_in_process_consumer(BrokerBus()) is False


class TestProfileChangedAction:
    def test_unsubscribe_reaches_settings(self):
        uid = uuid.uuid4()
        emit(
            "profile.changed",
            {
                "user_id": str(uid),
                "display_name": "",
                "avatar": "",
                "location_id": None,
                "location_display_name_narrow": "",
                "location_display_name_broad": "",
                "app_language": None,
                "auto_detected_language": None,
                "email_messages": False,
                "email_system": True,
                "push_messages": True,
                "push_system": False,
            },
            key=str(uid),
        )
        row = UserNotificationSettings.objects.get(user_id=uid)
        assert row.email_messages is False
        assert row.push_system is False
        assert row.email_system is True

    def test_unusable_user_id_is_dropped_quietly(self, caplog):
        from stapel_notifications.actions import handle_profile_changed

        with caplog.at_level("ERROR"):
            handle_profile_changed(types.SimpleNamespace(
                payload={"user_id": "not-a-uuid", "email_messages": False},
                event_id="e1",
            ))
        assert UserNotificationSettings.objects.count() == 0


def _ev(name, user_id):
    return types.SimpleNamespace(
        payload={"user_id": user_id, "trigger": "manual"}, event_id=f"evt-{name}",
    )


class TestDeletionCancelled:
    def test_cancel_restores_exactly_what_the_grace_period_paused(self):
        from stapel_notifications.actions import (
            handle_user_deletion_cancelled,
            handle_user_deletion_initiated,
        )

        uid = uuid.uuid4()
        UserContact.objects.create(user_id=uid, email="u@example.com")
        DevicePushToken.objects.create(user_id=uid, token="live", platform="ios")
        # Rejected by the push provider before the closure: stays dead.
        DevicePushToken.objects.create(
            user_id=uid, token="rejected", platform="ios", is_active=False,
        )

        handle_user_deletion_initiated(_ev("init", str(uid)))
        assert not UserContact.objects.get(user_id=uid).is_active
        assert not DevicePushToken.objects.get(token="live").is_active

        handle_user_deletion_cancelled(_ev("cancel", str(uid)))
        assert UserContact.objects.get(user_id=uid).is_active
        assert DevicePushToken.objects.get(token="live").is_active
        assert not DevicePushToken.objects.get(token="rejected").is_active

        # Redelivery changes nothing.
        handle_user_deletion_cancelled(_ev("cancel", str(uid)))
        assert DevicePushToken.objects.get(token="live").closure_paused_at is None

    def test_cancel_arrives_through_the_comm_plane(self):
        from stapel_notifications.actions import handle_user_deletion_initiated

        uid = uuid.uuid4()
        UserContact.objects.create(user_id=uid, email="u@example.com")
        handle_user_deletion_initiated(_ev("init", str(uid)))
        emit(
            "user.deletion_cancelled",
            {"user_id": str(uid), "cancelled_at": "2026-10-22T10:00:00+00:00",
             "trigger": "manual"},
            key=str(uid),
        )
        assert UserContact.objects.get(user_id=uid).is_active

    @pytest.mark.parametrize("name", ["init", "cancel"])
    def test_non_uuid_user_id_does_not_raise(self, name, caplog):
        from stapel_notifications.actions import (
            handle_user_deletion_cancelled,
            handle_user_deletion_initiated,
        )

        handler = (
            handle_user_deletion_initiated if name == "init"
            else handle_user_deletion_cancelled
        )
        with caplog.at_level("ERROR"):
            handler(_ev(name, "not-a-uuid"))
        assert any("unusable user_id" in r.message for r in caplog.records)


class TestDsarTypes:
    @pytest.mark.parametrize("ntype,variables", [
        ("gdpr.dsar.received", {
            "kind": "access", "received_at": "2026-10-22T10:00:00+00:00",
            "resolve_due_at": "2026-11-21T10:00:00+00:00",
            "resolve_due_date": "2026-11-21", "reference": "ref-1",
        }),
        ("gdpr.dsar.opened", {
            "kind": "erasure", "channel": "email", "subject_email": "s@example.com",
            "resolve_due_at": "2026-11-21T10:00:00+00:00",
            "resolve_due_date": "2026-11-21", "reference": "ref-2",
        }),
    ])
    def test_dsar_letters_are_registered_and_sent(self, ntype, variables):
        from stapel_notifications.routing import get_routing
        from stapel_notifications.services import process_notification

        assert get_routing(ntype), ntype
        with override_settings(STAPEL_NOTIFICATIONS=MOCK):
            process_notification(
                notification_type=ntype, user_id=None, variables=variables,
                email="dest@example.com",
            )
        log = NotificationLog.objects.get(notification_type=ntype)
        assert log.status == "sent"
