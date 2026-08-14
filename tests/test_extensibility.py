"""Fork-free extension points: open type registry + pluggable channel providers."""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.routing import (
    get_channels,
    get_email_template,
    get_group,
    get_routing,
    registered_types,
)


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


def test_builtin_types_present():
    assert get_channels("otp_code") == ["email", "sms"]
    assert get_group("otp_code") == "auth"
    assert get_email_template("otp_code") == "notifications/email/otp_code.html"


def test_workspace_invitation_registered():
    assert get_channels("workspace.invitation") == ["email"]
    assert get_email_template("workspace.invitation") == "notifications/email/workspace_invitation.html"


# ── Org program types (workspaces-org-program.md §F) ─────────────


def test_workspace_invitation_new_user_registered():
    """Clean routing-override: separate type, not an override of
    "workspace.invitation", same group/channel shape."""
    assert get_channels("workspace.invitation.new_user") == ["email"]
    assert get_group("workspace.invitation.new_user") == "system"
    assert (
        get_email_template("workspace.invitation.new_user")
        == "notifications/email/workspace_invitation_new_user.html"
    )


def test_workspace_provisioned_account_registered():
    """Auth-class notification: mandatory, no unsubscribe (checked via
    _should_send/group in test_services_pipeline-style tests)."""
    assert get_channels("workspace.provisioned_account") == ["email"]
    assert get_group("workspace.provisioned_account") == "auth"
    assert (
        get_email_template("workspace.provisioned_account")
        == "notifications/email/workspace_provisioned_account.html"
    )


def test_workspace_mfa_suspension_and_restored_registered():
    for t in ("workspace.mfa_suspension", "workspace.mfa_restored"):
        assert get_channels(t) == ["email"]
        assert get_group(t) == "auth"
        assert get_email_template(t) == f"notifications/email/{t.replace('.', '_')}.html"


def test_custom_type_via_settings():
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {
                "invoice_ready": {
                    "channels": ["email", "push"],
                    "group": "system",
                    "template": "email/invoice_ready.html",
                }
            }
        }
    ):
        assert get_channels("invoice_ready") == ["email", "push"]
        assert get_group("invoice_ready") == "system"
        assert get_email_template("invoice_ready") == "email/invoice_ready.html"
        assert "invoice_ready" in registered_types()
    assert get_routing("invoice_ready") is None  # override gone after exit


def test_builtin_type_overridable():
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"new_message": {"channels": ["push"], "group": "messages"}}
        }
    ):
        assert get_channels("new_message") == ["push"]
        # template falls back to the built-in when the override has none
        assert get_email_template("new_message") == "notifications/email/new_message.html"


class _FakeProvider:
    sent = []

    def send(self, phone, body):
        type(self).sent.append((phone, body))


def test_sms_provider_dotted_path():
    from stapel_notifications.channels.sms import send_sms

    _FakeProvider.sent = []
    with override_settings(
        STAPEL_NOTIFICATIONS={
            "SMS_PROVIDER": "tests.test_extensibility._FakeProvider"
        }
    ):
        send_sms("+100", "hi")
    assert _FakeProvider.sent == [("+100", "hi")]


def test_unknown_provider_raises_instead_of_becoming_a_mock():
    """A name nobody can resolve is a configuration error, not a mock.

    This used to substitute the channel's mock provider and log a WARNING.
    The mock RETURNS, and ``services._dispatch`` counts a provider that
    returned as a delivery — so one typo in ``SMS_PROVIDER`` sent nothing
    for as long as it took someone to notice, with every passcode recorded
    in the delivery journal as ``status="sent"``.
    """
    from django.core.exceptions import ImproperlyConfigured

    from stapel_notifications.channels.sms import _get_provider

    with override_settings(STAPEL_NOTIFICATIONS={"SMS_PROVIDER": "does-not-exist"}):
        with pytest.raises(ImproperlyConfigured, match="does-not-exist"):
            _get_provider()



# ── TEXT: the copy seam that matches the template seam ───────────


@pytest.mark.django_db
def test_text_registry_overrides_a_subject_without_a_fork():
    """A host could always replace a letter's layout and never its words.

    The subject is the sharp case: it lives in no template, and
    ``process_notification`` refuses caller ``variables`` that collide with a
    translation key, so it could not be passed either. meettoday shipped a
    fully Russian invitation body under an English library subject for exactly
    this reason.
    """
    from stapel_notifications.services import _resolve_translations

    key = "notification.otp_code.subject"
    with override_settings(
        STAPEL_NOTIFICATIONS={"TEXT": {key: "Sign in to Acme — code {code}"}}
    ):
        notifications_settings.reload()
        resolved = _resolve_translations([key], "en")
    assert resolved[key] == "Sign in to Acme — code {code}"


@pytest.mark.django_db
def test_text_override_stays_translatable():
    """A bare-string override becomes the gettext msgid, so a host catalogue
    still translates it — an override can never freeze copy into one
    language, which is the bug the key registry exists to prevent."""
    from stapel_notifications import services

    key = "notification.otp_code.subject"
    seen = []

    def _fake_gettext(default, lang):
        seen.append((default, lang))
        return "Вход в Acme" if default == "Acme code" else None

    original = services._gettext_default
    services._gettext_default = _fake_gettext
    try:
        with override_settings(STAPEL_NOTIFICATIONS={"TEXT": {key: "Acme code"}}):
            notifications_settings.reload()
            resolved = services._resolve_translations([key], "ru")
    finally:
        services._gettext_default = original

    assert ("Acme code", "ru") in seen, "the override did not become the msgid"
    assert resolved[key] == "Вход в Acme"


@pytest.mark.django_db
def test_text_per_language_pin_wins_outright():
    from stapel_notifications.services import _resolve_translations

    key = "notification.otp_code.subject"
    with override_settings(
        STAPEL_NOTIFICATIONS={"TEXT": {key: {"en": "Acme code", "ru": "Код Acme"}}}
    ):
        notifications_settings.reload()
        assert _resolve_translations([key], "ru")[key] == "Код Acme"
        assert _resolve_translations([key], "en")[key] == "Acme code"


def test_text_gives_a_host_registered_type_its_first_copy():
    """A type registered through TYPES has no entry in NOTIFICATION_KEYS at
    all: TEXT is the only place its copy can come from."""
    from stapel_notifications.services import _get_keys_for_type

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"invoice.ready": {"channels": ["email"], "group": "system"}},
            "TEXT": {"notification.invoice.ready.subject": "Your invoice"},
        }
    ):
        notifications_settings.reload()
        assert "notification.invoice.ready.subject" in _get_keys_for_type("invoice.ready")


def test_prefix_collision_no_longer_leaks_a_longer_types_keys():
    """``notification.workspace.invitation.`` is a prefix of
    ``notification.workspace.invitation.new_user.``; a plain prefix match gave
    the plain invitation variables literally named ``new_user.subject`` —
    names with a dot, which Django resolves as attribute lookups and can never
    render."""
    from stapel_notifications.services import _get_keys_for_type

    keys = _get_keys_for_type("workspace.invitation")
    assert "notification.workspace.invitation.subject" in keys
    assert not any(".new_user." in k or ".reminder." in k for k in keys)


def test_invitation_types_are_transactional():
    from stapel_notifications.routing import is_transactional

    assert is_transactional("workspace.invitation")
    assert is_transactional("workspace.invitation.new_user")
    assert is_transactional("workspace.invitation.reminder")
    assert not is_transactional("new_message")
    assert not is_transactional("otp_code")
