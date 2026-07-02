import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="UserNotificationSettings",
            fields=[
                ("user_id", models.UUIDField(primary_key=True, serialize=False)),
                ("language", models.CharField(blank=True, help_text="Profile app_language override (null = auto-detect)", max_length=10, null=True)),
                ("email_messages", models.BooleanField(default=True)),
                ("email_system", models.BooleanField(default=True)),
                ("push_messages", models.BooleanField(default=True)),
                ("push_system", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "User Notification Settings",
                "verbose_name_plural": "User Notification Settings",
            },
        ),
        migrations.CreateModel(
            name="UserContact",
            fields=[
                ("user_id", models.UUIDField(primary_key=True, serialize=False)),
                ("email", models.CharField(blank=True, default="", max_length=255)),
                ("phone", models.CharField(blank=True, default="", max_length=20)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "User Contact",
                "verbose_name_plural": "User Contacts",
            },
        ),
        migrations.CreateModel(
            name="TranslationCache",
            fields=[
                ("key", models.CharField(help_text="e.g. notification.otp_code.heading", max_length=255, primary_key=True, serialize=False)),
                ("values", models.JSONField(default=dict, help_text='{"en": "Your verification code", "de": "..."}')),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Translation Cache",
                "verbose_name_plural": "Translation Cache",
            },
        ),
        migrations.CreateModel(
            name="NotificationLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ("user_id", models.UUIDField(db_index=True, null=True)),
                ("notification_type", models.CharField(db_index=True, max_length=50)),
                ("channel", models.CharField(max_length=10)),
                ("status", models.CharField(max_length=10)),
                ("language", models.CharField(default="en", max_length=5)),
                ("recipient", models.CharField(max_length=255)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("body", models.TextField(blank=True, default="")),
                ("data", models.JSONField(default=dict, help_text="Deep link URL, notification_type, etc.")),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Notification Log",
                "verbose_name_plural": "Notification Logs",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["user_id", "-created_at"], name="notif_user_created_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DevicePushToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_id", models.UUIDField(db_index=True)),
                ("token", models.CharField(max_length=500, unique=True)),
                ("platform", models.CharField(max_length=10)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Device Push Token",
                "verbose_name_plural": "Device Push Tokens",
            },
        ),
    ]
