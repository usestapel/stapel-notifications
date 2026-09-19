"""Repair the contact mirror from its source, and deliver what it cost.

    python manage.py notifications_reconcile_contacts --dry-run
    python manage.py notifications_reconcile_contacts
    python manage.py notifications_reconcile_contacts --since 2026-09-01
    python manage.py notifications_reconcile_contacts --resend-skipped --dry-run
    python manage.py notifications_reconcile_contacts --resend-skipped

``UserContact`` is a projection, and every projection eventually needs a
pull: a consumer deployed after the accounts existed, a handler down while
the facts aged out of the topic, a bulk ``QuerySet.update()`` upstream that
no model observer can see — or, the case this command was written for, a
producer that never emitted at all. A mirror holding 41 relic rows sat next
to an auth database holding 192 verified addresses for four months, and the
only outward sign was transactional mail quietly journalled as ``skipped``.
A fact stream with no reconciler is a fact stream you are trusting.

The authoritative read is the ``auth.contacts_page`` comm Function —
keyset-paginated, service-only, addresses plus their verification state.
This command walks it and upserts the mirror idempotently.

**Output is counts.** created / updated / unchanged / missing-upstream, and
for ``--resend-skipped`` resent / still-unreachable / expired / gone. Never
an address, a name or a rendered body: an operator running this in an
incident is usually pasting the output somewhere, and a repair tool that
prints its subjects' e-mail addresses makes that paste a disclosure.

``--dry-run`` reads everything and writes nothing, including the resend
pass, so the counts can be seen before they are caused.
"""

from django.core.management.base import BaseCommand, CommandError

PAGE_SIZE = 200
MAX_PAGES = 10_000


