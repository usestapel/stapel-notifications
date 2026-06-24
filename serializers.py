"""Serializers for notifications API."""
from rest_framework import serializers
from stapel_core.django.serializers import IronDataclassSerializer
from .dto import DeviceTokenRequest, DeviceTokenResponse, FeedItemResponse


class DeviceTokenRequestSerializer(IronDataclassSerializer):
    class Meta:
        dataclass = DeviceTokenRequest


class DeviceTokenResponseSerializer(IronDataclassSerializer):
    class Meta:
        dataclass = DeviceTokenResponse


class FeedItemResponseSerializer(IronDataclassSerializer):
    class Meta:
        dataclass = FeedItemResponse
