"""Reservation notices (reservations module upstream, registry-only).

Three to the customer, one to the provider; the CTA renders only when the
caller passes ``reservation_url``, which is declared telemetry.
"""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog, UserContact
from stapel_notifications.services import process_notification

TYPES = (
    "reservations.booked",
    "reservations.status_changed",
    "reservations.reminder",
    "reservations.new_for_provider",
)


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _CapturingEmailProvider:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append({"recipient": recipient, "subject": subject, "html": html_body})


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    with override_settings(
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE, "PUSH_PROVIDER": "mock"}
    ):
        yield _CapturingEmailProvider.sent


VARIABLES = {
    "reservation_id": "7",
    "reservation_number": "20261105-ABCDEF",
    "title": "Room 4",
    "start": "2026-11-09T10:00:00+00:00",
    "start_local": "2026-11-09 11:00",
    "timezone": "Europe/Berlin",
    "status": "confirmed",
    "amount": "25.00",
    "currency": "USD",
}


@pytest.mark.django_db
def test_booked_states_title_time_and_link(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="reservations.booked",
        user_id=str(user.id),
        variables={**VARIABLES, "reservation_url": "https://book.example/reservations/7"},
    )
    (mail,) = capture_email
    assert mail["subject"] == "Booking 20261105-ABCDEF confirmed: Room 4"
    assert "2026-11-09 11:00 (Europe/Berlin)" in mail["html"]
    assert 'href="https://book.example/reservations/7"' in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type="reservations.booked", channel="push")
    assert log.status == "sent"
    assert log.title == "Booking received"
    assert log.data.get("reservation_url") == "https://book.example/reservations/7"


@pytest.mark.django_db
def test_no_reservation_url_means_no_cta(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="reservations.reminder",
        user_id=str(user.id),
        variables={**VARIABLES, "reservation_url": ""},
    )
    (mail,) = capture_email
    assert mail["subject"] == "Reminder: Room 4 at 2026-11-09 11:00"
    assert "View booking" not in mail["html"]


@pytest.mark.django_db
def test_status_changed_names_the_status(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="reservations.status_changed",
        user_id=str(user.id),
        variables={**VARIABLES, "status": "cancelled_by_provider", "from_status": "confirmed",
                   "reason": ""},
    )
    (mail,) = capture_email
    assert mail["subject"] == "Booking 20261105-ABCDEF: cancelled_by_provider"
    assert "is now cancelled_by_provider" in mail["html"]


@pytest.mark.django_db
def test_new_for_provider(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="p@example.com")
    process_notification(
        notification_type="reservations.new_for_provider",
        user_id=str(user.id),
        variables={**VARIABLES, "reservation_url": "https://book.example/agenda"},
    )
    (mail,) = capture_email
    assert mail["subject"] == "New booking 20261105-ABCDEF: Room 4"
    assert "Open the agenda" in mail["html"]


def test_types_are_registered_system_push_and_email():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    for ntype in TYPES:
        assert get_group(ntype) == "system"
        assert set(get_channels(ntype)) == {"push", "email"}
        assert NOTIFICATION_ROUTING[ntype]["telemetry"] == ["reservation_url"]


def test_types_pass_system_checks():
    from stapel_notifications.checks import (
        check_no_security_type_is_unsubscribable,
        check_notification_channels_have_a_preference,
        check_notification_groups_are_known,
        check_security_shaped_types_are_classified,
    )

    assert check_notification_groups_are_known(None) == []
    assert check_no_security_type_is_unsubscribable(None) == []
    assert check_notification_channels_have_a_preference(None) == []
    assert check_security_shaped_types_are_classified(None) == []
