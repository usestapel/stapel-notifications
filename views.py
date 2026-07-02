"""Views for stapel-notifications service."""

import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from stapel_core.django.api.errors import (
    StapelErrorResponse,
    StapelErrorSerializer,
    StapelResponse,
)
from stapel_core.django.api.pagination import CreatedAtAnchorPagination
from stapel_core.django.api.permissions import IsServiceRequest, IsStaffUser

from .dto import DeviceTokenResponse, FeedItemResponse
from .errors import ERR_400_INVALID_PLATFORM, ERR_404_TOKEN_NOT_FOUND
from .models import DevicePushToken, NotificationLog
from .serializers import (
    DeviceTokenRequestSerializer,
    DeviceTokenResponseSerializer,
    FeedItemResponseSerializer,
)
from .translation_keys import NOTIFICATION_KEYS

logger = logging.getLogger(__name__)

VALID_PLATFORMS = {"ios", "android", "web"}


class SerializerSeamMixin:
    """Overridable serializer seam for every notifications APIView.

    Host projects can swap the request/response serializer of any view by
    subclassing and setting ``request_serializer_class`` /
    ``response_serializer_class`` (or overriding the getters for
    per-request decisions) — no need to rewrite the HTTP method bodies.
    """

    request_serializer_class = None
    response_serializer_class = None

    def get_request_serializer_class(self):
        return self.request_serializer_class

    def get_response_serializer_class(self):
        return self.response_serializer_class


@extend_schema(tags=["Devices"])
class DeviceTokenView(SerializerSeamMixin, APIView):
    """Register a push notification token."""

    permission_classes = [IsAuthenticated]
    request_serializer_class = DeviceTokenRequestSerializer
    response_serializer_class = DeviceTokenResponseSerializer

    @extend_schema(
        operation_id="register_device_token",
        summary="Register push token",
        request=DeviceTokenRequestSerializer,
        responses={
            201: DeviceTokenResponseSerializer,
            400: StapelErrorSerializer,
        },
    )
    def post(self, request):
        serializer = self.get_request_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        # validated_data is a DeviceTokenRequest dataclass instance
        token = serializer.validated_data.token
        platform = serializer.validated_data.platform

        if platform not in VALID_PLATFORMS:
            return StapelErrorResponse(400, ERR_400_INVALID_PLATFORM)

        with transaction.atomic():
            # A device token identifies one physical device.  When another
            # account registers the same token (device handed over, account
            # switch), silently re-binding via update_or_create would move
            # the token without a trace — remove the previous binding
            # explicitly and leave an audit log line instead.
            stale = DevicePushToken.objects.filter(token=token).exclude(
                user_id=request.user.id
            ).first()
            if stale is not None:
                logger.warning(
                    "push token rebinding: token %s... moves from user %s "
                    "to user %s (platform=%s) — previous binding removed",
                    token[:20],
                    stale.user_id,
                    request.user.id,
                    platform,
                )
                stale.delete()

            DevicePushToken.objects.update_or_create(
                token=token,
                user_id=request.user.id,
                defaults={
                    "platform": platform,
                    "is_active": True,
                },
            )

        dto = DeviceTokenResponse(token=token, platform=platform)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(dto), status=status.HTTP_201_CREATED)


@extend_schema(tags=["Devices"])
class DeviceTokenDeleteView(SerializerSeamMixin, APIView):
    """Unregister a push notification token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="unregister_device_token",
        summary="Unregister push token",
        responses={
            204: None,
            404: StapelErrorSerializer,
        },
    )
    def delete(self, request, token):
        deleted, _ = DevicePushToken.objects.filter(
            token=token,
            user_id=request.user.id,
        ).delete()

        if not deleted:
            return StapelErrorResponse(404, ERR_404_TOKEN_NOT_FOUND)

        return StapelResponse(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Translation Keys"])
class NotificationKeysView(SerializerSeamMixin, APIView):
    """Expose notification translation keys for the translate service collector."""

    permission_classes = [IsStaffUser | IsServiceRequest]

    @extend_schema(
        operation_id="get_notification_keys",
        summary="Get notification translation keys",
        description="Returns all notification translation keys with English defaults. Used by translate service to sync.",
        responses={200: dict},
    )
    def get(self, request):
        return StapelResponse(NOTIFICATION_KEYS)


class FeedPagination(CreatedAtAnchorPagination):
    page_size = 20
    max_page_size = 50


@extend_schema(tags=["Feed"])
class NotificationFeedView(SerializerSeamMixin, APIView):
    """User's notification feed (push notifications log)."""

    permission_classes = [IsAuthenticated]
    pagination_class = FeedPagination
    response_serializer_class = FeedItemResponseSerializer

    @extend_schema(
        operation_id="get_notification_feed",
        summary="Get notification feed",
        description="Returns push notification log entries for the authenticated user, ordered by created_at desc.",
        responses={200: FeedItemResponseSerializer(many=True)},
    )
    def get(self, request):
        queryset = NotificationLog.objects.filter(
            user_id=request.user.id,
            status="sent",
            channel="push",
        )

        paginator = FeedPagination()
        page = paginator.paginate_queryset(queryset, request)

        response_cls = self.get_response_serializer_class()
        items = [
            response_cls(
                FeedItemResponse(
                    id=entry.id,
                    notification_type=entry.notification_type,
                    title=entry.title,
                    body=entry.body,
                    data=entry.data,
                    created_at=entry.created_at.isoformat(),
                )
            ).data
            for entry in page
        ]

        return paginator.get_paginated_response(items)
