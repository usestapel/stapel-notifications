"""Views for stapel-notifications service.

Guest (anonymous session) stance
--------------------------------
With ``AUTH_ANONYMOUS`` on, a guest session is ``is_authenticated``, so a bare
``IsAuthenticated`` says nothing about whether guests belong on a view
(``stapel_core.adoption`` E001/W002). Every view here answers, along this
line:

    **a guest may read its own notification feed — which is empty — and may
    not touch the device-token registry.**

The registry is the sharp half, and not merely because pushing to a throwaway
account is pointless. A device token identifies one *physical device*, and
``DeviceTokenView`` deliberately **removes another account's binding** when
the same token arrives under a new user (the hand-over case, logged as a
rebinding). With guest sessions on, that turns into a live defect on any
shared device: a user logs out, the app mints an anonymous session, the app
re-registers the same device token — and the previous, real account silently
stops receiving push. Requiring a real account on both the register and the
unregister side closes that path, and costs nothing: an anonymous session has
no durable identity worth notifying.

The feed is the opposite case. It reads ``NotificationLog`` filtered by
``request.user.id``, so a guest's answer is an empty page — which is the
truth, and cheaper for a caller (a bell that renders for every session) than
a 403 it would have to special-case.
"""

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
from stapel_core.django.api.permissions import (
    ANONYMOUS_ALLOWED,
    IsNotAnonymousUser,
    IsServiceRequest,
    IsStaffUser,
)

from .dto import DeviceListItemResponse, DeviceTokenResponse, FeedItemResponse
from .errors import (
    ERR_400_INVALID_PLATFORM,
    ERR_404_DEVICE_NOT_FOUND,
    ERR_404_TOKEN_NOT_FOUND,
)
from .models import DevicePushToken, NotificationLog
from .serializers import (
    DeviceListItemResponseSerializer,
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


def _device_dto(device: DevicePushToken) -> DeviceListItemResponse:
    """One row of the device list. The raw token never leaves this service.

    ``last_seen`` is the row's ``updated_at``: the last time this token was
    registered. Clients re-register on launch (FCM rotates tokens), so it
    reads as "when this device last announced itself" — not "when we last
    pushed to it", which this table does not record.
    """
    return DeviceListItemResponse(
        id=device.pk,
        token_fingerprint=device.token_fingerprint,
        platform=device.platform,
        is_active=device.is_active,
        created_at=device.created_at.isoformat(),
        last_seen=device.updated_at.isoformat(),
    )


@extend_schema(tags=["Devices"])
class DeviceTokenView(SerializerSeamMixin, APIView):
    """List the caller's push devices, or register one."""

    # A token is a physical device, and the rebinding branch below DELETES a
    # previous account's binding for it. A guest session must not be able to
    # take a device's notification routing away from the real account that
    # last held it — see the module header for the shared-device scenario.
    permission_classes = [IsNotAnonymousUser]
    request_serializer_class = DeviceTokenRequestSerializer
    response_serializer_class = DeviceTokenResponseSerializer
    #: Own seam for GET: the list item is a different DTO from the register
    #: response, so one ``response_serializer_class`` cannot serve both.
    list_serializer_class = DeviceListItemResponseSerializer

    def get_list_serializer_class(self):
        return self.list_serializer_class

    @extend_schema(
        operation_id="list_device_tokens",
        summary="List registered push devices",
        description=(
            "The caller's own push devices, most recently registered first. "
            "The raw token is never returned: a client identifies its own "
            "device by hashing the token it holds (SHA-256, hex) and matching "
            "`token_fingerprint`. Rows with `is_active: false` are still "
            "registered but the push provider has rejected the token, so "
            "nothing is delivered to them."
        ),
        responses={200: DeviceListItemResponseSerializer(many=True)},
    )
    def get(self, request):  # noqa: R007
        # Inactive rows are included on purpose. A toggle that reads this
        # endpoint must be able to say "registered, but the provider dropped
        # the token" — hiding those rows would make the switch render ON for
        # a device that receives nothing, which is the same lie as rendering
        # OFF for one that does.
        devices = DevicePushToken.objects.filter(
            user_id=request.user.id,
        ).order_by("-updated_at", "-id")

        response_cls = self.get_list_serializer_class()
        return StapelResponse(
            response_cls([_device_dto(d) for d in devices], many=True)
        )

    @extend_schema(
        operation_id="register_device_token",
        summary="Register push token",
        request=DeviceTokenRequestSerializer,
        responses={
            201: DeviceTokenResponseSerializer,
            400: StapelErrorSerializer,
        },
    )
    def post(self, request):  # noqa: R007
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

    # Mirror of DeviceTokenView: a session that cannot register a device has
    # no binding of its own to remove (this deletes only rows already owned by
    # the caller, so a guest could only ever have got a 404 here).
    permission_classes = [IsNotAnonymousUser]

    @extend_schema(
        operation_id="unregister_device_token",
        summary="Unregister push token",
        responses={
            204: None,
            404: StapelErrorSerializer,
        },
    )
    def delete(self, request, token):  # noqa: R007
        deleted, _ = DevicePushToken.objects.filter(
            token=token,
            user_id=request.user.id,
        ).delete()

        if not deleted:
            return StapelErrorResponse(404, ERR_404_TOKEN_NOT_FOUND)

        return StapelResponse(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Devices"])
class DeviceUnregisterView(SerializerSeamMixin, APIView):
    """Unregister a push device by the id ``GET /devices/`` handed out.

    The token-keyed sibling above stays: a client that has just minted a token
    and wants it gone does not need a round trip through the list. This one is
    for the other direction — a device row read from the list, unregistered by
    the identifier the list gave, without the caller ever holding the token
    (a second device in the list is not one this client can produce a token
    for at all).
    """

    # Same stance as the register/unregister pair: the device registry is not
    # a guest surface.
    permission_classes = [IsNotAnonymousUser]

    @extend_schema(
        operation_id="unregister_device",
        summary="Unregister a push device by id",
        responses={
            204: None,
            404: StapelErrorSerializer,
        },
    )
    def delete(self, request, device_id):  # noqa: R007
        # Scoped by the caller: an id belonging to somebody else answers 404,
        # the same as an id that never existed. There is nothing to learn here
        # by counting up.
        deleted, _ = DevicePushToken.objects.filter(
            pk=device_id,
            user_id=request.user.id,
        ).delete()

        if not deleted:
            return StapelErrorResponse(404, ERR_404_DEVICE_NOT_FOUND)

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
    def get(self, request):  # noqa: R007
        return StapelResponse(NOTIFICATION_KEYS)


class FeedPagination(CreatedAtAnchorPagination):
    page_size = 20
    max_page_size = 50


@extend_schema(tags=["Feed"])
class NotificationFeedView(SerializerSeamMixin, APIView):
    """User's notification feed (push notifications log)."""

    permission_classes = [IsAuthenticated]
    # Own log only (`user_id=request.user.id`); for a guest the page is
    # necessarily empty, and an empty feed is the truth. A bell icon rendered
    # for every session gets an answer instead of an error to special-case.
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    pagination_class = FeedPagination
    response_serializer_class = FeedItemResponseSerializer

    @extend_schema(
        operation_id="get_notification_feed",
        summary="Get notification feed",
        description="Returns push notification log entries for the authenticated user, ordered by created_at desc.",
        responses={200: FeedItemResponseSerializer(many=True)},
    )
    def get(self, request):  # noqa: R007
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
