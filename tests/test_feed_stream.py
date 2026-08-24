"""The feed socket: who may watch it, and what arrives on it.

Driven through ``stapel_realtime.testing`` — the substrate's own harness,
which speaks the v1 envelope — so these tests assert this module's stream
rather than re-implementing a WebSocket client.

Skipped, not failed, without the optional ``[realtime]`` extra: the socket is
an extra here (see MODULE.md § "Live feed"), so the suite must be green on a
bare install too. CI installs the extra so the skip is never the normal
outcome.
"""

import uuid

import pytest

pytest.importorskip("channels", reason="stapel-notifications[realtime] not installed")
pytest.importorskip(
    "stapel_realtime", reason="stapel-notifications[realtime] not installed"
)

from channels.db import database_sync_to_async  # noqa: E402
from channels.testing.websocket import WebsocketCommunicator  # noqa: E402
from stapel_realtime import envelope as wire  # noqa: E402
from stapel_realtime.testing import StreamClient  # noqa: E402

from stapel_notifications import realtime  # noqa: E402
from stapel_notifications.consumers import NotificationInboxConsumer  # noqa: E402
from stapel_notifications.models import NotificationLog  # noqa: E402

# The asyncio marker is per-test, not module-wide: the two route-manifest
# tests below are ordinary synchronous asserts.


async def _open(user):
    comm = WebsocketCommunicator(
        NotificationInboxConsumer.as_asgi(), "/ws/notifications/inbox"
    )
    comm.scope["url_route"] = {"kwargs": {}}
    comm.scope["user"] = user
    connected, code = await comm.connect()
    return StreamClient(comm, connected=connected, close_code=code)


def _log_row(user_id, **kwargs):
    defaults = {
        "user_id": user_id,
        "notification_type": "listing_blocked",
        "channel": "push",
        "status": "sent",
        "language": "en",
        "recipient": str(user_id),
        "title": "Your listing has been blocked",
        "body": "It broke a guideline.",
        "data": {"listing_url": "/listings/7"},
    }
    return NotificationLog.objects.create(**{**defaults, **kwargs})


# ── the route manifest ──────────────────────────────────────────────────


def test_routing_exports_the_socket_under_the_canonical_prefix():
    """``build_websocket_application()`` discovers this, and realtime.W004
    warns about anything outside ``ws/<module>/…``."""
    from stapel_notifications import routing

    routes = routing.websocket_urlpatterns
    assert len(routes) == 1
    assert str(routes[0].pattern) == "ws/notifications/inbox"


def test_the_route_carries_no_user_segment():
    """The stream key comes from the authenticated scope, so there is no id
    in the URL to tamper with."""
    from stapel_notifications import routing

    assert "<" not in routing.WEBSOCKET_ROUTE


# ── who may watch ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_anonymous_scope_subscribes_to_nothing():
    from django.contrib.auth.models import AnonymousUser

    sock = await _open(AnonymousUser())
    assert not sock.connected
    assert sock.close_code is not None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_signed_in_caller_watches_its_own_stream(user):
    sock = await _open(user)
    assert sock.connected
    await database_sync_to_async(realtime.broadcast_feed_item)(
        await database_sync_to_async(_log_row)(user.id)
    )
    frame = await sock.receive(timeout=3)
    assert frame.stream == realtime.user_stream(user.id)
    await sock.communicator.disconnect()


# ── what arrives ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_new_notification_arrives_as_a_v1_signal_frame(user):
    sock = await _open(user)
    row = await database_sync_to_async(_log_row)(user.id)
    await database_sync_to_async(realtime.broadcast_feed_item)(row)

    frame = await sock.receive(timeout=3)
    assert frame.type == realtime.SIGNAL_NEW
    assert frame.is_signal, "a feed item is a signal, never a protocol frame"
    assert frame.seq is None, "the feed stream is ephemeral — no journal cursor"
    assert frame.payload["id"] == str(row.id)
    assert frame.payload["title"] == "Your listing has been blocked"
    assert frame.payload["data"]["listing_url"] == "/listings/7", (
        "the deep link a client needs to open what the notification is about"
    )
    await sock.communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_nobody_hears_another_recipients_notification(user, other_user):
    sock = await _open(user)
    await database_sync_to_async(realtime.broadcast_feed_item)(
        await database_sync_to_async(_log_row)(other_user.id)
    )
    assert await sock.communicator.receive_nothing(timeout=0.5)
    await sock.communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_hello_is_answered_as_ephemeral(user):
    """No resume, no replay: what this socket missed is one GET /feed/ away."""
    sock = await _open(user)
    welcome = await sock.hello()
    assert welcome.type == wire.WELCOME
    assert welcome.payload["ephemeral"] is True
    await sock.communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_row_for_nobody_signals_nobody(user):
    """A notification with no recipient (a bare address send) has no stream
    to go to — and must not raise on the delivery path."""
    sock = await _open(user)
    row = await database_sync_to_async(_log_row)(None, recipient="x@example.com")
    await database_sync_to_async(realtime.broadcast_feed_item)(row)
    assert await sock.communicator.receive_nothing(timeout=0.5)
    await sock.communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_recipient_with_nothing_open_is_the_normal_case(user):
    """Nobody is watching: the frame is dropped and that is correct
    behaviour, not an incident."""
    row = await database_sync_to_async(_log_row)(uuid.uuid4())
    await database_sync_to_async(realtime.broadcast_feed_item)(row)