class Command(BaseCommand):
    help = (
        "Pull authoritative contacts from auth (auth.contacts_page) and "
        "upsert the UserContact mirror; optionally re-send transactional "
        "notifications that were skipped for a missing address."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read and report; write nothing.",
        )
        parser.add_argument(
            "--since",
            default="",
            help=(
                "ISO-8601 instant. Restrict the pull to accounts modified at "
                "or after it (ignored when the upstream user model records "
                "no modification time)."
            ),
        )
        parser.add_argument(
            "--resend-skipped",
            action="store_true",
            help=(
                "After reconciling, send the parked transactional "
                "notifications whose recipient now has an address."
            ),
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=PAGE_SIZE,
            help=f"Upstream page size (default {PAGE_SIZE}, max 1000).",
        )

    # ── the pull ────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        from stapel_core.comm import call
        from stapel_core.comm.exceptions import (
            FunctionNotRegistered,
            FunctionRouteNotConfigured,
        )

        from stapel_notifications.contact_gap import publish_contacts_missing_gauge
        from stapel_notifications.models import UserContact

        dry_run = options["dry_run"]
        page_size = max(1, min(int(options["page_size"]), 1000))
        since = (options["since"] or "").strip() or None

        created = updated = unchanged = 0
        seen = 0
        cursor = None
        pages = 0

        while True:
            pages += 1
            if pages > MAX_PAGES:
                raise CommandError(
                    "upstream paging did not terminate — refusing to loop "
                    f"past {MAX_PAGES} pages"
                )
            payload = {"limit": page_size, "addressable_only": True}
            if cursor:
                payload["after"] = cursor
            if since:
                payload["since"] = since
            try:
                answer = call("auth.contacts_page", payload)
            except (FunctionNotRegistered, FunctionRouteNotConfigured) as exc:
                raise CommandError(
                    "auth.contacts_page is not reachable from this service "
                    "(register stapel-auth in-process, or route the Function "
                    f"at the auth service): {exc}"
                ) from exc

            rows = (answer or {}).get("contacts") or []
            for row in rows:
                seen += 1
                outcome = self._apply(UserContact, row, dry_run=dry_run)
                if outcome == "created":
                    created += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    unchanged += 1

            cursor = (answer or {}).get("next")
            if not cursor:
                break

        # Rows this mirror holds that the source no longer knows about. NOT
        # deleted here: a contact that vanished upstream is either an
        # erasure (which has its own event and its own receipt) or a
        # --since window that simply did not include it, and a reconciler
        # that deletes on the strength of "I did not see it" is how a
        # repair tool becomes an outage. Counted so it is visible.
        missing_upstream = 0
        if not since:
            missing_upstream = self._missing_upstream(UserContact, seen)

        self.stdout.write("contact reconcile:")
        self.stdout.write(f"  upstream contacts read : {seen}")
        self.stdout.write(f"  created                : {created}")
        self.stdout.write(f"  updated                : {updated}")
        self.stdout.write(f"  unchanged              : {unchanged}")
        self.stdout.write(f"  missing upstream       : {missing_upstream}")
        if dry_run:
            self.stdout.write("  (dry run — nothing was written)")

        gauge = publish_contacts_missing_gauge()
        self.stdout.write(f"  notifications_contacts_missing (24h): {gauge}")

        if options["resend_skipped"]:
            self._resend(dry_run=dry_run)

    def _apply(self, UserContact, row, *, dry_run: bool) -> str:
        user_id = row.get("user_id")
        if not user_id:
            return "unchanged"
        desired = {
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
        }
        existing = UserContact.objects.filter(user_id=user_id).first()
        if existing is None:
            if not dry_run:
                UserContact.objects.create(
                    user_id=user_id, is_active=True, **desired
                )
            return "created"
        drift = any(
            getattr(existing, key) != value for key, value in desired.items()
        )
        if not drift and existing.is_active:
            return "unchanged"
        if not dry_run:
            for key, value in desired.items():
                setattr(existing, key, value)
            # A repaired contact is a reachable one: a row soft-deactivated
            # during a closure grace period that the source still reports is
            # a live account, and the legacy consumer reactivates it too.
            existing.is_active = True
            existing.save(update_fields=[*desired, "is_active", "updated_at"])
        return "updated"

    def _missing_upstream(self, UserContact, seen: int) -> int:
        total = UserContact.objects.count()
        return max(0, total - seen)

    # ── the resend ──────────────────────────────────────────────────────

    def _resend(self, *, dry_run: bool):
        """Send the parked transactional notifications that can now land.

        Policy, stated plainly: a notification skipped because this service
        had no address is re-sent ONCE, and only if it is one of the
        allowlisted transactional types
        (``STAPEL_NOTIFICATIONS["RETRY_ON_CONTACT"]``) and still inside the
        window (``RETRY_ON_CONTACT_WINDOW_HOURS``, 72 by default). Older
        ones are dropped unsent and counted.

        The window is the honest part. "You paid" and "your thing is ready"
        are worth delivering late; three weeks late they are an artefact of
        an outage the recipient cannot place, arriving next to a product
        that has moved on. And a passcode is never in this set at all — it
        expired long before the address arrived, and its variables are not
        something this service keeps.

        A notification skipped BEFORE this mechanism shipped has no parked
        row and cannot be re-sent from here: the journal records that a
        letter was skipped, never the letter. That is a deliberate property
        of the journal (it would otherwise hold passcodes and sign-in
        links), not an oversight, and the only honest repair for that
        backlog is for the service that owns the source event to re-emit it.
        """
        from stapel_notifications import contact_gap

        expired = 0 if dry_run else contact_gap.expire_parked()
        rows = list(contact_gap.replayable())

        outcomes = {"resent": 0, "still_unreachable": 0, "failed": 0, "gone": 0}
        for parked in rows:
            outcome = (
                self._would_replay(parked) if dry_run
                else contact_gap.replay(parked)
            )
            outcomes[outcome] = outcomes.get(outcome, 0) + 1

        self.stdout.write("resend of parked transactional notifications:")
        self.stdout.write(f"  parked in window       : {len(rows)}")
        self.stdout.write(f"  resent                 : {outcomes['resent']}")
        self.stdout.write(
            f"  still unreachable      : {outcomes['still_unreachable']}"
        )
        self.stdout.write(
            f"  provider refused       : {outcomes['failed']}"
        )
        self.stdout.write(f"  gone (nothing to do)   : {outcomes['gone']}")
        self.stdout.write(f"  expired past window    : {expired}")
        if dry_run:
            self.stdout.write("  (dry run — nothing was sent)")

    def _would_replay(self, parked) -> str:
        from stapel_notifications.models import NotificationLog, UserContact

        log = NotificationLog.objects.filter(pk=parked.log_id).first()
        if log is None or log.status != "skipped":
            return "gone"
        contact = UserContact.objects.filter(
            user_id=parked.user_id, is_active=True
        ).first()
        address = getattr(contact, parked.channel, "") if contact else ""
        return "resent" if address else "still_unreachable"
