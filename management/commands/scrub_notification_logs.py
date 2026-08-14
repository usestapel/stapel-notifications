"""Rewrite historical delivery rows through the telemetry rules.

Rows written before the journal filtered itself (stapel-notifications
< 0.11) hold whatever the caller passed: one-time passcodes, sign-in and
invitation links with their tokens, the initial password of a provisioned
account. New rows cannot (``NotificationLog.save`` → ``telemetry.scrub_data``);
the old ones sit there until somebody rewrites them, and nothing in a
migration should silently edit an audit table.

    manage.py scrub_notification_logs                 # dry run — counts only
    manage.py scrub_notification_logs --commit        # rewrite in place
    manage.py scrub_notification_logs --commit --older-than-days 90
    manage.py scrub_notification_logs --commit --delete-older-than-days 365

``--commit`` rewrites ``data``/``title``/``body`` through exactly the rules a
new row goes through — nothing else about the row changes, so the delivery
audit trail (who was written to, when, on which channel, with what outcome)
survives intact.

``--delete-older-than-days`` is the shredding option for a deployment whose
incident or retention policy says the rows themselves must go. It deletes
whole rows; database backups holding the same values are outside anything
this command can reach, and belong in the incident plan rather than here.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from stapel_notifications.models import NotificationLog
from stapel_notifications.telemetry import redact_text, scrub_data

BATCH = 500


class Command(BaseCommand):
    help = (
        "Rewrite NotificationLog.data/title/body of historical rows through "
        "the deny-by-default telemetry rules (dry run unless --commit)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually write. Without it the command only counts.",
        )
        parser.add_argument(
            "--older-than-days", type=int, default=None,
            help="Only consider rows created more than N days ago.",
        )
        parser.add_argument(
            "--delete-older-than-days", type=int, default=None,
            help="Delete rows created more than N days ago instead of rewriting them.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        queryset = NotificationLog.objects.all().order_by("created_at")

        delete_days = options["delete_older_than_days"]
        if delete_days is not None:
            cutoff = timezone.now() - timezone.timedelta(days=delete_days)
            doomed = queryset.filter(created_at__lt=cutoff)
            count = doomed.count()
            if commit:
                doomed.delete()
                self.stdout.write(self.style.SUCCESS(f"deleted {count} row(s) older than {delete_days}d"))
            else:
                self.stdout.write(f"would delete {count} row(s) older than {delete_days}d")
            return

        days = options["older_than_days"]
        if days is not None:
            queryset = queryset.filter(created_at__lt=timezone.now() - timezone.timedelta(days=days))

        scanned = changed = 0
        batch: list[NotificationLog] = []
        for row in queryset.iterator(chunk_size=BATCH):
            scanned += 1
            data = scrub_data(row.notification_type, row.data)
            title = redact_text(row.title)
            body = redact_text(row.body)
            if data == row.data and title == row.title and body == row.body:
                continue
            changed += 1
            row.data, row.title, row.body = data, title, body
            batch.append(row)
            if commit and len(batch) >= BATCH:
                self._flush(batch)
        if commit and batch:
            self._flush(batch)

        verb = "rewrote" if commit else "would rewrite"
        style = self.style.SUCCESS if commit else self.style.WARNING
        self.stdout.write(style(
            f"scrub_notification_logs: {verb} {changed} of {scanned} row(s)"
            + ("" if commit else " — re-run with --commit to write")
        ))

    def _flush(self, batch):
        # bulk_update, not save(): the values are already scrubbed, and a
        # second pass through save() would be the same work again.
        NotificationLog.objects.bulk_update(batch, ["data", "title", "body"])
        batch.clear()
