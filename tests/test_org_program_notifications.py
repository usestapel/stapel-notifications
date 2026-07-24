"""Org-program email notifications (workspaces-org-program.md §F).

Covers:
  - ``workspace.invitation`` gaining an additive ``{role_name}`` param —
    rendering without it must stay byte-identical to before this change.
  - ``workspace.invitation.new_user`` — clean routing-override, no
    unsubscribe machinery differences from its parent (still "system" group).
  - ``workspace.provisioned_account`` — auth-class (mandatory, no
    unsubscribe), and the secret-in-email precedent: ``otp_code`` already
    embeds a one-time secret (``{{ code }}``) directly in its template
    (templates/notifications/email/otp_code.html), so embedding the
    org-issued ``initial_password`` directly in this template follows an
    established canon rather than inventing a new exception.
  - ``workspace.mfa_suspension`` / ``workspace.mfa_restored`` — auth-class,
    no unsubscribe.
"""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog, UserContact
from stapel_notifications.services import process_notification
from stapel_notifications.translation_keys import NOTIFICATION_KEYS


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


# ── workspace.invitation: additive {role_name} ──────────────────


@pytest.mark.django_db
def test_invitation_without_role_name_renders_as_before(capture_email):
    """No role_name passed → no role line, no stray "{role_name}" literal."""
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.invitation",
            user_id=None,
            variables={
                "workspace_name": "Acme",
                "inviter_name": "Ada",
                "accept_url": "https://x.example/accept/1",
            },
            email="dest@example.com",
        )
    (mail,) = capture_email
    assert "role_name" not in mail["html"]
    assert "invited to join as" not in mail["html"]


@pytest.mark.django_db
def test_invitation_with_role_name_renders_role_line(capture_email):
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.invitation",
            user_id=None,
            variables={
                "workspace_name": "Acme",
                "inviter_name": "Ada",
                "accept_url": "https://x.example/accept/1",
                "role_name": "admin",
            },
            email="dest@example.com",
        )
    (mail,) = capture_email
    # Django autoescapes the apostrophe as &#x27; in the rendered HTML.
    assert "invited to join as admin." in mail["html"]


# ── workspace.invitation.new_user ────────────────────────────────


@pytest.mark.django_db
def test_invitation_new_user_is_distinct_type_and_template(capture_email):
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.invitation.new_user",
            user_id=None,
            variables={
                "workspace_name": "Acme",
                "inviter_name": "Ada",
                "accept_url": "https://x.example/invite/claim/1",
            },
            email="dest@example.com",
        )
    (mail,) = capture_email
    assert "creates one and joins you to the workspace" in mail["html"]
    log = NotificationLog.objects.get(notification_type="workspace.invitation.new_user")
    assert log.status == "sent"


@pytest.mark.django_db
def test_invitation_new_user_is_system_group_gets_unsubscribe_with_user(user, capture_email):
    """Contrast with the auth-class types below: "system" group still adds
    List-Unsubscribe once a user_id is known — same behavior as its parent
    "workspace.invitation" type."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.invitation.new_user",
            user_id=str(user.id),
            variables={
                "workspace_name": "Acme",
                "inviter_name": "Ada",
                "accept_url": "https://x.example/invite/claim/1",
            },
        )
    (mail,) = capture_email
    assert "List-Unsubscribe" in mail["headers"]


# ── workspace.provisioned_account: secret-in-email precedent ────


@pytest.mark.django_db
def test_provisioned_account_embeds_password_like_otp_code(capture_email):
    """otp_code embeds {{ code }} (a one-time secret) directly in its
    template; provisioned_account follows the same precedent for the
    org-issued initial_password rather than only linking to a "set
    password" flow."""
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.provisioned_account",
            user_id=None,
            variables={
                "workspace_name": "Acme",
                "username": "acme/jdoe",
                "initial_password": "TempPass123!",
                "login_url": "https://x.example/login",
            },
            email="dest@example.com",
        )
    (mail,) = capture_email
    assert "acme/jdoe" in mail["html"]
    assert "TempPass123!" in mail["html"]


@pytest.mark.django_db
def test_provisioned_account_is_auth_group_no_unsubscribe(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.provisioned_account",
            user_id=str(user.id),
            variables={
                "workspace_name": "Acme",
                "username": "acme/jdoe",
                "initial_password": "TempPass123!",
                "login_url": "https://x.example/login",
            },
        )
    (mail,) = capture_email
    assert "List-Unsubscribe" not in mail["headers"]
    # The base layout's footer literally reads "no unsubscribe" in an HTML
    # comment for the minimal (auth-group) footer variant — assert on the
    # absence of an actual unsubscribe link/token, not the bare substring.
    assert "unsubscribe_url" not in mail["html"]
    assert "/unsubscribe/" not in mail["html"]


# ── workspace.mfa_suspension / workspace.mfa_restored ────────────


@pytest.mark.django_db
def test_mfa_suspension_is_auth_group_no_unsubscribe(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.mfa_suspension",
            user_id=str(user.id),
            variables={
                "workspace_name": "Acme",
                "security_url": "https://x.example/security/mfa",
            },
        )
    (mail,) = capture_email
    assert "List-Unsubscribe" not in mail["headers"]
    assert "Acme" in mail["html"]


@pytest.mark.django_db
def test_mfa_restored_is_auth_group_no_unsubscribe(user, capture_email):
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="workspace.mfa_restored",
            user_id=str(user.id),
            variables={
                "workspace_name": "Acme",
                "workspace_url": "https://x.example/workspaces/acme",
            },
        )
    (mail,) = capture_email
    assert "List-Unsubscribe" not in mail["headers"]
    assert "Acme" in mail["html"]


# ── Translation-key registry coverage ────────────────────────────


@pytest.mark.parametrize(
    "ntype,required_suffixes",
    [
        ("workspace.invitation", ("subject", "heading", "body", "cta", "role_line")),
        (
            "workspace.invitation.new_user",
            ("subject", "heading", "body", "cta", "role_line", "warning"),
        ),
        (
            "workspace.provisioned_account",
            (
                "subject",
                "heading",
                "body",
                "username_label",
                "password_label",
                "cta",
                "warning",
            ),
        ),
        (
            "workspace.mfa_suspension",
            ("subject", "heading", "body", "cta", "warning"),
        ),
        ("workspace.mfa_restored", ("subject", "heading", "body", "cta")),
    ],
)
def test_translation_keys_registered_for_type(ntype, required_suffixes):
    prefix = f"notification.{ntype}."
    for suffix in required_suffixes:
        key = f"{prefix}{suffix}"
        assert key in NOTIFICATION_KEYS, f"missing translation key {key}"
        assert NOTIFICATION_KEYS[key]  # non-empty English default
