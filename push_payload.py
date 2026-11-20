"""The push payload contract: what a device receives, as data a client can type.

Every push this library sends has the same shape, whichever provider carries
it. ``compose`` builds it; the FCM provider translates it field by field into
``firebase_admin.messaging``; the mock provider records it as is. The JSON
Schema below is committed as ``docs/push.json`` (``make contract``) so a
client generates its types from the same declaration the sender is tested
against (``tests/test_push_payload.py``).

``data`` values are strings: FCM's data map carries strings only, and APNs
custom keys arrive the same way through the Firebase SDK. Numbers that a
client needs as numbers (``badge``) are repeated in the platform blocks.
"""
from __future__ import annotations

from .routing import VALID_GROUPS

#: Version of the payload contract, emitted as ``data.v``. A client that
#: reads a higher major than it knows treats the push as a plain notification.
PAYLOAD_VERSION = "1"

#: Template variables that carried a deep link before ``url`` existed, in the
#: order ``url`` falls back through them. Still sent under their own names.
LEGACY_LINK_KEYS = ("chat_url", "listing_url", "notifications_chat_url")

_GROUPS = sorted(VALID_GROUPS)

PUSH_PAYLOAD_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "stapel-notifications/push.json",
    "title": "PushMessage",
    "description": (
        "One push notification as stapel-notifications sends it to one device. "
        "`title`/`body` are the visible notification; `data` is the app's "
        "payload (all values strings); `android` and `apns` are the per-platform "
        "delivery hints the sender sets. A tap opens `data.url` when present "
        "(a deep link the app's link router validates), else the app's feed."
    ),
    "type": "object",
    "required": ["title", "body", "data", "android", "apns"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "description": "Notification title."},
        "body": {"type": "string", "description": "Notification body."},
        "data": {"$ref": "#/$defs/PushData"},
        "android": {
            "type": "object",
            "required": ["channel_id"],
            "additionalProperties": False,
            "properties": {
                "channel_id": {
                    "type": "string",
                    "enum": _GROUPS,
                    "description": "Android notification channel: equal to data.group.",
                },
            },
        },
        "apns": {
            "type": "object",
            "required": ["badge", "sound", "thread_id"],
            "additionalProperties": False,
            "properties": {
                "badge": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Unread rows in the recipient's feed, this one included.",
                },
                "sound": {"type": "string", "enum": ["default"]},
                "thread_id": {
                    "type": "string",
                    "enum": _GROUPS,
                    "description": "Notification grouping on iOS: equal to data.group.",
                },
            },
        },
    },
    "$defs": {
        "PushData": {
            "type": "object",
            "description": "The app's payload. Every value is a string.",
            "required": ["v", "notification_id", "notification_type", "group", "badge"],
            "additionalProperties": {"type": "string"},
            "properties": {
                "v": {"type": "string", "const": PAYLOAD_VERSION, "description": "Payload contract version."},
                "notification_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": (
                        "The feed row this push is (GET feed/ item id): mark it read "
                        "with POST feed/read/ {ids:[notification_id]}."
                    ),
                },
                "notification_type": {"type": "string", "description": "Notification type, e.g. new_message."},
                "group": {
                    "type": "string",
                    "enum": _GROUPS,
                    "description": "Routing group: the Android channel and the iOS thread.",
                },
                "url": {
                    "type": "string",
                    "description": "Canonical deep link to open on tap. Absent when the notification links nowhere.",
                },
                "badge": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                    "description": "Unread feed rows, this one included (decimal string).",
                },
                **{
                    key: {"type": "string", "deprecated": True, "description": "Legacy deep-link key; read `url`."}
                    for key in LEGACY_LINK_KEYS
                },
            },
        },
    },
}


def link_of(all_vars: dict, routing: dict) -> str | None:
    """The notification's canonical deep link.

    ``push_url`` when the caller set one; else the variable the type's routing
    names under ``link``; else the first legacy link key present.
    """
    candidates = ["push_url"]
    if routing.get("link"):
        candidates.append(routing["link"])
    candidates.extend(LEGACY_LINK_KEYS)
    for name in candidates:
        value = all_vars.get(name)
        if value:
            return str(value)
    return None


def compose(*, title: str, body: str, data: dict) -> dict:
    """The message for one device, from the ``data`` map the channel built.

    ``data`` must already carry the contract keys (``_deliver_push`` writes
    them); a custom provider gets the same map and can call this to build the
    same message.
    """
    group = data.get("group") or "system"
    badge = int(data.get("badge") or 0)
    return {
        "title": title or "",
        "body": body or "",
        "data": {k: str(v) for k, v in data.items()},
        "android": {"channel_id": group},
        "apns": {"badge": badge, "sound": "default", "thread_id": group},
    }
