"""
Email channel facade.

Dispatches to the provider configured via EMAIL_PROVIDER setting:
  resend       — Resend API (https://resend.com)
  smtp         — Standard SMTP via Django email backend
  mailgun      — Mailgun API (https://mailgun.com)
  mock         — Log only, no real sending. Must be asked for explicitly.
  unconfigured — The shipped default: refuses to send (see sms.py).

An unknown short name, or a dotted path that cannot be imported, RAISES —
see ``sms._resolve_provider_class`` for why the mock fallback had to go.
"""

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# sms.py owns the shared resolver and the "nobody chose a backend" vocabulary
# for all three channels; it imports nothing from here, so this is not a cycle.
from stapel_notifications.channels.sms import UNCONFIGURED, UNCONFIGURED_MESSAGE

logger = logging.getLogger(__name__)

# There is deliberately NO bundled default logo here any more.
#
# This package used to ship a 233 KB 512x512 PNG and attach it inline to
# every email whose host had not set LOGO_URL. Three things were wrong
# with that, and they only became visible on a real mail server:
#
#   1. It was one product's brand mark, shipped inside a general-purpose
#      OSS library. Every host that never configured branding sent mail
#      carrying somebody else's logo.
#   2. A quarter-megabyte base64 attachment on EVERY message — the single
#      biggest thing in a one-line OTP email, and slow enough over SMTP to
#      look like a hang (meettoday, 2026-07-28).
#   3. It made "no logo configured" a state that still rendered an <img>,
#      so a client that could not fetch it showed a broken-image icon.
#
# Unset LOGO_URL now means: no image at all, and the template renders the
# company name as a text wordmark. Hosts that want a picture set LOGO_URL
# to one they own and serve over https.


# ──────────────────────────────────────────────────────────────────
# Provider classes
# ──────────────────────────────────────────────────────────────────

class _MockEmailProvider:
    def send(self, recipient: str, subject: str, html_body: str, headers: dict | None) -> None:
        logger.info("[mock email] to=%s subject=%r", _mask(recipient), subject)


class _UnconfiguredEmailProvider:
    """The shipped default — see ``sms._UnconfiguredSMSProvider``.

    The email channel is the one this matters most on: every built-in
    security type routes to it, so a zero-config deployment used to journal
    OTP codes, password resets and account-closure notices as ``sent`` when
    all that happened was a log line.
    """

    def send(self, recipient: str, subject: str, html_body: str, headers: dict | None) -> None:
        raise ImproperlyConfigured(UNCONFIGURED_MESSAGE.format(setting="EMAIL_PROVIDER"))


class _ResendEmailProvider:
    def send(self, recipient: str, subject: str, html_body: str, headers: dict | None) -> None:
        import requests as _http

        from stapel_notifications.conf import notifications_settings

        api_key = notifications_settings.RESEND_API_KEY
        if not api_key:
            raise RuntimeError("EMAIL_PROVIDER=resend requires RESEND_API_KEY")

        payload: dict = {
            "from": settings.DEFAULT_FROM_EMAIL,
            "to": [recipient],
            "subject": subject,
            "html": html_body,
        }
        if headers:
            payload["headers"] = headers

        resp = _http.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text}")
        logger.info("Email sent to %s via Resend (id=%s)", _mask(recipient), resp.json().get("id"))


class _SMTPEmailProvider:
    def send(self, recipient: str, subject: str, html_body: str, headers: dict | None) -> None:
        from django.core.mail import EmailMessage, get_connection

        from stapel_notifications.conf import notifications_settings

        # Open the connection ourselves so a timeout is ALWAYS in force.
        # Django's default SMTP backend blocks forever unless the host set
        # EMAIL_TIMEOUT, and the sibling providers here already pass
        # timeout=15 to their HTTP calls — SMTP was the one path where a
        # slow server hung the request until nginx killed it with a 504
        # (meettoday, 2026-07-28). A host that set EMAIL_TIMEOUT keeps it:
        # we only supply a default where there was none.
        timeout = getattr(settings, "EMAIL_TIMEOUT", None)
        if timeout is None:
            timeout = notifications_settings.SMTP_TIMEOUT

        msg = EmailMessage(
            subject=subject,
            body=html_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            headers=headers or {},
            connection=get_connection(timeout=timeout),
        )
        msg.content_subtype = 'html'
        msg.send(fail_silently=False)
        logger.info("Email sent to %s via SMTP", _mask(recipient))


