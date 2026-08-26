"""Data Transfer Objects for notifications API."""
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID


@dataclass
class DeviceTokenRequest:
    """Register a push token.

    Attributes:
        token: FCM device token. Example: eHh4eHg6dG9rZW4...
        platform: Device platform. Example: ios
    """
    token: str
    platform: str


@dataclass
class DeviceTokenResponse:
    """Push token registration result.

    Attributes:
        token: Registered token. Example: eHh4eHg6dG9rZW4...
        platform: Device platform. Example: ios
    """
    token: str
    platform: str


@dataclass
class DeviceListItemResponse:
    """One push device registered to the caller.

    The raw token is deliberately absent — see ``token_fingerprint``.

    Attributes:
        id: Device registration id, for DELETE devices/by-id/{id}/. Example: 42
        token_fingerprint: SHA-256 of the device token, hex — hash your own token to find this device. Example: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
        platform: Device platform. Example: ios
        is_active: False once the push provider rejected this token — registered, but no longer delivered to. Example: true
        created_at: ISO 8601 timestamp of the first registration. Example: 2026-03-17T10:30:00Z
        last_seen: ISO 8601 timestamp of the last registration of this token. Example: 2026-03-18T08:02:11Z
    """
    id: int
    token_fingerprint: str
    platform: str
    is_active: bool
    created_at: str
    last_seen: str


@dataclass
class FeedItemResponse:
    """Notification feed item.

    Attributes:
        id: Notification UUID. Example: 550e8400-e29b-41d4-a716-446655440000
        notification_type: Type. Example: listing_blocked
        title: Notification title. Example: Your listing has been blocked
        body: Notification body. Example: Your listing was blocked for guideline violations.
        data: Extra data (deep links etc).
        created_at: ISO 8601 timestamp. Example: 2026-03-17T10:30:00Z
        read_at: ISO 8601 timestamp the recipient marked this read, or null while it is unread. Example: 2026-03-17T11:04:52Z
    """
    id: UUID
    notification_type: str
    title: str
    body: str
    data: dict
    created_at: str
    read_at: Optional[str] = None


@dataclass
class FeedReadRequest:
    """Mark feed rows read. Send exactly one of ``ids`` or ``all``.

    Attributes:
        ids: Feed item ids to mark read — the ids from GET feed/. Ignored when `all` is true.
        all: Mark every unread row of the caller's feed read. Example: false
    """
    ids: list[UUID] = field(default_factory=list)
    all: bool = False


@dataclass
class FeedReadResponse:
    """What the mark-as-read write actually changed.

    Attributes:
        marked: Rows this call moved from unread to read — 0 on a repeat of the same call. Example: 3
        unread_count: The caller's unread rows remaining after the write. Example: 0
    """
    marked: int
    unread_count: int
