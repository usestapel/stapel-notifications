"""The feed socket. One per recipient, read-only, ephemeral.

Built on ``stapel_realtime.EphemeralStreamConsumer`` — the substrate that
exists so the fleet stops writing its fourth WebSocket. Nothing here
implements a socket: it names its stream and says who may watch it.

The route carries **no** user segment (see :mod:`stapel_notifications.routing`)
— the stream key is derived from the authenticated scope, so there is no id in
the URL to tamper with and ``authorize()`` has one question to answer: is
anybody signed in.

Writes never come over this socket. A device is registered over REST, a feed
page is read over REST; the socket only says "something arrived". That is the
substrate's default posture and this module has no reason to be the exception
chat is.

``stapel-realtime`` (and its Channels extra) is an **optional** dependency of
this library. Importing this module without it raises a clear ImportError, and
it is never imported at app-ready time — ``routing.py`` resolves it lazily, so
a host that serves HTTP only never loads an ASGI stack.
"""
from __future__ import annotations

try:
    from stapel_realtime.consumers import EphemeralStreamConsumer
except ImportError as exc:  # pragma: no cover - exercised via optional-dep test
    raise ImportError(
        "stapel_notifications.consumers requires the optional 'stapel-realtime' "
        "dependency (and its Channels extra). Install it with:\n"
        "    pip install 'stapel-notifications[realtime]'"
    ) from exc

from .realtime import user_stream


class NotificationInboxConsumer(EphemeralStreamConsumer):
    """One socket ↔ one recipient's feed stream.

    Ephemeral by nature, not by economy: a frame this socket misses is one
    ``GET /feed/`` away, and the page the client renders on load is that same
    read. Losing a signal here costs a few seconds of latency on a row that
    the next page load shows anyway — which is precisely the bargain the
    Signal primitive is for.
    """

    module = "notifications"
    scope_type = "user"

    async def get_stream_key(self) -> str:
        return user_stream(self._user_id())

    async def authorize(self, scope, stream_key) -> bool:
        """You may watch exactly one feed: your own.

        The key comes from the authenticated scope rather than the URL, so
        "your own" is not a comparison that can be got wrong — there is
        nothing else to name. An unauthenticated scope subscribes to nothing
        (the substrate's gate is fail-closed).
        """
        return self._user_id() is not None


__all__ = ["NotificationInboxConsumer"]