class _MailgunEmailProvider:
    def send(self, recipient: str, subject: str, html_body: str, headers: dict | None) -> None:
        import requests as _http

        from stapel_notifications.conf import notifications_settings

        api_key = notifications_settings.MAILGUN_API_KEY
        domain = notifications_settings.MAILGUN_DOMAIN
        if not api_key or not domain:
            raise RuntimeError("EMAIL_PROVIDER=mailgun requires MAILGUN_API_KEY and MAILGUN_DOMAIN")

        resp = _http.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from": settings.DEFAULT_FROM_EMAIL,
                "to": recipient,
                "subject": subject,
                "html": html_body,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Email sent to %s via Mailgun", _mask(recipient))


# ──────────────────────────────────────────────────────────────────
# Registry + facade
# ──────────────────────────────────────────────────────────────────

_PROVIDERS: dict[str, type] = {
    'resend':      _ResendEmailProvider,
    'smtp':        _SMTPEmailProvider,
    'mailgun':     _MailgunEmailProvider,
    'mock':        _MockEmailProvider,
    UNCONFIGURED:  _UnconfiguredEmailProvider,
}


def _get_provider():
    from stapel_notifications.channels.sms import _resolve_provider
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.EMAIL_PROVIDER, _PROVIDERS, "email", "EMAIL_PROVIDER"
    )


def send_email(
    recipient: str,
    subject: str,
    html_body: str,
    headers: dict | None = None,
) -> None:
    """Send an HTML email via the configured provider."""
    _get_provider().send(recipient, subject, html_body, headers)


def _mask(email: str) -> str:
    if '@' not in email:
        return '***'
    local, domain = email.split('@', 1)
    return f"{local[0]}***@{domain}" if local else f"***@{domain}"


from .registry import Channel  # noqa: E402  (kept beside its use)


# ─── The channel object (registry seam) ─────────────────────
#
# The delivery half of this channel — what used to be the ``email`` branch of
# ``services._dispatch``, plus the two chains beside it that answered "who is
# the recipient" and "which rendering is being claimed". They live here, with
# the provider they use, so the registry can carry them as one thing.


def _deliver_email(msg) -> bool:
    """Render this recipient's letter and hand it to the email provider."""
    from django.template.loader import render_to_string
    from django.utils import translation

    from ..routing import get_email_template, unsubscribe_allowed

    if not msg.email:
        return False

    # The RENDER runs inside the recipient's language, not the process's.
    #
    # Every string this library owns is already resolved per-recipient into
    # all_vars before we get here, so the packaged templates — which contain
    # no prose of their own, enforced by
    # tests/test_no_hardcoded_copy_in_templates.py — were correct without
    # this. A HOST template is where it mattered: `{% trans %}`,
    # `{% blocktrans %}`, `|date` and every other locale-sensitive tag asks
    # Django's ACTIVE language, which in a consumer process is whatever the
    # last request left behind and in a web process is the SENDER's. Wrapping
    # the render is what makes a host's own gettext catalogue reach the
    # person being written to.
    #
    # What this cannot do: prose typed literally into a template stays in the
    # language it was typed in. get_email_template() takes no language —
    # there is one template per type — so a host whose letter is hardcoded
    # Russian markup sends Russian to everyone no matter what is active here.
    with translation.override(msg.lang):
        if msg.content_html or msg.content_text:
            # Raw-content escape hatch: wrap the caller-provided body in the
            # base brand layout instead of a per-type template.
            html = render_to_string(
                "notifications/email/_raw_content.html",
                {
                    **msg.all_vars,
                    "content_html": msg.content_html,
                    "content_text": msg.content_text,
                },
            )
        else:
            template = get_email_template(msg.notification_type)
            if not template:
                raise ValueError(
                    f"No email template for notification type: {msg.notification_type}"
                )
            html = render_to_string(template, msg.all_vars)

    subject = msg.all_vars.get(
        "subject", f"{msg.all_vars.get('company_name', '')} Notification".strip()
    )
    headers = {}
    # Asked again, from the same routing entry that granted the
    # unsubscribe_url — not from the presence of that variable. A caller may
    # pass unsubscribe_url as a plain template variable, and a passcode must
    # not grow a machine-actionable one-click opt-out from all security mail
    # because somebody put a URL in a dict.
    if unsubscribe_allowed(msg.routing) and "unsubscribe_url" in msg.all_vars:
        headers["List-Unsubscribe"] = f"<{msg.all_vars['unsubscribe_url']}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    send_email(msg.email, subject, html, headers)
    return True


def _email_template_version(msg) -> str:
    """Email is the one channel that renders a TEMPLATE, so it versions by it."""
    from ..routing import get_email_template

    if msg.content_html or msg.content_text:
        return "raw"
    return get_email_template(msg.notification_type) or msg.notification_type


def _email_address(msg) -> str:
    return msg.email or "unknown"


#: The registry entry for this channel.
email_channel = Channel(
    name="email",
    deliver=_deliver_email,
    address=_email_address,
    template_version=_email_template_version,
)
