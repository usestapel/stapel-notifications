"""Who may carry List-Unsubscribe, and every way of getting it wrong.

Live defect (Oleg, 2026-08-09, real Gmail): an "Unsubscribe" banner on ALL
mail — passcodes and security notices included. `List-Unsubscribe-Post:
One-Click` is machine-actionable: a mail client, an anti-abuse scanner or a
security appliance may POST that URL with no human involved, and this
library's token is minted per (user, GROUP, channel). One automated click on
a security letter therefore stops the mail that tells the recipient their
account is under attack.

Version 0.8.0 answered a narrower version of this per-letter (the personal
workspace invitation). This file gates the CLASS: the predicate is now an
allowlist (`routing.unsubscribe_allowed`), so a type must NAME an
unsubscribable group to get the affordance instead of merely failing to say
"auth". Every assertion below is on the headers of an actually rendered
message, not on the predicate.
"""
import re

import pytest
from django.test import override_settings

from stapel_notifications.checks import (
    check_no_security_type_is_unsubscribable,
    check_notification_groups_are_known,
    check_security_shaped_types_are_classified,
)
from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import UserContact
from stapel_notifications.routing import (
    NOTIFICATION_ROUTING,
    SECURITY_GROUPS,
    UNSUBSCRIBABLE_GROUPS,
    is_security,
    may_carry_unsubscribe,
    unsubscribe_allowed,
)
from stapel_notifications.services import process_notification

UNSUB_HEADERS = ("List-Unsubscribe", "List-Unsubscribe-Post")


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _Capture:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append(
            {"recipient": recipient, "subject": subject,
             "html": html_body, "headers": headers or {}}
        )


CAPTURE = f"{_Capture.__module__}._Capture"


def _send(notification_type, user_id, variables=None, types=None, **extra):
    """Send one notification through a capturing provider, return the message."""
    _Capture.sent = []
    settings = {"EMAIL_PROVIDER": CAPTURE, "FRONTEND_URL": "https://app.example"}
    if types is not None:
        settings["TYPES"] = types
    with override_settings(STAPEL_NOTIFICATIONS=settings):
        notifications_settings.reload()
        process_notification(
            notification_type=notification_type,
            user_id=user_id,
            variables=variables or {},
            **extra,
        )
    (mail,) = _Capture.sent
    return mail


# ── The packaged catalog: every security letter, rendered ────────────

_SECURITY_TYPES = sorted(
    t for t, r in NOTIFICATION_ROUTING.items()
    if r.get("group") in SECURITY_GROUPS and "email" in r["channels"]
)


@pytest.mark.parametrize("notification_type", _SECURITY_TYPES)
@pytest.mark.django_db
def test_no_packaged_security_letter_carries_an_unsubscribe(notification_type, user):
    """13 auth-group letters, one rendered message each, headers asserted.

    Parametrized rather than looped so a regression names the letter.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(notification_type, str(user.id), {"code": "1234"})
    assert not [h for h in UNSUB_HEADERS if h in mail["headers"]], (
        f"{notification_type} offered an unsubscribe: {mail['headers']}"
    )
    # and the rendered body must not offer one either — the base layout
    # switches to the unsubscribe footer on the mere presence of the
    # variable, so the header and the footer are one defect, not two.
    body = re.sub(r"<!--.*?-->", "", mail["html"], flags=re.S).lower()
    assert "unsubscribe" not in body


@pytest.mark.django_db
def test_bulk_mail_still_carries_both_headers(user):
    """The policy is an allowlist, not a ban: real list mail complies."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send("new_message", str(user.id), {"sender_name": "Ada"})
    assert mail["headers"]["List-Unsubscribe"].startswith(
        "<https://app.example/profiles/notifications/unsubscribe/?token="
    )
    assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# ── The holes the allowlist closes ───────────────────────────────────


@pytest.mark.django_db
def test_a_host_type_with_no_group_gets_no_unsubscribe(user):
    """`group != "auth"` treated a MISSING group as unsubscribable."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(
        "myapp.security_alert", str(user.id),
        types={"myapp.security_alert": {
            "channels": ["email"],
            "template": "notifications/email/new_device_login.html",
        }},
    )
    assert not [h for h in UNSUB_HEADERS if h in mail["headers"]]


@pytest.mark.django_db
def test_a_misspelled_group_gets_no_unsubscribe(user):
    """"Auth", "sistem", "System" — every near-miss used to pass."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(
        "myapp.alert", str(user.id),
        types={"myapp.alert": {
            "channels": ["email"], "group": "Auth",
            "template": "notifications/email/new_device_login.html",
        }},
    )
    assert not [h for h in UNSUB_HEADERS if h in mail["headers"]]


@pytest.mark.django_db
def test_an_override_that_drops_the_builtin_auth_group_gets_no_unsubscribe(user):
    """The live shape: a host wanted email-only OTP and lost the group.

    ``get_routing`` REPLACES a built-in entry, it does not merge — so this
    two-key override is how a passcode became bulk mail.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(
        "otp_code", str(user.id), {"code": "1234", "expiry_minutes": 5},
        types={"otp_code": {"channels": ["email"]}},
    )
    assert not [h for h in UNSUB_HEADERS if h in mail["headers"]]
    assert "1234" in mail["html"]  # it is still a passcode email


@pytest.mark.django_db
def test_security_flag_removes_the_affordance_without_moving_the_group(user):
    """The declaration for a security letter the recipient may switch off."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(
        "myapp.new_device", str(user.id),
        types={"myapp.new_device": {
            "channels": ["email"], "group": "system", "security": True,
            "template": "notifications/email/new_device_login.html",
        }},
    )
    assert not [h for h in UNSUB_HEADERS if h in mail["headers"]]
    # the group is untouched: the recipient's email_system preference still
    # governs whether this is sent at all
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        "myapp.new_device": {"channels": ["email"], "group": "system", "security": True},
    }}):
        notifications_settings.reload()
        from stapel_notifications.routing import get_group

        assert get_group("myapp.new_device") == "system"
        assert is_security("myapp.new_device") is True


