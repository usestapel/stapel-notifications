"""URL configuration for notifications app."""
from typing import NamedTuple

from django.urls import path

from .views import (
    DeviceTokenView,
    DeviceTokenDeleteView,
    DeviceUnregisterView,
    NotificationKeysView,
    NotificationFeedView,
    NotificationFeedReadView,
)

urlpatterns = [
    # Name unchanged though the view now also lists: a host's reverse() is a
    # seam, and renaming it would break callers for a nicer word.
    path('devices/', DeviceTokenView.as_view(), name='device-token-register'),
    # Two segments, so it can never be shadowed by (or shadow) the one-segment
    # token route below whatever a token happens to look like.
    path('devices/by-id/<int:device_id>/', DeviceUnregisterView.as_view(), name='device-unregister'),
    path('devices/<str:token>/', DeviceTokenDeleteView.as_view(), name='device-token-delete'),
    path('notification-keys/', NotificationKeysView.as_view(), name='notification-keys'),
    path('feed/', NotificationFeedView.as_view(), name='notification-feed'),
    path('feed/read/', NotificationFeedReadView.as_view(), name='notification-feed-read'),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns (capability-config.md §2 p.2).

    ``flags`` compose with OR — the block is mounted while ANY flag is on,
    and disappears only when ALL of them are off. Empty flags = always on.
    """
    name: str
    flags: tuple
    patterns: tuple


#: Gate registry (capability-config.md §2 p.2): notifications has no
#: per-method config gates (the provider axes select backends, they never
#: unmount endpoints) — the whole URL surface is a single always-on block.
#: Declared as a registry entry (rather than left implicit) so the
#: capabilities.json emitter has a uniform mechanism across every module.
GATE_REGISTRY: dict = {
    'notifications.api': GateEntry('notifications.api', (), tuple(urlpatterns)),
}
