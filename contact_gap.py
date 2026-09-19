"""The seam where a recipient turns out to have no address.

Everything this module does exists because that seam used to be silent. A
dispatch that found no e-mail wrote ``status="skipped"`` with
``error_message="no email address for this recipient"`` and a WARNING line
in a consumer's log, and nothing else. On a live fleet that state persisted
for four months: a contact mirror holding 41 relic rows next to an auth
database holding 192 verified addresses, twenty transactional letters —
payment receipts among them — journalled as skipped, and no alert of any
kind, because every individual piece of the system was working exactly as
written. A class of failure that has to be *noticed* is a class of failure
that stays.

Three mechanisms, in order of how early they act:

**1. It shouts.** :func:`report_missing_contact` raises the skip of a
transactional notification for a KNOWN account to ERROR with a stable
fingerprint, ``notification_skipped_no_contact``, which is what a host's
ERROR→alerting bridge is keyed on. Rate-limited per (type, recipient), so a
broken seam produces an alert rather than a flood, and the suppressed count
rides on the next line that gets through.

*Known account* is the discriminator, and it is a cheap one: a skip carrying
a ``user_id`` names somebody whose account exists upstream — the id came
from there — while a skip with no ``user_id`` is the unauthenticated case
(a passcode for an address that has no account yet) where "no address for
this recipient" is ordinary and must stay quiet. No cross-service call is
made on the dispatch path to establish this.

**2. It is measurable.** :func:`publish_contacts_missing_gauge` publishes
``notifications_contacts_missing`` — how many notifications were skipped for
a missing address in the last 24 hours — so the condition has a number that
a dashboard shows and an alert rule can threshold, instead of a log line
somebody has to think to grep for.

**3. It is recoverable.** :func:`park_dispatch` keeps the *request* (never
the rendered letter — see :class:`~stapel_notifications.models.ParkedDispatch`
for why the journal cannot do this) for the narrow, allowlisted set of
transactional types a person is entitled to receive late rather than never.
``manage.py notifications_reconcile_contacts --resend-skipped`` replays them
once the mirror is repaired.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from .conf import notifications_settings

logger = logging.getLogger(__name__)

#: The stable identifier a host's ERROR→alert bridge groups on. Deliberately
#: a constant and not an f-string: an alert rule keyed on a message that
#: interpolates a type name matches nothing the day a new type is added.
FINGERPRINT = "notification_skipped_no_contact"

#: Gauge name. Counts NotificationLog rows, not live memory, so a restarted
#: process reports the same number as the one it replaced.
GAUGE = "notifications_contacts_missing"

#: How long one (type, recipient-hash) pair stays quiet after it has shouted.
_ALERT_TTL_SECONDS = 3600

_SUPPRESSED_KEY = "stapel_notifications:contact_gap:suppressed:%s"
_SEEN_KEY = "stapel_notifications:contact_gap:seen:%s"


# ── 1. it shouts ─────────────────────────────────────────────────────────────


def _cache():
    from django.core.cache import cache

    return cache


def _bucket(notification_type: str, user_id) -> str:
    """The rate-limit bucket. Hashed, because a cache key is a log line
    waiting to happen and a user id in one is a user id in Redis' keyspace."""
    import hashlib

    raw = f"{notification_type}|{user_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def report_missing_contact(
    *, notification_type: str, channel: str, user_id, event_id=None,
) -> bool:
    """A transactional notification for a known account had nowhere to go.

    Returns True when it actually logged (i.e. was not rate-limited), which
    is what the tests assert on — the caller has nothing to do with the
    answer.

    Silent for ``user_id is None``: that is the unauthenticated address-less
    case, which is ordinary and has always been a WARNING at the call site.

    Never carries an address, a name or a rendered body. The ERROR line is
    the *class* of the failure plus the ids needed to find the row; whoever
    is paged reads the journal for the rest, under the access controls the
    journal has.
    """
    if user_id is None:
        return False

    from stapel_core.observability import metrics

    try:
        metrics.counter(
            "notifications_skipped_no_contact_total",
            labels={"type": notification_type, "channel": channel},
        )
    except Exception:  # pragma: no cover - a metrics backend is optional
        logger.debug("contact-gap counter not recorded", exc_info=True)

    bucket = _bucket(notification_type, user_id)
    suppressed = 0
    try:
        cache = _cache()
        seen_key = _SEEN_KEY % bucket
        if cache.get(seen_key):
            try:
                cache.incr(_SUPPRESSED_KEY % bucket)
            except ValueError:
                cache.set(_SUPPRESSED_KEY % bucket, 1, _ALERT_TTL_SECONDS)
            return False
        cache.set(seen_key, 1, _ALERT_TTL_SECONDS)
        suppressed = cache.get(_SUPPRESSED_KEY % bucket) or 0
        cache.delete(_SUPPRESSED_KEY % bucket)
    except Exception:  # pragma: no cover - no cache configured
        # A deployment with no usable cache gets every occurrence rather
        # than none. Losing the rate limit is an annoyance; losing the
        # alert is the bug this module exists to close.
        logger.debug("contact-gap rate limiter unavailable", exc_info=True)

    logger.error(
        "%s: %s/%s skipped for user_id=%s — the account exists upstream but "
        "this service holds no %s address for it, so a transactional "
        "notification was never delivered. Repair the mirror with "
        "`manage.py notifications_reconcile_contacts` and check that "
        "user.contact.changed is being produced and consumed. "
        "event_id=%s suppressed_since_last=%s",
        FINGERPRINT, notification_type, channel, user_id, channel,
        event_id or "-", suppressed,
        extra={"fingerprint": FINGERPRINT, "notification_type": notification_type},
    )
    return True


