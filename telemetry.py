"""What a delivery journal is allowed to remember.

``NotificationLog`` is a delivery journal, and a journal that copies the
caller's template variables into itself is a credential store. The variables
of this library's own built-in types are, literally: a one-time passcode
(``otp_code.code``), a sign-in link with its token in the query string
(``magic_link_login``), an invitation acceptance URL that both creates an
account and joins it to a workspace, and the initial password of an
org-provisioned account. Every one of those was persisted verbatim, and the
table is exposed in the Django admin and kept for the life of the deployment.

So the rule here is deny-by-default, in two independent layers:

1. **Keys.** A caller variable reaches the journal only if somebody DECLARED
   it as telemetry — in the routing entry (``"telemetry": [...]``), in
   ``STAPEL_NOTIFICATIONS["TELEMETRY"]``, or because it is one of the deep
   links the push feed needs to open the thing the notification is about.
   An undeclared key is not stored. A denylist of known-bad names
   (``code``, ``token``, ``password``…) would have to be right about a name
   nobody has invented yet; this has to be right about names somebody
   deliberately wrote down.

2. **Shapes.** Even a declared key is dropped when its VALUE looks like a
   credential — a link carrying a query token, a JWT, an opaque high-entropy
   run, a short digit run. This is what makes the allowlist survive being
   filled in by somebody who did not think hard: declaring ``reset_url`` as
   telemetry does not get the reset token into the table.

Both layers run again inside ``NotificationLog.save()``, so the guarantee is
a property of the TABLE and not of one call site in ``services.py`` — host
code, a data migration and a future channel are all covered by the same
mechanism.
"""
from __future__ import annotations

import re

#: Deep-link variables the push feed reads back out of ``NotificationLog.data``
#: (``views.NotificationFeedView`` hands ``data`` to the client so a feed item
#: can open the chat/listing it is about). Allowed for every type because they
#: are the library's own names for "where this notification points", and they
#: still have to survive the shape sieve below — a deep link that carries a
#: token in its query string is not stored.
DEEP_LINK_KEYS = ("chat_url", "listing_url", "notifications_chat_url")

#: Facts this library writes about a delivery itself, as opposed to anything
#: the caller passed. Allowlisted so the journal's own columns survive the
#: filter that runs over the whole ``data`` dict in ``save()``.
JOURNAL_KEYS = frozenset({
    "notification_type",
    "language_source",
    "recipient_language_unaskable",
    "event_id",
})

#: The one journal key that is opaque BY DESIGN: a bus message id is not a
#: credential, it is the handle an operator needs to tie a delivery row back
#: to the event that caused it. Exempt from the shape sieve, which would
#: otherwise read a 32-char message id as a token.
_OPAQUE_BY_DESIGN = frozenset({"event_id"})

#: Placeholder left where a secret-shaped value was removed. A visible marker
#: rather than a silent drop: an operator reading the journal should be able
#: to tell "this notification carried a link" from "this notification had no
#: link", without the link.
REDACTED = "[redacted]"

_UUID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
#: The charset a base64url / hex / opaque-id token is drawn from.
_TOKEN_CHARS = re.compile(r"\A[A-Za-z0-9._~+/=-]+\Z")
#: A JWT, or anything else shaped like one.
_JWT = re.compile(r"\A[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*\Z")
_HEX = re.compile(r"\A[0-9a-fA-F]{16,}\Z")
#: A passcode/PIN: the shape of every OTP this library sends.
_DIGIT_RUN = re.compile(r"\A[0-9]{4,10}\Z")

#: Free text: the substrings that are unmistakably credential carriers. Used
#: on the journal's ``title``/``body`` copy, where the cost of a false
#: positive is a mangled feed item, so the rules here are narrower than
#: ``looks_secret`` — a link with parameters, a JWT, and a long opaque run.
_TEXT_PATTERNS = (
    re.compile(r"https?://[^\s<>\"']*[?#][^\s<>\"']*"),
    re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?<![A-Za-z0-9._~+/=-])(?=[A-Za-z0-9._~+/=-]*[0-9])"
               r"[A-Za-z0-9._~+/=-]{24,}(?![A-Za-z0-9._~+/=-])"),
)


