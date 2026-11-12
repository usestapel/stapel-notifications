"""Shipment notices (delivery module upstream, registry-only).

``delivery.shipment_status_changed`` goes to the buyer with the variables the
producer sends (``stapel_delivery.services.events.variables``). The tracking
line renders only with ``tracking_number``, the tracking link only with
``tracking_url``, the CTA only with ``shipment_url``; both links are declared
telemetry.
"""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog, UserContact
from stapel_notifications.services import process_notification

NTYPE = "delivery.shipment_status_changed"


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
    "shipment_id": "7",
    "shipment_number": "SH-20261112-0007",
    "order_id": "42",
    "method": "Courier",
    "status": "shipped",
    "from_status": "packed",
    "tracking_number": "1Z999AA10123456784",
    "tracking_url": "https://carrier.example/track/1Z999AA10123456784",
    "shipment_url": "https://shop.example/shipments/7",
}


@pytest.mark.django_db
def test_status_changed_names_the_shipment_status_and_tracking(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(notification_type=NTYPE, user_id=str(user.id), variables=VARIABLES)
    (mail,) = capture_email
    assert mail["subject"] == "Shipment SH-20261112-0007: shipped"
    assert "Tracking number: 1Z999AA10123456784" in mail["html"]
    assert 'href="https://shop.example/shipments/7"' in mail["html"]
    assert 'href="https://carrier.example/track/1Z999AA10123456784"' in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type=NTYPE, channel="push")
    assert log.status == "sent" and log.title == "Delivery updated"
    assert log.data.get("shipment_url") == "https://shop.example/shipments/7"
    # Declared, so journalled; an opaque carrier id in the path reads as a
    # credential to the redactor, which is the redactor's call to make.
    assert "tracking_url" in log.data


@pytest.mark.django_db
def test_no_tracking_and_no_links_render_nothing_extra(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type=NTYPE, user_id=str(user.id),
        variables={**VARIABLES, "tracking_number": "", "tracking_url": "", "shipment_url": ""},
    )
    (mail,) = capture_email
    assert "Tracking number" not in mail["html"]
    assert "View shipment" not in mail["html"] and "Track the parcel" not in mail["html"]


def test_type_is_registered_system_push_and_email():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    assert get_group(NTYPE) == "system"
    assert set(get_channels(NTYPE)) == {"push", "email"}
    assert NOTIFICATION_ROUTING[NTYPE]["telemetry"] == ["shipment_url", "tracking_url"]
