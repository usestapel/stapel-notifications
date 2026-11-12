"""Staff notice (promotions module upstream, registry-only).

``promotions.promotion_exhausted`` is what a host subscriber sends staff on
the ``promotions.promotion.exhausted`` fact (``promotion_key``, ``used``,
``usage_limit_total``); the CTA renders only with ``promotion_url``,
declared telemetry.
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


NTYPE = "promotions.promotion_exhausted"
FACT = {"promotion_id": "5", "promotion_key": "spring-sale", "usage_limit_total": 100, "used": 100}


@pytest.mark.django_db
def test_exhausted_names_the_promotion_and_its_cap(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="staff@example.com")
    process_notification(
        notification_type=NTYPE, user_id=str(user.id),
        variables={**FACT, "promotion_url": "https://admin.example/promotions/5"},
    )
    (mail,) = capture_email
    assert mail["subject"] == "Promotion spring-sale has reached its usage limit"
    assert "used 100 times and reached its limit of 100" in mail["html"]
    assert 'href="https://admin.example/promotions/5"' in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type=NTYPE, channel="push")
    assert log.title == "Promotion exhausted"
    assert log.data.get("promotion_url") == "https://admin.example/promotions/5"


@pytest.mark.django_db
def test_no_url_means_no_cta(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="staff@example.com")
    process_notification(notification_type=NTYPE, user_id=str(user.id), variables=FACT)
    (mail,) = capture_email
    assert "Review the promotion" not in mail["html"]


def test_type_is_registered_system_push_and_email():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    assert get_group(NTYPE) == "system"
    assert set(get_channels(NTYPE)) == {"push", "email"}
    assert NOTIFICATION_ROUTING[NTYPE]["telemetry"] == ["promotion_url"]
