"""Community activity types (feed module upstream, registry-only).

``feed.comment_received`` (push + email), ``feed.reaction_received`` and
``feed.new_post_from_followed`` (push) go to a post's author, in the
switch-off-able ``messages`` group; the email CTA renders only with
``post_url``, which is declared telemetry.
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


VARIABLES = {"post_id": "7", "comment_id": "9", "actor_id": "someone"}


@pytest.mark.django_db
def test_comment_received_mails_with_a_link(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="feed.comment_received",
        user_id=str(user.id),
        variables={**VARIABLES, "post_url": "https://community.example/p/7"},
    )
    (mail,) = capture_email
    assert mail["subject"] == "New comment on your post"
    assert 'href="https://community.example/p/7"' in mail["html"]
    log = NotificationLog.objects.get(
        user_id=user.id, notification_type="feed.comment_received", channel="push"
    )
    assert log.status == "sent"
    assert log.title == "New comment"
    assert log.data.get("post_url") == "https://community.example/p/7"


@pytest.mark.django_db
def test_no_post_url_means_no_cta(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="feed.comment_received",
        user_id=str(user.id),
        variables={**VARIABLES, "post_url": ""},
    )
    (mail,) = capture_email
    assert "View post" not in mail["html"]


@pytest.mark.django_db
@pytest.mark.parametrize("ntype,title", [
    ("feed.reaction_received", "New reaction"),
    ("feed.new_post_from_followed", "New post"),
])
def test_push_only_types_send_no_mail(user, capture_email, ntype, title):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(notification_type=ntype, user_id=str(user.id), variables=dict(VARIABLES))
    assert capture_email == []
    log = NotificationLog.objects.get(user_id=user.id, notification_type=ntype, channel="push")
    assert log.title == title


def test_types_are_registered_in_the_messages_group():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    expected = {
        "feed.comment_received": {"push", "email"},
        "feed.reaction_received": {"push"},
        "feed.new_post_from_followed": {"push"},
    }
    for ntype, channels in expected.items():
        assert get_group(ntype) == "messages"
        assert set(get_channels(ntype)) == channels
        assert NOTIFICATION_ROUTING[ntype]["telemetry"] == ["post_url"]


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
