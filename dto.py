"""Data Transfer Objects for notifications API."""
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID


@dataclass
class DeviceTokenRequest:
    """Register a push token.

    Send it on every sign-in and every token refresh: a repeat refreshes the
    same row, and the row is bound to the session that sent it.

    Attributes:
        token: FCM device token. Example: eHh4eHg6dG9rZW4...
        platform: Device platform. Example: ios
        device_id: The installation's stable id, generated once per install (opaque, 16-255 characters of letters, digits and - . _ ~ : + / =). A new token from the same installation refreshes its row instead of adding one, and a token still held by another account is handed over (instead of 409) when that row carries the same device_id. Example: 3f2b9c4e-8a71-4d6e-9b0a-5c1e7f2d4a90
        session_id: Only for access tokens without a session claim (issued by stapel-auth before 0.54.0) — the id auth's session list marks is_current. Ignored, not refused, when the token carries the session (sid) or the service can read it itself. When that session is revoked or expires the row is deleted. Example: 550e8400-e29b-41d4-a716-446655440000
    """
    token: str
    platform: str
    device_id: Optional[str] = None
    session_id: Optional[str] = None


@dataclass
class DeviceTokenResponse:
    """Push token registration result.

    Attributes:
        token: Registered token. Example: eHh4eHg6dG9rZW4...
        platform: Device platform. Example: ios
        device_id: The installation id the row carries, or null. Example: 3f2b9c4e-8a71-4d6e-9b0a-5c1e7f2d4a90
        session_bound: True when the row is bound to a session and is deleted when that session is revoked or expires. Example: true
    """
    token: str
    platform: str
    device_id: Optional[str] = None
    session_bound: bool = field(default=False, metadata={"example": True})


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
        device_id: The installation id this row was registered with, or null. Example: 3f2b9c4e-8a71-4d6e-9b0a-5c1e7f2d4a90
        session_bound: True when the row is deleted together with the session that registered it. Example: true
    """
    id: int
    token_fingerprint: str
    platform: str
    is_active: bool
    created_at: str
    last_seen: str
    device_id: Optional[str] = None
    session_bound: bool = field(default=False, metadata={"example": True})


@dataclass
class DeviceUnregisterRequest:
    """Unregister this installation's push device. Send exactly one of ``token`` or ``device_id``.

    The token travels in the body, never in the URL: it is a bearer value
    and URLs end up in access logs and traces.

    Attributes:
        token: The device token to remove. Example: eHh4eHg6dG9rZW4...
        device_id: The installation id to remove every row of (for the caller's account). Example: 3f2b9c4e-8a71-4d6e-9b0a-5c1e7f2d4a90
    """
    token: Optional[str] = None
    device_id: Optional[str] = None


@dataclass
class FeedItemResponse:
    """Notification feed item.

    Attributes:
        id: Notification UUID. Example: 550e8400-e29b-41d4-a716-446655440000
        notification_type: Type. Example: listing_blocked
        title: Notification title. Example: Your listing has been blocked
        body: Notification body. Example: Your listing was blocked for guideline violations.
        data: Extra data (deep links etc). Example: {"listing_id": "8f14e45f-ceea-467f-a0e6-0f2b4c1d9a3e"}
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
