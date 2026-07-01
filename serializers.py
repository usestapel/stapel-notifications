"""Serializers for notifications API."""
from stapel_core.django.api.serializers import StapelDataclassSerializer
from .dto import DeviceTokenRequest, DeviceTokenResponse, FeedItemResponse


class DeviceTokenRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = DeviceTokenRequest


class DeviceTokenResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = DeviceTokenResponse


class FeedItemResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = FeedItemResponse
