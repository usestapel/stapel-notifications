"""The delivery claim table, and the journal's narrowed `data` contract.

Expand-only: a new table plus a help_text change. Nothing is backfilled from
the existing ``NotificationLog.data->>'event_id'`` rows, so the first
redelivery of an event that was already delivered BEFORE this migration can
send once more; the window is however long the broker retains the event, and
paying it once is the price of moving idempotency off a check-then-act on a
journal (see models.NotificationDelivery).

Historical journal rows are NOT rewritten here — a migration that silently
edits the contents of an audit table is the wrong place for it, and the rows
may be large. ``manage.py scrub_notification_logs`` does that on the
operator's word, with a dry run first.
"""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0005_drop_language_mirror"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationlog",
            name="data",
            field=models.JSONField(
                default=dict,
                help_text=(
                    "Declared telemetry only — deep link, notification_type, "
                    "language_source, event_id. Deny-by-default: see telemetry.py."
                ),
            ),
        ),
        migrations.CreateModel(
            name="NotificationDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ("event_id", models.CharField(db_index=True, max_length=255)),
                ("channel", models.CharField(max_length=10)),
                ("recipient", models.CharField(max_length=255)),
                ("template_version", models.CharField(blank=True, default="", max_length=255)),
                ("state", models.CharField(default="claimed", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Notification Delivery",
                "verbose_name_plural": "Notification Deliveries",
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("event_id", "channel", "recipient", "template_version"),
                        name="notif_delivery_claim_uniq",
                    )
                ],
            },
        ),
    ]
