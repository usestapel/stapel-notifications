"""Branding config (logo + colors via the base layout) and the raw-content
escape hatch (content_html/content_text without a registered template)."""

import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog
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
            {"recipient": recipient, "subject": subject, "html": html_body}
        )


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    return _CapturingEmailProvider.sent


def _process(ntype="otp_code", extra_settings=None, **kwargs):
    conf = {"EMAIL_PROVIDER": CAPTURE, **(extra_settings or {})}
    with override_settings(STAPEL_NOTIFICATIONS=conf):
        process_notification(
            notification_type=ntype,
            user_id=None,
            variables=kwargs.pop("variables", {"code": "1234", "expiry_minutes": 5}),
            email="dest@example.com",
            **kwargs,
        )


# ── Branding: colors ────────────────────────────────────────────


@pytest.mark.django_db
def test_default_brand_colors_render_in_every_email(capture_email):
    _process()
    (mail,) = capture_email
    assert "#00AEEF" in mail["html"]  # BRAND_PRIMARY (logo accent)
    assert "#F5F5F6" in mail["html"]  # BRAND_BG
    assert "#1C1D20" in mail["html"]  # BRAND_TEXT


@pytest.mark.django_db
def test_brand_color_overrides_apply_without_editing_templates(capture_email):
    _process(extra_settings={
        "BRAND_PRIMARY": "#111111",
        "BRAND_PRIMARY_DARK": "#222222",
        "BRAND_BG": "#333333",
        "BRAND_TEXT": "#444444",
    })
    (mail,) = capture_email
    html = mail["html"]
    assert "#111111" in html and "#333333" in html and "#444444" in html
    for default in ("#00AEEF", "#F5F5F6", "#1C1D20"):
        assert default not in html


@pytest.mark.django_db
def test_brand_overrides_apply_across_types(capture_email):
    """The base layout drives all type templates — spot-check a CTA type."""
    _process(
        ntype="gdpr.export_ready",
        variables={"download_url": "https://x/dl"},
        extra_settings={"BRAND_PRIMARY_DARK": "#ABCDEF"},
    )
    (mail,) = capture_email
    assert "#ABCDEF" in mail["html"]  # button + footer links
    assert "#2A90D9" not in mail["html"]


# ── Branding: logo ──────────────────────────────────────────────


@pytest.mark.django_db
def test_no_logo_configured_renders_a_text_wordmark(capture_email):
    """This package ships no logo of its own. It used to bundle one
    product's 233 KB brand mark and attach it to every email whose host
    had not configured branding — see channels/email.py."""
    _process()
    (mail,) = capture_email
    assert "cid:logo" not in mail["html"]
    assert "<img" not in mail["html"]
    assert "Stapel" in mail["html"]  # COMPANY_NAME as text instead


@pytest.mark.django_db
def test_a_data_uri_logo_is_not_something_we_invent(capture_email):
    """Whatever the host sets is what goes out — we do not substitute a
    data: URI as a fallback. Gmail blocks data: as an image source in
    mail, so a "helpful" default would render as a broken-image icon
    (measured, meettoday 2026-07-28)."""
    _process()
    (mail,) = capture_email
    assert "data:image" not in mail["html"]


@pytest.mark.django_db
def test_logo_url_replaces_cid_reference(capture_email):
    _process(extra_settings={"LOGO_URL": "https://cdn.example/logo.png"})
    (mail,) = capture_email
    assert 'src="https://cdn.example/logo.png"' in mail["html"]
    assert "cid:logo" not in mail["html"]
    # No width attribute is applied to a fallback that is not an image.
    assert 'alt="Stapel"' in mail["html"]


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
def test_smtp_never_attaches_anything():
    from django.core import mail

    from stapel_notifications.channels.email import _SMTPEmailProvider

    with override_settings(STAPEL_NOTIFICATIONS={}):
        _SMTPEmailProvider().send("dest@example.com", "s", "<b>x</b>", None)
    (msg,) = mail.outbox
    # Not "no attachment because LOGO_URL was set" — no attachment, ever.
    assert msg.attachments == []


