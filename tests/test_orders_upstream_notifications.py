"""Order receipts (cart/checkout module upstream, registry-only).

``orders.placed`` and ``orders.status_changed`` go to the buyer; the CTA
renders only when the caller passes ``order_url`` (additive, like
listing_blocked's ``appeal_url``), and ``order_url`` is declared telemetry.
"""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog, UserContact
from stapel_notifications.services import process_notification


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
    "order_id": "42",
    "order_number": "20261029-ABCDEF",
    "status": "new",
    "total": "26.75",
    "currency": "USD",
}


@pytest.mark.django_db
def test_placed_states_number_and_total_with_a_link(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="orders.placed",
        user_id=str(user.id),
        variables={**VARIABLES, "order_url": "https://shop.example/orders/42"},
    )
    (mail,) = capture_email
    assert mail["subject"] == "Order 20261029-ABCDEF received"
    assert "26.75 USD" in mail["html"]
    assert 'href="https://shop.example/orders/42"' in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type="orders.placed", channel="push")
    assert log.status == "sent"
    assert log.title == "Order received"
    assert log.data.get("order_url") == "https://shop.example/orders/42"


@pytest.mark.django_db
def test_no_order_url_means_no_cta(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="orders.placed",
        user_id=str(user.id),
        variables={**VARIABLES, "order_url": ""},
    )
    (mail,) = capture_email
    assert "View order" not in mail["html"]


@pytest.mark.django_db
def test_status_changed_names_the_status(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="orders.status_changed",
        user_id=str(user.id),
        variables={**VARIABLES, "status": "shipped", "from_status": "confirmed", "reason": ""},
    )
    (mail,) = capture_email
    assert mail["subject"] == "Order 20261029-ABCDEF: shipped"
    assert "is now shipped" in mail["html"]


def test_types_are_registered_system_push_and_email():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    for ntype in ("orders.placed", "orders.status_changed"):
        assert get_group(ntype) == "system"
        assert set(get_channels(ntype)) == {"push", "email"}
        assert NOTIFICATION_ROUTING[ntype]["telemetry"] == ["order_url"]


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
