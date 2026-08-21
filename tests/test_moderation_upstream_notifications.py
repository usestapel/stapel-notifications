"""stapel-moderation upstream notifications (tasks/stapel-moderation-design.md
§16.3, §6.3).

Covers the four notification types that release adds/changes, registry-only
(no producer lives in this package):

  - ``moderation.report_received`` -- new, receipt to the reporter
    (DSA Art. 16(4)).
  - ``moderation.sanction_issued`` -- new, the decision on a sanctioned
    account with its statement of reasons and appeal path (DSA Art. 17).
    ``expires_label``/``appeal_url`` are additive: the expiry line and the
    appeal CTA only render when the caller passes them.
  - ``moderation.appeal_resolved`` -- new, the outcome to the appellant
    (DSA Art. 20). No link -- the type carries no notifications_chat_url.
  - ``listing_blocked`` -- existing type gains ``reason_label``/
    ``appeal_url``, additive same as the three above: omitting them renders
    byte-identical to before this release. ``appeal_url`` is declared
    telemetry so it survives into ``NotificationLog.data``.
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
        type(self).sent.append(
            {"recipient": recipient, "subject": subject, "html": html_body, "headers": headers}
        )


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    return _CapturingEmailProvider.sent


@pytest.fixture
def push_and_email():
    """Both channels of the "system" group route mock -- these four types
    (like report_reviewed/listing_blocked) are push + email."""
    with override_settings(
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE, "PUSH_PROVIDER": "mock"}
    ):
        yield


# ── moderation.report_received ──────────────────────────────────


@pytest.mark.django_db
def test_report_received_renders_target_and_case_ref(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.report_received",
        user_id=str(user.id),
        variables={
            "target_label": "a listing",
            "case_ref": "CASE-42",
            "notifications_chat_url": "https://x.example/cases/CASE-42",
        },
    )
    (mail,) = capture_email
    assert "a listing" in mail["html"]
    assert "CASE-42" in mail["html"]
    log = NotificationLog.objects.get(user_id=user.id, notification_type="moderation.report_received", channel="push")
    assert log.status == "sent"
    assert log.title == "Report received"


# ── moderation.sanction_issued ──────────────────────────────────


@pytest.mark.django_db
def test_sanction_issued_states_kind_and_reason(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.sanction_issued",
        user_id=str(user.id),
        variables={
            "sanction_kind": "posting restriction",
            "reason_label": "off-platform payment",
        },
    )
    (mail,) = capture_email
    assert "posting restriction" in mail["html"]
    assert "off-platform payment" in mail["html"]


@pytest.mark.django_db
def test_sanction_issued_without_expiry_or_appeal_omits_both(push_and_email, user, capture_email):
    """No expires_label, no appeal_url: no expiry line, no CTA button --
    same additive contract as listing_blocked's reason_line/appeal_cta."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.sanction_issued",
        user_id=str(user.id),
        variables={"sanction_kind": "warning", "reason_label": "spam"},
    )
    (mail,) = capture_email
    assert "restriction lasts until" not in mail["html"]
    assert "Appeal this decision" not in mail["html"]


@pytest.mark.django_db
def test_sanction_issued_with_expiry_renders_expiry_line(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.sanction_issued",
        user_id=str(user.id),
        variables={
            "sanction_kind": "suspension",
            "reason_label": "counterfeit",
            "expires_label": "2026-09-21",
        },
    )
    (mail,) = capture_email
    assert "restriction lasts until 2026-09-21" in mail["html"]


