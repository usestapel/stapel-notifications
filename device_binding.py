"""Which session and which installation a device registration belongs to.

A push device row is bound to two things besides the account:

* the **session** that registered it — ``user.session_revoked`` or
  ``user.session_expired`` for that session deletes the row
  (``actions.handle_user_session_ended``), so a handset that signed out, whose
  session was revoked elsewhere, or whose session ran out, stops receiving the
  account's pushes even when its own unregister never arrived;
* the **installation** (``device_id``) — one installation holds one row per
  account, so a rotated token refreshes the row instead of adding one, and a
  token another account still holds on the same installation is handed over
  instead of refused.

The session comes from the credential, in this order:

1. the ``sid`` claim of the verified access token
   (``stapel_core.django.jwt.claims.request_session_id``) — signed by the
   issuer, readable in any process that verifies tokens, with or without
   stapel-auth installed. When it is present a ``session_id`` in the body is
   ignored, not refused;
2. stapel-auth's own resolver when auth runs in this process
   (``current_session``, its access-token ledger) — tokens issued before the
   claim existed;
3. the ``session_id`` the client declares (the id auth's session list marks
   ``is_current``) — only when the token carries no claim. Not verifiable
   here, and safe anyway: a client that lies can only make its own device row
   outlive (unbound) or die with another of its own sessions, never touch
   another account.
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

    The token's verified ``sid`` claim wins; then stapel-auth's ledger when
    it runs in this process; the declared id (bounded in length) only when
    neither knows the session.
    """
    from stapel_core.django.jwt.claims import request_session_id

    claimed = request_session_id(request)
    if claimed:
        return claimed
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
