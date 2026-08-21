"""
Telegram channel facade.

Dispatches to the provider configured via the ``TELEGRAM_PROVIDER`` key of
the ``STAPEL_NOTIFICATIONS`` namespace:

  mock         — Log only, no real sending. Must be asked for explicitly.
  unconfigured — The shipped default: refuses to send (see sms.py).

Unlike email/SMS/push this channel ships **no built-in delivery provider**.
A Telegram bot is not a service a deployment subscribes to, it is an
identity the deployment owns: the bot token IS the sender, and whoever
holds it can write to every chat the bot has ever been added to. A library
that shipped a token-reading client would be asking every host to hand that
identity to a class it did not write, for a channel most hosts do not use.

So the provider is app-layer, by dotted path — the same seam every other
channel already offers, and the same shape stapel-mailtrap uses to plug a
mail catcher into ``EMAIL_PROVIDER`` from its own package::

    STAPEL_NOTIFICATIONS = {
        "TELEGRAM_PROVIDER": "myproject.telegram.BotProvider",
    }

    class BotProvider:
        def send(self, chat_id: str, text: str) -> None:
            ...  # your Bot API call, your token

An unknown short name, or a dotted path that cannot be imported, RAISES —
see ``sms._resolve_provider_class`` for why the mock fallback had to go.
"""

import logging

from django.core.exceptions import ImproperlyConfigured

# sms.py owns the shared resolver and the "nobody chose a backend"
# vocabulary for every channel; it imports nothing from here.
from stapel_notifications.channels.sms import UNCONFIGURED, UNCONFIGURED_MESSAGE

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Provider classes
# ──────────────────────────────────────────────────────────────────

class _MockTelegramProvider:
    def send(self, chat_id: str, text: str) -> None:
        logger.info("[mock telegram] to=%s text=%r", _mask(chat_id), text)


class _UnconfiguredTelegramProvider:
    """The shipped default — see ``sms._UnconfiguredSMSProvider``.

    This is the ONLY default this channel can have: with no built-in client
    there is nothing else to point at, and a channel that quietly logged
    instead would journal ``status="sent"`` for messages nobody received.
    """

    def send(self, chat_id: str, text: str) -> None:
        raise ImproperlyConfigured(
            UNCONFIGURED_MESSAGE.format(setting="TELEGRAM_PROVIDER")
        )


# ──────────────────────────────────────────────────────────────────
# Registry + facade
# ──────────────────────────────────────────────────────────────────

_PROVIDERS: dict[str, type] = {
    'mock':        _MockTelegramProvider,
    UNCONFIGURED:  _UnconfiguredTelegramProvider,
}


def _get_provider():
    from stapel_notifications.channels.sms import _resolve_provider
    from stapel_notifications.conf import notifications_settings

    return _resolve_provider(
        notifications_settings.TELEGRAM_PROVIDER,
        _PROVIDERS,
        "Telegram",
        "TELEGRAM_PROVIDER",
    )


def send_telegram(chat_id: str, text: str) -> None:
    """Send a Telegram message via the configured provider."""
    _get_provider().send(chat_id, text)


def _mask(chat_id: str) -> str:
    chat_id = str(chat_id)
    if len(chat_id) <= 4:
        return '***'
    return f"{chat_id[:2]}***{chat_id[-4:]}"
