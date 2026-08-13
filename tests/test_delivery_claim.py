"""Idempotency claimed in the database, not checked in Python.

NOTIFY-02 (security audit 2026-08-11): delivery idempotency was a
non-atomic ``NotificationLog.objects.filter(data__event_id=..., status=
"sent").exists()`` — two consumers handed the same event both read "no row
yet" and both sent — and it answered for the whole event, so one channel's
success suppressed every other channel's retry.
"""
import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings

from stapel_notifications import delivery
from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import (
    NotificationDelivery,
    NotificationLog,
    UserContact,
)
from stapel_notifications.services import process_notification


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _CapturingEmailProvider:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append(recipient)


class _CapturingSMSProvider:
    sent = []

    def send(self, phone, body):
        type(self).sent.append(phone)


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"
CAPTURE_SMS = f"{_CapturingSMSProvider.__module__}._CapturingSMSProvider"
KEY = dict(event_id="evt-1", channel="email", recipient="a@b.c", template_version="t")


@pytest.mark.django_db
class TestTheClaimIsTheDatabases:
    def test_the_constraint_is_enforced_by_the_database(self):
        """Not "we looked and there was nothing" — the table refuses it."""
        NotificationDelivery.objects.create(**KEY)
        with pytest.raises(IntegrityError), transaction.atomic():
            NotificationDelivery.objects.create(**KEY)

    def test_a_second_claim_is_refused_while_the_first_is_in_flight(self):
        assert delivery.claim(**KEY) is True
        assert delivery.claim(**KEY) is False  # still "claimed", not yet delivered

    def test_a_released_claim_can_be_taken_again(self):
        delivery.claim(**KEY)
        delivery.release(**KEY)
        assert delivery.claim(**KEY) is True

    def test_a_confirmed_delivery_is_never_released(self):
        delivery.claim(**KEY)
        delivery.confirm(**KEY)
        delivery.release(**KEY)
        assert delivery.claim(**KEY) is False

    def test_a_dead_holders_claim_is_taken_over_after_the_ttl(self):
        delivery.claim(**KEY)
        with override_settings(STAPEL_NOTIFICATIONS={"DELIVERY_CLAIM_TTL": 0}):
            assert delivery.claim(**KEY) is True

    def test_no_event_id_means_nothing_to_deduplicate_on(self):
        assert delivery.claim(None, "email", "a@b.c", "t") is True
        assert delivery.claim(None, "email", "a@b.c", "t") is True
        assert NotificationDelivery.objects.count() == 0


@pytest.mark.django_db
def test_one_channels_success_no_longer_suppresses_another_channels_retry(user):
    """The coarseness half of NOTIFY-02, as the product sees it.

    A passcode routes to email and SMS. The first delivery reaches the
    email, and there is no phone number yet, so SMS is skipped. The phone
    number arrives, the broker redelivers the same event — and under the
    old whole-event ``.exists()`` guard the SMS was suppressed forever by
    the email row that had nothing to do with it.
    """
    _CapturingEmailProvider.sent = []
    _CapturingSMSProvider.sent = []
    contact = UserContact.objects.create(user_id=user.id, email="u@example.com")
    conf = {"EMAIL_PROVIDER": CAPTURE, "SMS_PROVIDER": CAPTURE_SMS}

    with override_settings(STAPEL_NOTIFICATIONS=conf):
        process_notification(
            notification_type="otp_code", user_id=str(user.id),
            variables={"code": "1234", "expiry_minutes": 5}, event_id="evt-otp",
        )
    assert _CapturingEmailProvider.sent == ["u@example.com"]
    assert _CapturingSMSProvider.sent == []

    contact.phone = "+15550000001"
    contact.save()
    with override_settings(STAPEL_NOTIFICATIONS=conf):
        process_notification(
            notification_type="otp_code", user_id=str(user.id),
            variables={"code": "1234", "expiry_minutes": 5}, event_id="evt-otp",
        )
    assert _CapturingSMSProvider.sent == ["+15550000001"]      # the retry got through
    assert _CapturingEmailProvider.sent == ["u@example.com"]   # and did not double-send


@pytest.mark.django_db
def test_a_duplicate_event_still_sends_once(user):
    """The property the old check had, kept."""
    _CapturingEmailProvider.sent = []
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        for _ in range(3):
            process_notification(
                notification_type="gdpr.export_ready", user_id=None,
                variables={"download_url": "https://x/dl"},
                email="dest@example.com", event_id="evt-dup",
            )
    assert _CapturingEmailProvider.sent == ["dest@example.com"]
    assert NotificationLog.objects.filter(status="sent").count() == 1
