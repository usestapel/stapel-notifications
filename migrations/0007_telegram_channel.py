"""The telegram channel: its recipient address and its two preferences.

Expand-only — three additive columns, each with a default, so an older
process keeps running against the migrated schema.

``telegram_messages`` / ``telegram_system`` default to True, exactly like
the sms pair they mirror: the preference says "the recipient has not
switched this off", not "this deployment sends it". Nothing is routed to
telegram out of the box and TELEGRAM_PROVIDER is "unconfigured", so a
default of True cannot make an existing deployment start writing to anyone.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0006_delivery_claim"),
    ]

    operations = [
        migrations.AddField(
            model_name="usercontact",
            name="telegram_chat_id",
            field=models.CharField(
                blank=True,
                default="",
                max_length=64,
                help_text=(
                    "Telegram chat id this user can be written to — the address of "
                    "the telegram channel, the exact counterpart of email/phone. A "
                    "numeric id (the value a bot reads off an incoming update), not "
                    "an @username: a username can be changed by its owner and a bot "
                    "cannot open a conversation from one."
                ),
            ),
        ),
        migrations.AddField(
            model_name="usernotificationsettings",
            name="telegram_messages",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="usernotificationsettings",
            name="telegram_system",
            field=models.BooleanField(default=True),
        ),
    ]
