"""Switches for channels this library does not ship.

The concrete <channel>_<group> boolean columns are a closed set — they are
columns — so a host that registers a channel through
STAPEL_NOTIFICATIONS["CHANNELS"] had nowhere to record "this recipient
turned the webhook off", and services._should_send refuses to send on a
preference it cannot read. Expand-only: additive, defaulted, nullable-free.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0007_telegram_channel'),
    ]

    operations = [
        migrations.AddField(
            model_name='usernotificationsettings',
            name='channel_preferences',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
