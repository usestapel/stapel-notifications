"""Models for stapel-notifications service."""

import uuid

from django.db import models


class UserNotificationSettings(models.Model):
    """Notification preferences synced from profiles via Kafka."""

    user_id = models.UUIDField(primary_key=True)
    language = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        help_text="Profile app_language override (null = auto-detect)",
    )
    auto_detected_language = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="Last detected language from Accept-Language header",
    )
    email_messages = models.BooleanField(default=True)
    email_system = models.BooleanField(default=True)
    push_messages = models.BooleanField(default=True)
    push_system = models.BooleanField(default=True)
    sms_messages = models.BooleanField(default=True)
    sms_system = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Notification Settings"
        verbose_name_plural = "User Notification Settings"

    def __str__(self):
        return f"Settings({self.user_id})"


class UserContact(models.Model):
    """User contact info synced from auth via Kafka."""

    user_id = models.UUIDField(primary_key=True)
    email = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Contact"
        verbose_name_plural = "User Contacts"

    def __str__(self):
        return f"Contact({self.user_id})"


class TranslationCache(models.Model):
    """Notification translation keys synced from translate via Kafka."""

    key = models.CharField(
        max_length=255, primary_key=True, help_text="e.g. notification.otp_code.heading"
    )
    values = models.JSONField(
        default=dict, help_text='{"en": "Your verification code", "de": "..."}'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Translation Cache"
        verbose_name_plural = "Translation Cache"

    def __str__(self):
        return self.key


class NotificationLog(models.Model):
    """Tracks every notification sent/attempted. Also serves as feed for push."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user_id = models.UUIDField(null=True, db_index=True)
    notification_type = models.CharField(max_length=50, db_index=True)
    channel = models.CharField(max_length=10)  # email | push | sms
    status = models.CharField(max_length=10)  # sent | failed | skipped
    language = models.CharField(max_length=5, default="en")
    recipient = models.CharField(max_length=255)
    # Feed-displayable fields (stored at send time for push notifications)
    title = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    data = models.JSONField(
        default=dict, help_text="Deep link URL, notification_type, etc."
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notification Log"
        verbose_name_plural = "Notification Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user_id", "-created_at"],
                name="notificatio_user_id_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.notification_type}/{self.channel} → {self.recipient} ({self.status})"


class DevicePushToken(models.Model):
    """FCM tokens for push notifications (iOS, Android, Web)."""

    user_id = models.UUIDField(db_index=True)
    token = models.CharField(max_length=500, unique=True)
    PLATFORM_CHOICES = [
        ("ios", "iOS"),
        ("android", "Android"),
        ("web", "Web"),
    ]
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Device Push Token"
        verbose_name_plural = "Device Push Tokens"

    def __str__(self):
        return f"{self.platform}:{self.token[:20]}... (user={self.user_id})"
