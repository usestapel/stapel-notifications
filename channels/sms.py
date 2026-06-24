"""
SMS channel facade.

Dispatches to the provider configured via SMS_PROVIDER setting:
  gatewayapi — GatewayAPI REST API (https://gatewayapi.com)
  twilio     — Twilio Verify / Messages API
  mock       — Log only, no real sending (default)

Unknown values fall back to mock with a warning.
"""

import logging

from django.conf import settings

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

        token = getattr(settings, 'GATEWAYAPI_TOKEN', '')
        sender = getattr(settings, 'GATEWAYAPI_SENDER', 'legacy')
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

        account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', '')
        auth_token  = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
        from_number = getattr(settings, 'TWILIO_PHONE_NUMBER', '')
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


def _get_provider():
    name = getattr(settings, 'SMS_PROVIDER', 'mock').lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        logger.warning("Unknown SMS_PROVIDER=%r — falling back to mock", name)
        cls = _MockSMSProvider
    return cls()


def send_sms(phone: str, body: str) -> None:
    """Send an SMS via the configured provider."""
    _get_provider().send(phone, body)


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return '***'
    return f"{phone[:2]}***{phone[-4:]}"
