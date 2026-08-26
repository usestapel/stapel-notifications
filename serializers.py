"""Serializers for notifications API."""
from stapel_core.django.api.serializers import StapelDataclassSerializer
from .dto import (
    DeviceListItemResponse,
    DeviceTokenRequest,
    DeviceTokenResponse,
    FeedItemResponse,
    FeedReadRequest,
    FeedReadResponse,
)


class DeviceTokenRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = DeviceTokenRequest


class DeviceTokenResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = DeviceTokenResponse


class DeviceListItemResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = DeviceListItemResponse


class FeedItemResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = FeedItemResponse


class FeedReadRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = FeedReadRequest


class FeedReadResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = FeedReadResponse
