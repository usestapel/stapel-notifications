"""Data Transfer Objects for notifications API."""
from dataclasses import dataclass
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
    """
    id: UUID
    notification_type: str
    title: str
    body: str
    data: dict
    created_at: str
