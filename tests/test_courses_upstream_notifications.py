"""Learner notices (courses module upstream, registry-only).

``courses.enrollment_activated``, ``courses.completed`` and
``courses.certificate_issued`` carry the variables the producer sends
(``stapel_courses.services.events.variables``); the CTA renders only with
``course_url`` (certificate: ``certificate_url``), declared telemetry.
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
    "course_id": "3",
    "course_slug": "intro-to-pottery",
    "course_title": "Intro to Pottery",
    "course_url": "https://school.example/courses/intro-to-pottery",
}


def _send(user, ntype, **extra):
    UserContact.objects.get_or_create(user_id=user.id, defaults={"email": "u@example.com"})
    process_notification(notification_type=ntype, user_id=str(user.id), variables={**VARIABLES, **extra})


@pytest.mark.django_db
def test_enrollment_activated_welcomes_with_a_link(user, capture_email):
    _send(user, "courses.enrollment_activated")
    (mail,) = capture_email
    assert mail["subject"] == "You're enrolled in Intro to Pottery"
    assert 'href="https://school.example/courses/intro-to-pottery"' in mail["html"]
    log = NotificationLog.objects.get(
        user_id=user.id, notification_type="courses.enrollment_activated", channel="push",
    )
    assert log.title == "Enrollment active"
    assert log.data.get("course_url") == "https://school.example/courses/intro-to-pottery"


@pytest.mark.django_db
def test_completed_congratulates(user, capture_email):
    _send(user, "courses.completed")
    (mail,) = capture_email
    assert mail["subject"] == "You completed Intro to Pottery"
    assert "Congratulations!" in mail["html"]


@pytest.mark.django_db
def test_certificate_issued_names_the_number_and_links_the_certificate(user, capture_email):
    _send(user, "courses.certificate_issued", certificate_number="CERT-2026-000042",
          certificate_url="https://school.example/certificates/CERT-2026-000042")
    (mail,) = capture_email
    assert "CERT-2026-000042" in mail["html"]
    assert 'href="https://school.example/certificates/CERT-2026-000042"' in mail["html"]


@pytest.mark.django_db
def test_no_url_means_no_cta(user, capture_email):
    _send(user, "courses.enrollment_activated", course_url="")
    (mail,) = capture_email
    assert "Open the course" not in mail["html"]


def test_types_are_registered_system_push_and_email():
    from stapel_notifications.routing import NOTIFICATION_ROUTING, get_channels, get_group

    for ntype, link in (("courses.enrollment_activated", "course_url"),
                        ("courses.completed", "course_url"),
                        ("courses.certificate_issued", "certificate_url")):
        assert get_group(ntype) == "system"
        assert set(get_channels(ntype)) == {"push", "email"}
        assert NOTIFICATION_ROUTING[ntype]["telemetry"] == [link]
