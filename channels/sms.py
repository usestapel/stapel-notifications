"""
SMS channel facade.

Dispatches to the provider configured via SMS_PROVIDER setting:
  gatewayapi — GatewayAPI REST API (https://gatewayapi.com)
  twilio     — Twilio Verify / Messages API
  mock       — Log only, no real sending (default)

Unknown values fall back to mock with a warning.
"""

import logging


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Provider classes
# ──────────────────────────────────────────────────────────────────

class _MockSMSProvider:
    def send(self, phone: str, body: str) -> None:
        logger.info("[mock sms] to=%s body=%r", _mask(phone), body)


class _GatewayAPISMSProvider:
    def send(self, phone: str, body: str) -> None:
        import requests as _http

        from stapel_notifications.conf import notifications_settings

        token = notifications_settings.GATEWAYAPI_TOKEN
        sender = notifications_settings.GATEWAYAPI_SENDER
        if not token:
            raise RuntimeError("SMS_PROVIDER=gatewayapi requires GATEWAYAPI_TOKEN")

        msisdn = int(phone.lstrip('+'))
        resp = _http.post(
            "https://gatewayapi.com/rest/mtsms",
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            },
            json={
                "sender": sender,
                "message": body,
                "recipients": [{"msisdn": msisdn}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("SMS sent to %s via GatewayAPI (ids=%s)", _mask(phone), resp.json().get("ids"))


class _TwilioSMSProvider:
    def send(self, phone: str, body: str) -> None:
        from twilio.rest import Client

        from stapel_notifications.conf import notifications_settings

        account_sid = notifications_settings.TWILIO_ACCOUNT_SID
        auth_token = notifications_settings.TWILIO_AUTH_TOKEN
        from_number = notifications_settings.TWILIO_PHONE_NUMBER
        if not account_sid or not auth_token:
            raise RuntimeError("SMS_PROVIDER=twilio requires TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")

        Client(account_sid, auth_token).messages.create(
            body=body, from_=from_number, to=phone,
        )
        logger.info("SMS sent to %s via Twilio", _mask(phone))


# ──────────────────────────────────────────────────────────────────
# Registry + facade
# ──────────────────────────────────────────────────────────────────

_PROVIDERS: dict[str, type] = {
    'gatewayapi': _GatewayAPISMSProvider,
    'twilio':     _TwilioSMSProvider,
    'mock':       _MockSMSProvider,
}




def _resolve_provider(name_or_path: str, registry: dict, fallback: type, kind: str):
    """Resolve a provider by built-in short name or dotted path.

    The dotted-path escape hatch means new providers need no fork — same
    pattern as stapel_core.captcha backends.
    """
    key = (name_or_path or "").strip()
    cls = registry.get(key.lower())
    if cls is None and "." in key:
        try:
            from django.utils.module_loading import import_string

            cls = import_string(key)
        except ImportError:
            logger.warning("Cannot import %s provider %r", kind, key)
            cls = None
    if cls is None:
        logger.warning("Unknown %s provider %r — falling back to mock", kind, key)
        cls = fallback
    return cls()


def _get_provider():
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.SMS_PROVIDER, _PROVIDERS, _MockSMSProvider, "SMS"
    )


def send_sms(phone: str, body: str) -> None:
    """Send an SMS via the configured provider."""
    _get_provider().send(phone, body)


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return '***'
    return f"{phone[:2]}***{phone[-4:]}"