# ── 2. it is measurable ──────────────────────────────────────────────────────


def contacts_missing_count(hours: int = 24) -> int:
    """Notifications skipped for a missing address in the last *hours*."""
    from datetime import timedelta

    from .models import NotificationLog

    since = timezone.now() - timedelta(hours=hours)
    return NotificationLog.objects.filter(
        status="skipped",
        created_at__gte=since,
        error_message__startswith="no ",
        error_message__endswith="address for this recipient",
    ).count()


def publish_contacts_missing_gauge(hours: int = 24) -> int:
    """Publish :data:`GAUGE` and return the value."""
    from stapel_core.observability import metrics

    value = contacts_missing_count(hours)
    try:
        metrics.gauge(GAUGE, value, labels={"window_hours": str(hours)})
    except Exception:  # pragma: no cover - a metrics backend is optional
        logger.debug("contact-gap gauge not published", exc_info=True)
    return value


# ── 3. it is recoverable ─────────────────────────────────────────────────────


def retry_prefixes() -> tuple[str, ...]:
    return tuple(notifications_settings.RETRY_ON_CONTACT or ())


def retry_window_hours() -> int:
    return int(notifications_settings.RETRY_ON_CONTACT_WINDOW_HOURS or 0)


def is_retry_eligible(notification_type: str) -> bool:
    """Is this type allowed to be parked and replayed?

    Prefix match, so ``recordings.ready`` covers ``recordings.ready_v2`` and
    ``billing.payment_`` covers the whole payment family, which is what a
    host names when it writes the setting. A security-class type can never
    be eligible even if a host lists it — ``checks.E008`` refuses that boot,
    and this is the runtime half of the same statement, because a check can
    be silenced and this cannot.
    """
    from .routing import get_routing, is_security

    if not notification_type:
        return False
    if is_security(notification_type):
        return False
    if get_routing(notification_type) is None:
        # An ad-hoc raw-content notification has no registry entry, so
        # nothing declares its class. Not parked: the request carries a
        # caller-supplied body, and this table is not where that waits.
        return False
    return any(notification_type.startswith(p) for p in retry_prefixes())


def park_dispatch(*, log, user_id, notification_type, channel, event_id,
                  language, request: dict) -> bool:
    """Keep this dispatch's request so it can be sent when the address lands.

    Returns True when a row was written. Idempotent on ``log_id``: the same
    skip parked twice is one row.
    """
    if user_id is None or not is_retry_eligible(notification_type):
        return False

    from .models import ParkedDispatch

    _obj, created = ParkedDispatch.objects.get_or_create(
        log_id=log.id,
        defaults={
            "user_id": user_id,
            "notification_type": notification_type,
            "channel": channel,
            "event_id": event_id or "",
            "language": language or "",
            "request": request,
        },
    )
    if created:
        # No variables, no address — a count and two ids.
        logger.info(
            "parked %s/%s for user_id=%s pending a contact (log_id=%s)",
            notification_type, channel, user_id, log.id,
        )
    return created