@override_settings(
    STAPEL_NOTIFICATIONS={"RESEND_API_KEY": "re_key", "LOGO_URL": "https://cdn/l.png"},
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
def test_resend_skips_inline_attachment_when_logo_url_set(monkeypatch):
    import sys
    import types

    mod = types.ModuleType("requests")
    calls = []

    class _Resp:
        ok = True
        status_code = 200

        def json(self):
            return {"id": "m1"}

    def post(url, **kwargs):
        calls.append(kwargs)
        return _Resp()

    mod.post = post
    monkeypatch.setitem(sys.modules, "requests", mod)

    from stapel_notifications.channels.email import _ResendEmailProvider

    _ResendEmailProvider().send("d@example.com", "s", "<b>x</b>", None)
    (kwargs,) = calls
    assert "attachments" not in kwargs["json"]


# ── Raw-content escape hatch ────────────────────────────────────


@pytest.mark.django_db
class TestRawContentEscapeHatch:
    def test_unknown_type_with_content_html_sends(self, capture_email):
        _process(
            ntype="adhoc.announcement",  # NOT in the registry
            variables={"subject": "Big news"},
            content_html='<p id="adhoc-body">Hello there</p>',
        )
        (mail,) = capture_email
        assert '<p id="adhoc-body">Hello there</p>' in mail["html"]
        assert mail["subject"] == "Big news"
        # wrapped in the base brand layout, not sent bare
        assert "Stapel" in mail["html"]
        assert "#00AEEF" in mail["html"]
        log = NotificationLog.objects.get(notification_type="adhoc.announcement")
        assert log.status == "sent"

    def test_unknown_type_with_content_text_sends(self, capture_email):
        _process(
            ntype="adhoc.plain",
            variables={},
            content_text="line one\nline two",
        )
        (mail,) = capture_email
        assert "line one<br>line two" in mail["html"]

    def test_content_beats_registered_template(self, capture_email):
        _process(
            ntype="otp_code",  # registered, has a template
            content_html="<p>override body</p>",
        )
        (mail,) = capture_email
        assert "override body" in mail["html"]
        # the registered otp_code template body was not used
        assert "Use the code below" not in mail["html"]

    def test_unknown_type_without_content_still_errors(self, capture_email, caplog):
        with caplog.at_level("ERROR", logger="stapel_notifications.services"):
            _process(ntype="adhoc.nothing", variables={})
        assert capture_email == []
        assert NotificationLog.objects.count() == 0
        assert any("Unknown notification type" in r.message for r in caplog.records)

    def test_adhoc_defaults_to_system_group_with_unsubscribe(self, user, capture_email):
        """Ad-hoc notifications default to the 'system' group: opt-outs and
        unsubscribe machinery apply."""
        from stapel_notifications.models import UserNotificationSettings

        UserNotificationSettings.objects.create(user_id=user.id, email_system=False)
        from stapel_notifications.models import UserContact

        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
            process_notification(
                notification_type="adhoc.announcement",
                user_id=str(user.id),
                variables={},
                content_html="<p>hi</p>",
            )
        assert capture_email == []
        assert NotificationLog.objects.get(channel="email").status == "skipped"


# ── SMTP timeout + footer link ──────────────────────────────────

_LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


class TestSmtpAlwaysHasATimeout:
    """A slow mail server must fail, not hang. Django's SMTP backend blocks
    forever unless EMAIL_TIMEOUT is set, and the sibling providers here
    already pass timeout=15 to their HTTP calls — SMTP was the one path
    that could hang a request until nginx returned 504 (meettoday,
    2026-07-28).

    Asserted on what we hand to get_connection, not on the resulting
    backend object: the locmem backend used in tests silently swallows
    kwargs it does not know, so reading the attribute back would pass
    whether or not we sent anything.
    """

    def _timeout_passed(self, monkeypatch):
        import django.core.mail as dm

        seen = {}
        real = dm.get_connection

        def spy(*args, **kwargs):
            seen.update(kwargs)
            kwargs.pop("timeout", None)
            return real(*args, **kwargs)

        monkeypatch.setattr(dm, "get_connection", spy)
        from stapel_notifications.channels.email import _SMTPEmailProvider

        _SMTPEmailProvider().send("dest@example.com", "s", "<b>x</b>", None)
        return seen.get("timeout")

    def test_library_supplies_a_default_when_the_host_set_none(self, monkeypatch):
        with override_settings(EMAIL_BACKEND=_LOCMEM, DEFAULT_FROM_EMAIL="a@b.c",
                               STAPEL_NOTIFICATIONS={}):
            assert self._timeout_passed(monkeypatch) == 15

    def test_configurable_without_touching_django_settings(self, monkeypatch):
        with override_settings(EMAIL_BACKEND=_LOCMEM, DEFAULT_FROM_EMAIL="a@b.c",
                               STAPEL_NOTIFICATIONS={"SMTP_TIMEOUT": 3}):
            assert self._timeout_passed(monkeypatch) == 3

    def test_a_host_that_set_email_timeout_keeps_it(self, monkeypatch):
        """We fill a gap; we do not overrule a host that already decided."""
        with override_settings(EMAIL_BACKEND=_LOCMEM, DEFAULT_FROM_EMAIL="a@b.c",
                               EMAIL_TIMEOUT=42, STAPEL_NOTIFICATIONS={}):
            assert self._timeout_passed(monkeypatch) == 42


@pytest.mark.django_db
def test_footer_company_link_is_text_when_no_url_is_configured(capture_email):
    """COMPANY_URL has no default. Rendering <a href=""> gave a footer that
    looks like a link, is styled like a link, and does nothing."""
    _process()
    (mail,) = capture_email
    assert 'href=""' not in mail["html"]


@pytest.mark.django_db
def test_footer_company_link_is_a_link_when_a_url_is_configured(capture_email):
    _process(extra_settings={"COMPANY_URL": "https://example.test"})
    (mail,) = capture_email
    assert 'href="https://example.test"' in mail["html"]
