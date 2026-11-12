"""Reward notice (referrals module upstream, registry-only).

``referrals.reward_granted`` carries what the producer sends
(``stapel_referrals.services.events.reward_granted_after_commit``): ``role``,
``value``, ``unit`` and, for a promo-code reward, the code as ``grant_ref``.
The credits line renders only for ``unit == "credits"``, the code line only
with ``grant_ref``.
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


NTYPE = "referrals.reward_granted"


def _send(user, **variables):
    UserContact.objects.get_or_create(user_id=user.id, defaults={"email": "u@example.com"})
    process_notification(notification_type=NTYPE, user_id=str(user.id),
                         variables={"reward_id": "9", "role": "referrer", **variables})


@pytest.mark.django_db
def test_a_credit_reward_names_the_amount(user, capture_email):
    _send(user, value="50.00", unit="credits", grant_ref="")
    (mail,) = capture_email
    assert mail["subject"] == "You've earned a referral reward"
    assert "50.00 credits have been added to your balance." in mail["html"]
    assert "promo code" not in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type=NTYPE, channel="push")
    assert log.status == "sent" and log.title == "Referral reward"


@pytest.mark.django_db
def test_a_code_reward_shows_the_code_and_no_credits(user, capture_email):
    _send(user, value="10.00", unit="code", grant_ref="FRIEND-7Q2X")
    (mail,) = capture_email
    assert "Your promo code: FRIEND-7Q2X" in mail["html"]
    assert "credits" not in mail["html"]


def test_type_is_registered_system_push_and_email():
    from stapel_notifications.routing import get_channels, get_group

    assert get_group(NTYPE) == "system"
    assert set(get_channels(NTYPE)) == {"push", "email"}
