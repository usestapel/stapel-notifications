"""URL configuration for notifications app."""
from typing import NamedTuple

from django.urls import path

from .views import (
    DeviceTokenView,
    DeviceTokenDeleteView,
    NotificationKeysView,
    NotificationFeedView,
)

urlpatterns = [
    path('devices/', DeviceTokenView.as_view(), name='device-token-register'),
    path('devices/<str:token>/', DeviceTokenDeleteView.as_view(), name='device-token-delete'),
    path('notification-keys/', NotificationKeysView.as_view(), name='notification-keys'),
    path('feed/', NotificationFeedView.as_view(), name='notification-feed'),
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
