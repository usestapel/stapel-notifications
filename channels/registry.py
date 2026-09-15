"""Delivery-channel registry — the channel set, opened.

``routing.py`` has always been a registry: a host adds a notification TYPE
through ``STAPEL_NOTIFICATIONS["TYPES"]``, merged over the built-in catalog,
no fork. The channel that type is delivered ON was the opposite — an
``if/elif`` chain over ``"email" / "push" / "sms" / "telegram"`` in
``services._dispatch``, mirrored by two more chains (``_get_recipient``,
``_template_version``). A host that wanted an in-app feed, a webhook, a
Slack channel or a WhatsApp gateway had to patch upstream, or reimplement
``process_notification`` and lose the preference gate, the delivery claim
and the journal with it.

Same canon as ``TYPES``: **merge over the built-ins**, last-wins per channel
name::

    STAPEL_NOTIFICATIONS = {
        "CHANNELS": {
            # a channel this library does not carry
            "webhook": "myproject.notify.webhook_channel",
            # override a built-in's delivery without touching its routing
            "sms": "myproject.notify.my_sms_channel",
            # or switch one off entirely
            "telegram": None,
        },
        "TYPES": {
            "invoice_ready": {"channels": ["webhook"], "group": "system"},
        },
    }

A value is a :class:`Channel`, a dotted path to one, a bare
``deliver(message) -> bool`` callable, or ``None`` to disable. Resolution
happens at call time, so a test that flips the setting does not re-import
anything and a bad dotted path is a boot check (``notifications.E005``)
rather than an import-order surprise.

**What a host channel gets for free, and what it must still respect.**
Registering a channel does not opt out of anything: the recipient's
preference (``_should_send``), the per-``(event, channel, recipient,
template_version)`` delivery claim, the journal row and the telemetry
allowlist all wrap ``deliver`` exactly as they wrap email. A registered
channel becomes switch-off-able through
``UserNotificationSettings.channel_preferences`` — the JSON half of the
preference model that exists precisely because the concrete
``<channel>_<group>`` boolean columns are a closed set and a host cannot add
one. A channel nobody registered is refused at boot
(``notifications.E004``): routing a type to it produces mail that can never
leave.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class ChannelMessage:
    """Everything one delivery attempt knows, on its way to one channel.

    A dataclass rather than eleven positional arguments: a host channel is
    written against this, so growing the payload upstream must not break
    every registered channel in the fleet.
    """

    channel: str
    notification_type: str
    routing: dict
    all_vars: dict
    lang: str
    user_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    content_html: Optional[str] = None
    content_text: Optional[str] = None
    #: What the channel LEARNED while delivering, for the journal row.
    #: Write-only from the channel's side, read by ``services`` after
    #: ``deliver`` returns. A channel that knows something the boolean return
    #: cannot express — push knows how many devices it actually reached —
    #: records it here instead of the boolean lying by omission. Declared on
    #: the message rather than returned so the ``deliver`` signature, which
    #: every host channel in the fleet is written against, does not change.
    facts: dict = field(default_factory=dict)

    @property
    def body(self) -> str:
        """The letter's text, however this recipient's copy resolved it."""
        return self.all_vars.get("body", self.content_text or "")


#: Signature of the one function a channel must have. Returns True when the
#: message was handed to a provider, False when there was nothing to deliver
#: it TO — no address for this recipient on this channel. That distinction
#: is not cosmetic: "no address" is not a delivery and must not be journalled
#: as one. A provider that is reached and then fails RAISES.
#:
#: **PUSH IS THE ONE EXCEPTION, and it is written here so nobody has to find
#: it by reading push.py.** A recipient with no registered device gets `True`,
#: not `False`, because on this channel the delivery journal IS the product:
#: ``views.NotificationFeedView`` renders the in-app feed from rows where
#: ``status="sent", channel="push"``, so returning False would delete the feed
#: item of every user who has not installed the mobile app — losing a real
#: delivery to make a number honest.
#:
#: The number is made honest the other way, since 0.22.0: push records
#: ``facts["device_count"]``, the journal row carries it, and
#: ``device_count == 0`` counts as a reachability gap for the
#: ``NOTIFICATION UNDELIVERABLE`` escalation even though the row stays "sent".
#: So ``status`` answers "is it in the feed" and ``device_count`` answers "did
#: it leave the building", and a dashboard can finally tell the two apart.
#: Email, SMS and Telegram follow the plain rule above and leave it NULL.
Deliver = Callable[[ChannelMessage], bool]


@dataclass(frozen=True)
class Channel:
    """One delivery channel.

    ``deliver`` is the only required part. ``address`` names the recipient
    for the delivery claim and the journal — it must be stable for one
    recipient, because it is half the idempotency key; the default is the
    user id, which is right for any channel addressed by account rather than
    by contact detail. ``template_version`` says what RENDERING is being
    claimed, so re-pointing a channel at new copy makes a redelivery a new
    delivery rather than a duplicate of the old one.
    """

    name: str
    deliver: Deliver
    address: Callable[[ChannelMessage], str] = staticmethod(
        lambda msg: str(msg.user_id) if msg.user_id else "unknown"
    )
    template_version: Optional[Callable[[ChannelMessage], str]] = None


def _builtin_channels() -> dict[str, Channel]:
    """Built-ins, imported lazily.

    ``channels.push`` imports models at module scope and this module is
    reached from ``AppConfig.ready`` — the same reason ``checks._provider_axes``
    imports where it does.
    """
    from .email import email_channel
    from .push import push_channel
    from .sms import sms_channel
    from .telegram import telegram_channel

    return {
        "email": email_channel,
        "push": push_channel,
        "sms": sms_channel,
        "telegram": telegram_channel,
    }


#: The channel names this library ships. A host adds to this set through
#: ``STAPEL_NOTIFICATIONS["CHANNELS"]``; it never edits it.
BUILTIN_CHANNEL_NAMES = ("email", "push", "sms", "telegram")


def _resolve(name: str, value) -> Optional[Channel]:
    """A registry value → a Channel, or None for "this channel is off"."""
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            value = import_string(value)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"STAPEL_NOTIFICATIONS['CHANNELS'][{name!r}] points at "
                f"{value!r}, which cannot be imported. A channel that does "
                f"not resolve is every notification routed to it silently "
                f"undelivered, so this refuses instead of logging."
            ) from exc
    if isinstance(value, Channel):
        # Keep the registry key authoritative: a host that registers an
        # existing Channel object under a second name must not have the
        # object's own name shadow the one the routing entries use.
        return value if value.name == name else Channel(
            name=name,
            deliver=value.deliver,
            address=value.address,
            template_version=value.template_version,
        )
    if callable(value):
        return Channel(name=name, deliver=value)
    raise ImproperlyConfigured(
        f"STAPEL_NOTIFICATIONS['CHANNELS'][{name!r}] is {value!r}, which is "
        f"neither a Channel, a dotted path to one, a deliver() callable, "
        f"nor None."
    )


def channels() -> dict[str, Optional[Channel]]:
    """Effective registry: settings merged OVER the built-ins.

    Last-wins per channel name. A key whose value resolved to ``None`` is
    KEPT with that value: "registered, then switched off" and "never a
    channel here" are different facts, and only the second one is a
    misrouted notification type.
    """
    from ..conf import notifications_settings

    resolved: dict[str, Optional[Channel]] = dict(_builtin_channels())
    for name, value in (notifications_settings.CHANNELS or {}).items():
        resolved[str(name)] = _resolve(str(name), value)
    return resolved


def get_channel(name: str) -> Optional[Channel]:
    """The channel by name, or None when there is none to deliver with."""
    return channels().get(name)


def registered_channels() -> list[str]:
    """Channel names that can actually deliver — for checks and preferences."""
    return sorted(name for name, channel in channels().items() if channel is not None)


__all__ = [
    "BUILTIN_CHANNEL_NAMES",
    "Channel",
    "ChannelMessage",
    "Deliver",
    "channels",
    "get_channel",
    "registered_channels",
]