def expire_parked(now=None) -> int:
    """Delete parked rows past the retry window. Returns how many."""
    from datetime import timedelta

    from .models import ParkedDispatch

    hours = retry_window_hours()
    if hours <= 0:
        return 0
    cutoff = (now or timezone.now()) - timedelta(hours=hours)
    deleted, _ = ParkedDispatch.objects.filter(created_at__lt=cutoff).delete()
    return int(deleted)


def replayable(now=None):
    """Parked rows still inside the window, oldest first."""
    from datetime import timedelta

    from .models import ParkedDispatch

    hours = retry_window_hours()
    if hours <= 0:
        return ParkedDispatch.objects.none()
    cutoff = (now or timezone.now()) - timedelta(hours=hours)
    return ParkedDispatch.objects.filter(created_at__gte=cutoff).order_by("created_at")


def replay(parked) -> str:
    """Send one parked dispatch, once. Returns the outcome as a word.

    ``"resent"`` — it went out, the parked row is gone and the journal row
    it came from now reads ``sent``. ``"still_unreachable"`` — the mirror
    still holds no address for this account, so the row stays parked until
    it expires. ``"failed"`` — the address was right and the provider
    refused; also still parked, because that outage is ours and the next
    run should try again. ``"gone"`` — there is nothing left for a retry to
    change: the journal row it points at is missing or no longer skipped
    (an erasure, a scrub, a concurrent run), or the dispatch reached no
    channel for a reason an address cannot fix.

    **Why the journal row is flipped rather than a second row appended.**
    ``status`` is what a person answering "did this customer get their
    receipt?" reads. Leaving the skip and adding a sent row beside it makes
    that question ambiguous forever; the skip is not history worth keeping
    once it has been made good, and ``data.resent_from_skipped`` records
    that it happened.

    Idempotency is the parked row's own deletion, inside the same
    transaction as the flip: a second ``--resend-skipped`` finds nothing.
    The delivery claim is the second gate, not the first — the original
    claim was RELEASED when the dispatch found no address, so re-claiming is
    legitimate here and would not be if it had been held.
    """
    from django.db import transaction

    from .models import NotificationLog, UserContact
    from .services import process_notification

    log = NotificationLog.objects.filter(pk=parked.log_id).first()
    if log is None or log.status != "skipped":
        parked.delete()
        return "gone"

    contact = UserContact.objects.filter(
        user_id=parked.user_id, is_active=True
    ).first()
    address = getattr(contact, parked.channel, "") if contact else ""
    if not address:
        return "still_unreachable"

    request = dict(parked.request or {})
    request.pop("notification_type", None)
    request.pop("user_id", None)

    def _counts():
        rows = NotificationLog.objects.filter(
            user_id=parked.user_id,
            notification_type=parked.notification_type,
        )
        return rows.filter(status="sent").count(), rows.filter(
            status="failed"
        ).count()

    sent_before, failed_before = _counts()
    process_notification(
        notification_type=parked.notification_type,
        user_id=str(parked.user_id),
        **request,
    )
    sent_after, failed_after = _counts()

    if sent_after <= sent_before:
        if failed_after > failed_before:
            # The provider was reached and refused. NOT dropped: the address
            # is right and the outage is ours, so the row stays parked and
            # the next run tries again until the window closes. Deleting
            # here would turn a transient mail-provider failure into the
            # same silent loss this whole module exists to stop.
            return "failed"
        # Handed to no provider at all (a preference now says no, a claim
        # is already held). Not an error, and not a resend either — there
        # is nothing left for a retry to change.
        parked.delete()
        return "gone"

    with transaction.atomic():
        log.status = "sent"
        log.error_message = ""
        log.data = {**(log.data or {}), "resent_from_skipped": True}
        log.save(update_fields=["status", "error_message", "data"])
        parked.delete()
    return "resent"
