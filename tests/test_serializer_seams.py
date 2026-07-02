"""Serializer seam: a host project can swap serializers by subclassing."""

import pytest
from django.test import override_settings
from django.urls import path

from stapel_notifications.serializers import (
    DeviceTokenRequestSerializer,
    DeviceTokenResponseSerializer,
)
from stapel_notifications.views import DeviceTokenView


class BrandedDeviceTokenSerializer(DeviceTokenResponseSerializer):
    """Host-project serializer adding a computed field."""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["registered_on"] = instance.platform.upper()
        return data


class BrandedDeviceTokenView(DeviceTokenView):
    response_serializer_class = BrandedDeviceTokenSerializer


urlpatterns = [
    path("custom/devices/", BrandedDeviceTokenView.as_view(), name="custom-devices"),
]


def test_getters_return_the_overridden_classes():
    view = BrandedDeviceTokenView()
    assert view.get_response_serializer_class() is BrandedDeviceTokenSerializer
    # untouched seam falls back to the parent's default
    assert view.get_request_serializer_class() is DeviceTokenRequestSerializer


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.test_serializer_seams")
def test_subclassed_view_uses_swapped_response_serializer(authed_client, user):
    resp = authed_client.post(
        "/custom/devices/", {"token": "tok-seam", "platform": "ios"}, format="json"
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["registered_on"] == "IOS"  # added by the swapped serializer
    assert body["token"] == "tok-seam"  # base payload still intact

    from stapel_notifications.models import DevicePushToken

    assert DevicePushToken.objects.filter(token="tok-seam", user_id=user.id).exists()