def looks_secret(value) -> bool:
    """Does this value have the shape of a credential?

    Deliberately trigger-happy: it only ever runs on values already declared
    as telemetry, where the cost of a false positive is one missing analytics
    field and the cost of a false negative is a passcode in a table the admin
    renders. Non-strings (ints, bools, floats) are never secret-shaped — a
    library that refused to journal ``expiry_minutes=5`` would be useless —
    and a canonical UUID is an identifier, which is the whole point of
    telemetry.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    if _UUID.match(candidate):
        return False
    if "://" in candidate:
        # A link is where every recovery artifact this library sends actually
        # travels: the token is in the query string, the fragment, or the
        # last path segment. A bare https://host/path stays — that is a
        # destination, not a credential.
        url = candidate.split()[0] if " " in candidate else candidate
        if "?" in url or "#" in url:
            return True
        return any(_is_opaque(part) for part in url.split("/")[3:])
    if " " in candidate or "\n" in candidate:
        return False  # prose, not a token
    if _DIGIT_RUN.match(candidate):
        return True
    return _is_opaque(candidate)


def _is_opaque(candidate: str) -> bool:
    """A single word with no meaning to a human: token, hash, id-blob."""
    if _JWT.match(candidate):
        return True
    if _HEX.match(candidate):
        return True
    if len(candidate) < 12 or not _TOKEN_CHARS.match(candidate):
        return False
    # A long word is only opaque when it mixes letters and digits; a
    # hyphenated slug ("workspace-invitation") is a name, not a secret.
    return any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate)


def redact_text(text):
    """Strip credential carriers out of free text (journal title/body).

    Narrower than ``looks_secret`` on purpose — this runs over copy a human
    reads back in the notification feed, so it removes only what cannot be
    anything but a credential.
    """
    if not isinstance(text, str) or not text:
        return text
    for pattern in _TEXT_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def telemetry_keys(notification_type: str, routing: dict | None = None) -> frozenset[str]:
    """The variable names this type may journal, from all three declarations.

    ``routing`` is passed in by ``services`` because the raw-content escape
    hatch synthesises an entry that no registry lookup would find — the same
    reason ``routing.unsubscribe_allowed`` takes an entry rather than a name.
    """
    from .conf import notifications_settings
    from .routing import get_routing

    if routing is None:
        routing = get_routing(notification_type) or {}
    declared = set(DEEP_LINK_KEYS) | set(JOURNAL_KEYS)
    declared |= _as_key_set(routing.get("telemetry"))
    configured = notifications_settings.TELEMETRY or {}
    declared |= _as_key_set(configured.get("*"))
    declared |= _as_key_set(configured.get(notification_type))
    return frozenset(declared)


def _as_key_set(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        return {value}
    return {str(key) for key in value}


def telemetry(notification_type: str, variables: dict, routing: dict | None = None) -> dict:
    """The journallable part of a caller's variables — nothing else."""
    return _scrub(variables, telemetry_keys(notification_type, routing))


def scrub_data(notification_type: str, data: dict | None) -> dict:
    """Both layers applied to a whole ``data`` dict — the table's own guard.

    Called from ``NotificationLog.save()``, so it must be safe on a row that
    is already clean (idempotent) and on a row written by host code that
    never heard of ``telemetry()``.
    """
    if not isinstance(data, dict) or not data:
        return data if isinstance(data, dict) else {}
    return _scrub(data, telemetry_keys(notification_type))


def _scrub(data: dict | None, allowed: frozenset[str]) -> dict:
    scrubbed = {}
    for key, value in (data or {}).items():
        if key not in allowed:
            continue  # deny-by-default: nobody declared this one
        if key in _OPAQUE_BY_DESIGN:
            scrubbed[key] = value
            continue
        if isinstance(value, str) and looks_secret(value):
            # Kept as a marker rather than dropped: "this delivery carried a
            # link" is legitimate telemetry, the link is not.
            scrubbed[key] = REDACTED
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            scrubbed[key] = value
        # Anything else (nested dicts/lists) is not telemetry this library
        # knows how to inspect, so it is not stored.
    return scrubbed