@pytest.mark.django_db
def test_a_caller_supplied_unsubscribe_url_cannot_create_the_header(user):
    """The header comes from the routing entry, never from a variable."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(
        "otp_code", str(user.id),
        {"code": "1234", "expiry_minutes": 5,
         "unsubscribe_url": "https://evil.example/opt-out"},
    )
    assert not [h for h in UNSUB_HEADERS if h in mail["headers"]]


@pytest.mark.django_db
def test_raw_content_ad_hoc_mail_is_judged_by_the_same_rule(user):
    """The escape hatch synthesises a routing entry; it is not exempt.

    An unregistered type with raw content is treated as ``system`` — genuine
    ad-hoc list mail, so it DOES get the affordance. The point of asserting
    it here is that the decision goes through ``unsubscribe_allowed`` on the
    synthesised entry rather than through a registry lookup that answers
    None.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    mail = _send(
        "myapp.announcement", str(user.id),
        content_html="<p>We moved office.</p>",
    )
    assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# ── The predicate itself: default-deny ───────────────────────────────


def test_unsubscribe_allowed_denies_everything_it_was_not_told_about():
    assert unsubscribe_allowed(None) is False
    assert unsubscribe_allowed({}) is False
    assert unsubscribe_allowed({"channels": ["email"]}) is False
    assert unsubscribe_allowed({"group": ""}) is False
    assert unsubscribe_allowed({"group": "Auth"}) is False
    assert unsubscribe_allowed({"group": "marketing"}) is False
    assert unsubscribe_allowed({"group": "auth"}) is False
    assert unsubscribe_allowed({"group": "system", "security": True}) is False
    assert unsubscribe_allowed({"group": "system", "transactional": True}) is False
    for group in sorted(UNSUBSCRIBABLE_GROUPS):
        assert unsubscribe_allowed({"group": group}) is True


def test_may_carry_unsubscribe_reads_the_registry():
    assert may_carry_unsubscribe("otp_code") is False
    assert may_carry_unsubscribe("workspace.invitation") is False  # transactional
    assert may_carry_unsubscribe("new_message") is True
    assert may_carry_unsubscribe("no.such.type") is False


# ── The boot gate ────────────────────────────────────────────────────


def test_the_packaged_catalog_passes_its_own_checks():
    """A gate that fires on the library's own types is a gate people mute."""
    assert check_notification_groups_are_known(None) == []
    assert check_no_security_type_is_unsubscribable(None) == []
    assert check_security_shaped_types_are_classified(None) == []


def test_e001_an_unknown_group_stops_the_boot():
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        "myapp.thing": {"channels": ["email"], "group": "sistem"},
    }}):
        notifications_settings.reload()
        (error,) = check_notification_groups_are_known(None)
    assert error.id == "stapel_notifications.E001"
    assert "myapp.thing" in error.msg


def test_e001_covers_a_missing_group_key():
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        "myapp.thing": {"channels": ["email"]},
    }}):
        notifications_settings.reload()
        (error,) = check_notification_groups_are_known(None)
    assert error.id == "stapel_notifications.E001"


def test_e002_an_override_that_demotes_a_builtin_security_type():
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        "otp_code": {"channels": ["email"], "group": "system"},
    }}):
        notifications_settings.reload()
        (error,) = check_no_security_type_is_unsubscribable(None)
    assert error.id == "stapel_notifications.E002"
    assert "otp_code" in error.msg
    assert "REPLACES" in error.msg


def test_e002_is_satisfied_by_restating_the_classification():
    for entry in (
        {"channels": ["email"], "group": "auth"},
        {"channels": ["email"], "group": "system", "security": True},
    ):
        with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {"otp_code": entry}}):
            notifications_settings.reload()
            assert check_no_security_type_is_unsubscribable(None) == []


def test_w003_warns_on_a_security_shaped_name_in_a_bulk_group():
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        "account.password_changed": {"channels": ["email"], "group": "system"},
    }}):
        notifications_settings.reload()
        (warning,) = check_security_shaped_types_are_classified(None)
    assert warning.id == "stapel_notifications.W003"
    assert "account.password_changed" in warning.msg


@pytest.mark.parametrize("notification_type", [
    "account.password_changed", "new_login", "session.revoked",
    "mfa_enrolled", "suspicious_activity", "device.added",
    "email_verification", "recovery.codes_viewed",
])
def test_w003_vocabulary(notification_type):
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        notification_type: {"channels": ["email"], "group": "system"},
    }}):
        notifications_settings.reload()
        assert [w.id for w in check_security_shaped_types_are_classified(None)] == [
            "stapel_notifications.W003"
        ]


def test_w003_is_silenced_by_the_declaration_it_asks_for():
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        "account.password_changed": {
            "channels": ["email"], "group": "system", "security": True,
        },
    }}):
        notifications_settings.reload()
        assert check_security_shaped_types_are_classified(None) == []


@pytest.mark.parametrize("notification_type", [
    "new_message", "listing_expiring", "report_reviewed",
    "invoice_ready", "weekly_digest", "meeting.invitation",
])
def test_w003_does_not_fire_on_ordinary_product_mail(notification_type):
    with override_settings(STAPEL_NOTIFICATIONS={"TYPES": {
        notification_type: {"channels": ["email"], "group": "system"},
    }}):
        notifications_settings.reload()
        assert check_security_shaped_types_are_classified(None) == []
