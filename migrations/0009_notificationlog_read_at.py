"""Read state for the feed: NotificationLog.read_at.

The feed had no read state at all, so no client could render an unread badge
or dim a row it had already shown — a notification bell that cannot tell
read from unread is a bell that either shouts forever or says nothing.

Expand-only: a nullable column (NULL = unread, the state every row is born
in) plus a partial index over the unread rows, which is the set the
per-page unread count walks.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0008_usernotificationsettings_channel_preferences'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationlog',
            name='read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='notificationlog',
            index=models.Index(
                condition=models.Q(('read_at__isnull', True)),
                fields=['user_id'],
                name='notif_user_unread_idx',
            ),
        ),
    ]
