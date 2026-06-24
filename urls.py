"""URL configuration for notifications app."""
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
