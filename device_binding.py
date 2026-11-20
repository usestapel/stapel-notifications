"""Which session and which installation a device registration belongs to.

A push device row is bound to two things besides the account:

* the **session** that registered it — ``user.session_revoked`` for that
  session deletes the row (``actions.handle_user_session_revoked``), so a
  handset that signed out, or whose session was revoked elsewhere, stops
  receiving the account's pushes even when its own unregister never arrived;
* the **installation** (``device_id``) — one installation holds one row per
  account, so a rotated token refreshes the row instead of adding one, and a
  token another account still holds on the same installation is handed over
  instead of refused.

Access tokens carry no session claim: stapel-auth finds the session of a
request through its own access-token ledger
(``stapel_auth.sessions.services.current_session``). So the session is read
from auth when auth runs in this process, and is otherwise the one the client
declares — the id auth's own session list marks ``is_current``. A declared id
is not verified here, and does not need to be for safety: a client that lies
can only make its own device row outlive (unbound) or die with (another of its
own sessions) a session of the same account, never touch another account.
"""
from __future__ import annotations

import logging
import re

from django.apps import apps

logger = logging.getLogger(__name__)

#: Same alphabet and floor as stapel-auth's installation id: opaque, random,
#: generated once per install — never a readable name.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9\-._~:+/=]{16,255}$")

#: The longest session id the row stores (auth's are UUIDs, 36 characters).
SESSION_ID_MAX = 64


def valid_device_id(value: str | None) -> bool:
    """True for an absent id and for a well-formed one."""
    return value is None or bool(DEVICE_ID_RE.match(value))


def resolve_session(request, declared: str | None) -> str | None:
    """The session this request registers a device under, or ``None``.

    stapel-auth in this process answers from its ledger and wins over the
    body; otherwise the declared id is taken as given (bounded in length).
    """
    if apps.is_installed("stapel_auth"):
        try:
            from stapel_auth.sessions.services import current_session
        except ImportError:  # an auth too old to expose the resolver
            current_session = None
        if current_session is not None:
            session = current_session(request)
            if session is not None:
                return str(session.pk)
    if declared:
        declared = str(declared).strip()
        if declared and len(declared) <= SESSION_ID_MAX:
            return declared
    return None
