"""A push row can finally say how many handsets it reached.

Expand-only and nullable: every existing row keeps meaning exactly what it
meant, and NULL is an honest "this predates the column" rather than a
back-filled 0 that would claim we know a push reached nobody when we do not.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0010_alter_notificationlog_read_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationlog",
            name="device_count",
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text=(
                    "How many devices a push actually reached. NULL on every "
                    "other channel, and on push rows written before 0.22.0. 0 "
                    "means the row is in the in-app feed and NO handset was "
                    "reached — a state status='sent' cannot express and used "
                    "to hide. Split delivery dashboards on this: "
                    "status='sent' answers 'is it in the feed', device_count "
                    "> 0 answers 'did it leave the building'."
                ),
            ),
        ),
    ]
