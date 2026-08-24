"""The feed, live: what this module puts on the wire, and where.

One stream, and it is the recipient's own:

``notifications:user:<user_id>`` — the **feed inbox**. Ephemeral. Everything
it carries is already readable at ``GET /notifications/api/v1/feed/``, which
is the substrate's condition for a fact being allowed to travel as a Signal.
It exists so that the REST read is not the *normal* path: without it a
notification bell has no socket, and a bell with no socket refreshes on a
timer forever — or, as the frontend audit found, never refreshes at all and
the page learns about a notification only when someone reloads it.

**Emitting is free; serving the socket is an extra.** The emitter here is
``stapel_core.comm.signal()`` — stdlib, already a dependency, and a silent
no-op on a host with no ``STAPEL_COMM["SIGNAL_TRANSPORT"]``. So a deployment
that has never heard of WebSockets pays nothing for this file and keeps
working exactly as before. The *delivery* half — the consumer, per-stream
authorization, the kick — is ``stapel-realtime``, and it is an optional
extra (``pip install 'stapel-notifications[realtime]'``) rather than a base
dependency: unlike chat, this module's product is a push notification and a
REST feed, both of which are complete without a socket. See
:mod:`stapel_notifications.consumers` and MODULE.md § "Live feed".

Payload minimalism, the substrate's review-checklist item, is satisfied by
the stream's gate: subscription to ``notifications:user:<id>`` *is* being
that user, and being that user is exactly the right to read that feed row
over REST. So the row itself may travel, and a client can prepend the item
instead of refetching a page to find out what arrived.

Best-effort is the contract, not a caveat. No transport, no subscriber, redis
down: the frame is dropped and nothing raises, because the truth is the
``NotificationLog`` row and ``GET /feed/`` still returns it. Delivery is
scheduled by ``signal()`` through ``transaction.on_commit`` — the row is
durable before anyone is told about it.

There is no ``notification.read`` signal. This library has no read state at
all: ``NotificationLog`` records what was *sent*, there is no mark-as-read
endpoint and therefore no unread count, so a read event would be a frame
nothing can emit and nothing can act on. When read state lands, it belongs on
this same stream as a second signal type.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Canonical stream-key module segment.
STREAM_MODULE = "notifications"

#: A notification was just delivered to this recipient — the feed row that the
#: next ``GET /feed/`` would return at the top. Never a protocol frame type;
#: the core refuses a signal type that claims one.
SIGNAL_NEW = "notification.new"


def user_stream(user_id) -> str:
    """``notifications:user:<id>`` — one recipient's ephemeral feed stream."""
    from stapel_core.comm import stream_key

    return stream_key(STREAM_MODULE, "user", str(user_id))


def feed_item_payload(entry) -> dict:
    """The feed row as the wire carries it.

    Deliberately field-for-field the same shape as
    :class:`~stapel_notifications.dto.FeedItemResponse`, so a client parses
    one type whether the item arrived over the socket or on a page of
    ``GET /feed/`` — a frame with a shape of its own is how a client ends up
    with two notification models that drift.
    """
    return {
        "id": str(entry.id),
        "notification_type": entry.notification_type,
        "title": entry.title,
        "body": entry.body,
        "data": dict(entry.data or {}),
        "created_at": entry.created_at.isoformat(),
    }


def broadcast_feed_item(entry) -> None:
    """Tell the recipient's open screens that a feed row just landed.

    Called after the journal row is written; ``signal()`` defers to
    ``transaction.on_commit``, so nothing is announced before it is durable.
    A courtesy never breaks a delivery: every failure below is swallowed,
    because the notification itself has already been sent and logged.
    """
    if not entry.user_id:
        return
    try:
        from stapel_core.comm import signal

        signal(user_stream(entry.user_id), SIGNAL_NEW, feed_item_payload(entry))
    except Exception:  # pragma: no cover - a courtesy never breaks a caller
        logger.debug(
            "notifications: feed signal skipped for user %s",
            entry.user_id,
            exc_info=True,
        )


__all__ = [
    "SIGNAL_NEW",
    "STREAM_MODULE",
    "broadcast_feed_item",
    "feed_item_payload",
    "user_stream",
]