@pytest.mark.django_db
def test_sanction_issued_with_appeal_url_renders_cta_and_is_journalled(push_and_email, user, capture_email):
    """appeal_url both renders the CTA button and survives into
    NotificationLog.data -- it must be declared telemetry (routing.py) or
    telemetry.telemetry_keys() drops it before it reaches the journal."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.sanction_issued",
        user_id=str(user.id),
        variables={
            "sanction_kind": "banned",
            "reason_label": "illegal content",
            "appeal_url": "https://x.example/appeals/case-42",
        },
    )
    (mail,) = capture_email
    assert 'href="https://x.example/appeals/case-42"' in mail["html"]
    assert "Appeal this decision" in mail["html"]
    log = NotificationLog.objects.get(
        user_id=user.id, notification_type="moderation.sanction_issued", channel="push"
    )
    assert log.data.get("appeal_url") == "https://x.example/appeals/case-42"


# ── moderation.appeal_resolved ──────────────────────────────────


@pytest.mark.django_db
def test_appeal_resolved_states_outcome(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.appeal_resolved",
        user_id=str(user.id),
        variables={"outcome_label": "overturned", "appeal_note": ""},
    )
    (mail,) = capture_email
    assert "overturned" in mail["html"]


@pytest.mark.django_db
def test_appeal_resolved_note_is_additive(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.appeal_resolved",
        user_id=str(user.id),
        variables={"outcome_label": "upheld", "appeal_note": "The listing violated the weapons policy."},
    )
    (mail,) = capture_email
    assert "The listing violated the weapons policy." in mail["html"]


@pytest.mark.django_db
def test_appeal_resolved_carries_no_cta(push_and_email, user, capture_email):
    """Unlike its siblings, this letter has no notifications_chat_url and
    its template renders no CTA button -- moderation-design.md §6.3's
    variable table names only outcome_label/appeal_note for this type."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="moderation.appeal_resolved",
        user_id=str(user.id),
        variables={"outcome_label": "upheld", "appeal_note": ""},
    )
    (mail,) = capture_email
    # The base layout's footer always carries its own links (brand,
    # manage-notifications, unsubscribe) -- what this type's CONTENT block
    # must not add is the CTA button table every sibling above it renders.
    assert "padding: 28px 0 0 0" not in mail["html"]


# ── listing_blocked: reason_label / appeal_url (DSA Art. 17) ────


@pytest.mark.django_db
def test_listing_blocked_without_reason_or_appeal_renders_as_before(push_and_email, user, capture_email):
    """No reason_label, no appeal_url: byte-identical to before this
    release -- no reason line, no appeal link."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="listing_blocked",
        user_id=str(user.id),
        variables={
            "listing_title": "Vintage lamp",
            "notifications_chat_url": "https://x.example/listings/1",
        },
    )
    (mail,) = capture_email
    assert "Reason:" not in mail["html"]
    assert "Appeal this decision" not in mail["html"]


@pytest.mark.django_db
def test_listing_blocked_with_reason_label_renders_reason_line(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="listing_blocked",
        user_id=str(user.id),
        variables={
            "listing_title": "Vintage lamp",
            "notifications_chat_url": "https://x.example/listings/1",
            "reason_label": "prohibited item",
        },
    )
    (mail,) = capture_email
    assert "Reason: prohibited item." in mail["html"]


@pytest.mark.django_db
def test_listing_blocked_with_appeal_url_renders_link_and_is_journalled(push_and_email, user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    process_notification(
        notification_type="listing_blocked",
        user_id=str(user.id),
        variables={
            "listing_title": "Vintage lamp",
            "notifications_chat_url": "https://x.example/listings/1",
            "reason_label": "prohibited item",
            "appeal_url": "https://x.example/appeals/listing-1",
        },
    )
    (mail,) = capture_email
    assert 'href="https://x.example/appeals/listing-1"' in mail["html"]
    assert "Appeal this decision" in mail["html"]
    log = NotificationLog.objects.get(
        user_id=user.id, notification_type="listing_blocked", channel="push"
    )
    assert log.data.get("appeal_url") == "https://x.example/appeals/listing-1"


# ── Registry shape: no boot-time defects, no false-positive security flag ─


def test_new_types_are_registered_system_push_and_email():
    from stapel_notifications.routing import get_channels, get_group

    for ntype in (
        "moderation.report_received",
        "moderation.sanction_issued",
        "moderation.appeal_resolved",
    ):
        assert get_group(ntype) == "system"
        assert set(get_channels(ntype)) == {"push", "email"}


def test_new_types_and_listing_blocked_pass_system_checks():
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


def test_listing_blocked_and_sanction_issued_declare_appeal_url_telemetry():
    from stapel_notifications.routing import NOTIFICATION_ROUTING

    assert NOTIFICATION_ROUTING["listing_blocked"]["telemetry"] == ["appeal_url"]
    assert NOTIFICATION_ROUTING["moderation.sanction_issued"]["telemetry"] == ["appeal_url"]
