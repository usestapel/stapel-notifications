"""
Email channel facade.

Dispatches to the provider configured via EMAIL_PROVIDER setting:
  resend   — Resend API (https://resend.com)
  smtp     — Standard SMTP via Django email backend
  mailgun  — Mailgun API (https://mailgun.com)
  mock     — Log only, no real sending (default)

Unknown values fall back to mock with a warning.
"""

import base64
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

_logo_data: bytes | None = None
LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'static', 'notifications', 'logo.png',
)


def _get_logo_data() -> bytes | None:
    global _logo_data
    if _logo_data is None:
        try:
            with open(LOGO_PATH, 'rb') as f:
                _logo_data = f.read()
        except FileNotFoundError:
            logger.warning("Logo file not found: %s", LOGO_PATH)
    return _logo_data


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
        logo = _get_logo_data()
        if logo:
            payload["attachments"] = [{
                "filename": "logo.png",
                "content": base64.b64encode(logo).decode(),
                "content_type": "image/png",
                "content_id": "logo",
            }]
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
        from email.mime.image import MIMEImage
        from django.core.mail import EmailMessage

        msg = EmailMessage(
            subject=subject,
            body=html_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            headers=headers or {},
        )
        msg.content_subtype = 'html'
        logo = _get_logo_data()
        if logo:
            logo_mime = MIMEImage(logo, _subtype='png')
            logo_mime.add_header('Content-ID', '<logo>')
            logo_mime.add_header('Content-Disposition', 'inline', filename='logo.png')
            msg.attach(logo_mime)
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
