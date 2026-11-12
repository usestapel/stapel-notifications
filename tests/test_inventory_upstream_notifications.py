"""Staff stock alerts (inventory module upstream, registry-only).

``inventory.stock_low`` and ``inventory.stock_out`` are what a host
subscriber sends staff on the ``inventory.stock.low`` / ``.out`` facts
(``purchasable_key``, ``location``, ``available``, ``threshold``); the CTA
renders only with ``stock_url``, declared telemetry.
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


FACT = {
    "stock_item_id": "11",
    "purchasable_type": "product",
    "purchasable_key": "mug-blue",
    "location": "main",
    "on_hand": 3,
    "available": 2,
    "threshold": 5,
}


def _send(user, ntype, **extra):
    UserContact.objects.get_or_create(user_id=user.id, defaults={"email": "staff@example.com"})
    process_notification(notification_type=ntype, user_id=str(user.id), variables={**FACT, **extra})


@pytest.mark.django_db
def test_low_names_item_location_and_threshold(user, capture_email):
    _send(user, "inventory.stock_low", stock_url="https://admin.example/stock/11")
    (mail,) = capture_email
    assert mail["subject"] == "Low stock: mug-blue"
    assert "mug-blue at main is down to 2 available (threshold 5)." in mail["html"]
    assert 'href="https://admin.example/stock/11"' in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type="inventory.stock_low", channel="push")
    assert log.title == "Low stock"
    assert log.data.get("stock_url") == "https://admin.example/stock/11"


@pytest.mark.django_db
def test_out_names_item_and_location(user, capture_email):
    _send(user, "inventory.stock_out", available=0)
    (mail,) = capture_email
    assert mail["subject"] == "Out of stock: mug-blue"
    assert "mug-blue at main has no units available." in mail["html"]
    assert "Review stock" not in mail["html"]


def test_types_are_registered_system_push_and_email():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    for ntype in ("inventory.stock_low", "inventory.stock_out"):
        assert get_group(ntype) == "system"
        assert set(get_channels(ntype)) == {"push", "email"}
        assert NOTIFICATION_ROUTING[ntype]["telemetry"] == ["stock_url"]
