"""
SMS channel facade.

Dispatches to the provider configured via SMS_PROVIDER setting:
  gatewayapi   — GatewayAPI REST API (https://gatewayapi.com)
  twilio       — Twilio Verify / Messages API
  mock         — Log only, no real sending. Must be asked for explicitly.
  unconfigured — The shipped default: refuses to send (see below).

An unknown short name, or a dotted path that cannot be imported, RAISES.
It used to fall back to ``mock`` with a warning, which meant a typo in
``SMS_PROVIDER`` — or an ImportError inside a working provider module after
a deploy — silently downgraded a live gateway to "write a log line and tell
the delivery journal it was sent".
"""

import logging

from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger(__name__)

#: The short name every channel reserves for "nobody has chosen a backend".
UNCONFIGURED = "unconfigured"

#: Shared wording for the refusal, so the three channels read the same in a
#: traceback. ``{setting}`` is the key inside ``STAPEL_NOTIFICATIONS``.
UNCONFIGURED_MESSAGE = (
    "STAPEL_NOTIFICATIONS['{setting}'] is 'unconfigured': this deployment has "
    "not chosen a delivery backend, so nothing was sent. Set {setting} to a "
    "provider (short name or dotted path to a class with .send(...)), or to "
    "'mock' if you deliberately want log-only delivery in this environment."
)


# ──────────────────────────────────────────────────────────────────
# Provider classes
# ──────────────────────────────────────────────────────────────────

class _MockSMSProvider:
    def send(self, phone: str, body: str) -> None:
        logger.info("[mock sms] to=%s body=%r", _mask(phone), body)


class _UnconfiguredSMSProvider:
    """The shipped default: a channel that has not been pointed at anything.

    Not ``mock``. A mock provider RETURNS, and a provider that returns is
    counted as a delivery by ``services._dispatch`` — so a zero-config
    deployment wrote ``status="sent"`` into the delivery journal for every
    passcode it never sent. Refusing loudly makes the same deployment record
    ``status="failed"`` and escalate through the "NOTIFICATION UNDELIVERABLE"
    path, which is the honest description of what happened.

    A host that genuinely wants the log-only behaviour asks for it by name:
    ``STAPEL_NOTIFICATIONS = {"SMS_PROVIDER": "mock"}``.
    """

    def send(self, phone: str, body: str) -> None:
        raise ImproperlyConfigured(UNCONFIGURED_MESSAGE.format(setting="SMS_PROVIDER"))


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
    'gatewayapi':   _GatewayAPISMSProvider,
    'twilio':       _TwilioSMSProvider,
    'mock':         _MockSMSProvider,
    UNCONFIGURED:   _UnconfiguredSMSProvider,
}


def _resolve_provider_class(name_or_path: str, registry: dict, kind: str, setting: str) -> type:
    """Resolve a provider CLASS by built-in short name or dotted path.

    The dotted-path escape hatch means new providers need no fork — same
    pattern as stapel_core.captcha backends.

    Raises ``ImproperlyConfigured`` when the name resolves to nothing. It
    used to substitute the channel's mock class and log a warning, which
    turned two different accidents into silent, total mail loss that the
    delivery journal still recorded as ``sent``:

      * a typo in the setting (``"resedn"``), and
      * an ``ImportError`` raised from INSIDE a working provider module —
        a missing dependency after a deploy demoted a live mailer to a log
        line, with nothing louder than a WARNING nobody was alerting on.

    Neither has a reading under which "silently send nothing" is the right
    answer, so both stop the send (and, via checks.E003, the boot).

    Split from ``_resolve_provider`` so ``checks.py`` can ask "does this
    name resolve?" at boot without instantiating anything.
    """
    key = (name_or_path or "").strip()
    cls = registry.get(key.lower())
    if cls is not None:
        return cls
    if "." in key:
        from django.utils.module_loading import import_string

        try:
            return import_string(key)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"STAPEL_NOTIFICATIONS['{setting}']={key!r} cannot be imported: "
                f"{exc}. This used to fall back to the {kind} mock provider, "
                "which sends nothing while the delivery journal records "
                "'sent' — so a broken import silenced the channel instead of "
                "failing it."
            ) from exc
    raise ImproperlyConfigured(
        f"STAPEL_NOTIFICATIONS['{setting}']={key!r} is not a known {kind} "
        f"provider. Use one of {sorted(registry)}, or a dotted path to a "
        "class with a .send(...) method."
    )


def _resolve_provider(name_or_path: str, registry: dict, kind: str, setting: str):
    """``_resolve_provider_class``, instantiated."""
    return _resolve_provider_class(name_or_path, registry, kind, setting)()


def _get_provider():
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.SMS_PROVIDER, _PROVIDERS, "SMS", "SMS_PROVIDER"
    )


def send_sms(phone: str, body: str) -> None:
    """Send an SMS via the configured provider."""
    _get_provider().send(phone, body)


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return '***'
    return f"{phone[:2]}***{phone[-4:]}"
