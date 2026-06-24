"""Views for iron-notifications service."""
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema

from stapel_core.django.errors import IronResponse, IronErrorResponse, IronErrorSerializer
from stapel_core.django.pagination import CreatedAtAnchorPagination
from stapel_core.django.permissions import IsStaffUser, IsServiceRequest

from .models import DevicePushToken, NotificationLog
from .dto import DeviceTokenResponse, FeedItemResponse
from .serializers import (
    DeviceTokenRequestSerializer,
    DeviceTokenResponseSerializer,
    FeedItemResponseSerializer,
)
from .errors import ERR_400_INVALID_PLATFORM, ERR_404_TOKEN_NOT_FOUND
from .translation_keys import NOTIFICATION_KEYS

VALID_PLATFORMS = {'ios', 'android', 'web'}


@extend_schema(tags=['Devices'])
class DeviceTokenView(APIView):
    """Register a push notification token."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id='register_device_token',
        summary='Register push token',
        request=DeviceTokenRequestSerializer,
        responses={
            201: DeviceTokenResponseSerializer,
            400: IronErrorSerializer,
        },
    )
    def post(self, request):
        serializer = DeviceTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        platform = serializer.validated_data['platform']

        if platform not in VALID_PLATFORMS:
            return IronErrorResponse(400, ERR_400_INVALID_PLATFORM)

        DevicePushToken.objects.update_or_create(
            token=token,
            defaults={
                'user_id': request.user.id,
                'platform': platform,
                'is_active': True,
            },
        )

        dto = DeviceTokenResponse(token=token, platform=platform)
        return IronResponse(DeviceTokenResponseSerializer(dto), status=status.HTTP_201_CREATED)


@extend_schema(tags=['Devices'])
class DeviceTokenDeleteView(APIView):
    """Unregister a push notification token."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id='unregister_device_token',
        summary='Unregister push token',
        responses={
            204: None,
            404: IronErrorSerializer,
        },
    )
    def delete(self, request, token):
        deleted, _ = DevicePushToken.objects.filter(
            token=token,
            user_id=request.user.id,
        ).delete()

        if not deleted:
            return IronErrorResponse(404, ERR_404_TOKEN_NOT_FOUND)

        return IronResponse(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Translation Keys'])
class NotificationKeysView(APIView):
    """Expose notification translation keys for the translate service collector."""
    permission_classes = [IsStaffUser | IsServiceRequest]

    @extend_schema(
        operation_id='get_notification_keys',
        summary='Get notification translation keys',
        description='Returns all notification translation keys with English defaults. Used by translate service to sync.',
        responses={200: dict},
    )
    def get(self, request):
        return IronResponse(NOTIFICATION_KEYS)


class FeedPagination(CreatedAtAnchorPagination):
    page_size = 20
    max_page_size = 50


@extend_schema(tags=['Feed'])
class NotificationFeedView(APIView):
    """User's notification feed (push notifications log)."""
    permission_classes = [IsAuthenticated]
    pagination_class = FeedPagination

    @extend_schema(
        operation_id='get_notification_feed',
        summary='Get notification feed',
        description='Returns push notification log entries for the authenticated user, ordered by created_at desc.',
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

        items = [
            FeedItemResponseSerializer(
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
