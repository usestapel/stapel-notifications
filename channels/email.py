"""
Email channel facade.

Dispatches to the provider configured via EMAIL_PROVIDER setting:
  resend   — Resend API (https://resend.com)
  smtp     — Standard SMTP via Django email backend
  mailgun  — Mailgun API (https://mailgun.com)
  mock     — Log only, no real sending (default)

Unknown values fall back to mock with a warning.
"""

import logging

from django.conf import settings

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
    'resend':   _ResendEmailProvider,
    'smtp':     _SMTPEmailProvider,
    'mailgun':  _MailgunEmailProvider,
    'mock':     _MockEmailProvider,
}


def _get_provider():
    from stapel_notifications.channels.sms import _resolve_provider
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.EMAIL_PROVIDER, _PROVIDERS, _MockEmailProvider, "email"
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
