"""Which language a notification is written in, and on whose authority.

The chain is ordered by *whose statement each step is*, strongest first.
Every step is a decision with a reason, including the last one — the old
chain ended in a fallthrough, and a fallthrough is how every recipient of
this library came to be written to in the SENDER's language:

1. ``recipient_choice`` — the language the recipient CHOSE, asked of the
   module that owns the fact (``profiles.language``) at send time. Nobody
   outranks a person's own stated preference about how they are written to.
2. ``caller`` — the ``language`` argument. The caller knows something about
   THIS message that we cannot: an anonymous OTP is answering a request the
   recipient themselves just made, so that request's language is theirs.
   It sits below (1) because the caller passes the *request's* language,
   which for a notification sent by one person to another is the sender's.
3. ``recipient_detected`` — the last language OBSERVED for the recipient
   (their Accept-Language, mirrored by profiles). About the right person,
   but never stated by them, so it loses to anything they actually said.
4. ``sender`` — the language active in the process doing the send, i.e. the
   language of whoever pressed the button. **A decision, not a fallthrough:**
   an unregistered invitee has no profile and no preference and will not
   have one until they accept, so the only fact in the system about how to
   address them is that someone who presumably knows them wrote to them
   from a UI in this language. It is a guess, it is labelled a guess in the
   delivery log (``language_source``), and it beats writing to a Russian
   colleague's invitee in English because nobody had a better idea.
5. ``default`` — the project's configured default language. Reached only
   when the process has no active language at all (a consumer/worker with
   no request in scope).

What each recipient gets, plainly:

* a registered user who chose a language → **that language** (step 1);
* a registered user who chose nothing → the language they were last SEEN
  in (step 3), and only if profiles never saw one either, the sender's;
* an unregistered invitee → the **sender's** language (step 4), by the
  decision above.

**Why a call and not a mirror.** This module used to keep its own copy of
``app_language`` in ``UserNotificationSettings``, fed by a bus consumer.
The copy was empty for 100% of users for the mirror's entire lifetime
(meettoday sandbox, 2026-08: 0 rows against 66 profiles), for two
independent reasons — a monolith on the in-process bus cannot run the
standalone consumer at all, and the consumer subscribed to a topic the
comm plane does not publish to. The failure was invisible because a mirror
answers ``None`` both for "the user chose nothing" and for "the sync never
ran", and those two demand opposite responses. A call cannot hide that: it
either answers (and ``None`` then means the user really chose nothing) or
it raises, which is reported as ``language_source="sender"`` with
``recipient_reachable=False``, logged loudly, and caught at boot by
``checks.check_recipient_language_is_askable``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: comm Function owned by stapel-profiles (>= 0.12.1): the recipient's own
#: stated/observed language. Named, not imported — profiles may live in
#: another process entirely.
PROFILES_LANGUAGE = "profiles.language"

#: ``language_source`` values, recorded on every delivery row so an operator
#: can measure how many letters were addressed on a guess.
SOURCE_RECIPIENT_CHOICE = "recipient_choice"
SOURCE_CALLER = "caller"
SOURCE_RECIPIENT_DETECTED = "recipient_detected"
SOURCE_SENDER = "sender"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class LanguageChoice:
    """The resolved language plus *why* — the why is what makes it auditable."""

    language: str
    source: str
    #: False only when profiles could not be ASKED (absent provider, dead
    #: route, provider error). Distinct from "asked, and they have no
    #: preference": that answers True with source below the recipient steps.
    recipient_reachable: bool = True

    @property
    def is_guess(self) -> bool:
        """True when nothing about the RECIPIENT decided this language."""
        return self.source in (SOURCE_SENDER, SOURCE_DEFAULT)


def ask_profiles(user_id) -> tuple[str | None, str | None, bool]:
    """``(chosen, detected, reachable)`` for *user_id* from profiles.

    Never raises: a notification must still go out when the language plane
    is broken — in the wrong language is recoverable, unsent is not. The
    breakage is returned (``reachable=False``) rather than swallowed, so the
    caller can say so in the log and in the delivery row.
    """
    from stapel_core.comm import call
    from stapel_core.comm.exceptions import (
        FunctionCallError,
        FunctionNotRegistered,
        FunctionRouteNotConfigured,
    )

    try:
        answer = call(PROFILES_LANGUAGE, {"user_id": str(user_id)}) or {}
    except (FunctionNotRegistered, FunctionRouteNotConfigured) as exc:
        # Wiring, not weather: this deployment cannot ask anybody what
        # language they read in, so EVERY recipient is about to be addressed
        # in the sender's. `manage.py check` says the same thing at boot.
        logger.warning(
            "RECIPIENT LANGUAGE UNASKABLE: no reachable provider for %s (%s) "
            "— every recipient will be written to in the sender's language",
            PROFILES_LANGUAGE, exc,
        )
        return None, None, False
    except FunctionCallError:
        logger.exception(
            "RECIPIENT LANGUAGE UNASKABLE: %s failed for user %s — falling "
            "back to the sender's language", PROFILES_LANGUAGE, user_id,
        )
        return None, None, False

    if not isinstance(answer, dict):
        logger.error(
            "%s returned %r, expected a mapping", PROFILES_LANGUAGE, type(answer)
        )
        return None, None, False
    return (
        answer.get("app_language") or None,
        answer.get("auto_detected_language") or None,
        True,
    )


def resolve(user_id, language: str | None) -> LanguageChoice:
    """Decide the language of one notification. See the module docstring."""
    from .services import _active_language, default_language

    chosen = detected = None
    reachable = True
    if user_id:
        chosen, detected, reachable = ask_profiles(user_id)

    if chosen:
        return LanguageChoice(chosen, SOURCE_RECIPIENT_CHOICE, reachable)
    if language:
        return LanguageChoice(language, SOURCE_CALLER, reachable)
    if detected:
        return LanguageChoice(detected, SOURCE_RECIPIENT_DETECTED, reachable)

    sender = _active_language()
    if sender:
        return LanguageChoice(sender, SOURCE_SENDER, reachable)
    return LanguageChoice(default_language(), SOURCE_DEFAULT, reachable)
