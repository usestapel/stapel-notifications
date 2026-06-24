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
