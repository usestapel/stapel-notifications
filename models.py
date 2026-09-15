"""Models for stapel-notifications service."""

import uuid

from django.db import models

# The light declaration module, not the package root — models load during
# app population (see stapel_core.django.outbox.models for the precedent).
from stapel_core.access.declaration import access


class UserNotificationSettings(models.Model):
    """Per-user channel preferences, synced from profiles.

    Holds no language. It used to mirror ``app_language`` /
    ``auto_detected_language`` here, and the mirror never filled once in its
    lifetime — so every recipient was silently written to in the SENDER's
    language. The language is now ASKED of the module that owns it, at send
    time, through the ``profiles.language`` comm Function (see language.py):
    a call either answers or raises, where a mirror answers ``None`` both
    for "chose nothing" and for "the sync never ran".
    """

    user_id = models.UUIDField(primary_key=True)
    email_messages = models.BooleanField(default=True)
    email_system = models.BooleanField(default=True)
    push_messages = models.BooleanField(default=True)
    push_system = models.BooleanField(default=True)
    sms_messages = models.BooleanField(default=True)
    sms_system = models.BooleanField(default=True)
    telegram_messages = models.BooleanField(default=True)
    telegram_system = models.BooleanField(default=True)
    #: Switches for channels this library does not ship. The concrete
    #: columns above are a closed set — they are columns — so a host that
    #: registers a channel through STAPEL_NOTIFICATIONS["CHANNELS"] has
    #: nowhere to put "this recipient turned the webhook off". Keys are the
    #: same "<channel>_<group>" pairs, values booleans; a missing key means
    #: the built-ins' own default, opted in. Read by services._should_send,
    #: which refuses to send on a preference it cannot read.
    channel_preferences = models.JSONField(default=dict, blank=True)
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
    telegram_chat_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Telegram chat id this user can be written to — the address of "
            "the telegram channel, the exact counterpart of email/phone. A "
            "numeric id (the value a bot reads off an incoming update), not "
            "an @username: a username can be changed by its owner and a bot "
            "cannot open a conversation from one."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Soft-deactivated during an account-closure grace period "
            "(user.deletion_initiated); reactivated by the next contact sync. "
            "Inactive contacts are not used as notification recipients."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Contact"
        verbose_name_plural = "User Contacts"

    def __str__(self):
        return f"Contact({self.user_id})"


@access.ops  # pure sync cache: populated only by translate.resolve / sync_translations, never staff-authored (AS-5)
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


@access.ops  # delivery/audit journal: written by services.py, read-only in admin (AS-5)
class NotificationLog(models.Model):
    """Tracks every notification sent/attempted. Also serves as feed for push."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user_id = models.UUIDField(null=True, db_index=True)
    notification_type = models.CharField(max_length=50, db_index=True)
    channel = models.CharField(max_length=10)  # email | push | sms | telegram
    status = models.CharField(max_length=10)  # sent | failed | skipped
    language = models.CharField(max_length=5, default="en")
    recipient = models.CharField(max_length=255)
    # Feed-displayable fields (stored at send time for push notifications)
    title = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    data = models.JSONField(
        default=dict,
        help_text=(
            "Declared telemetry only — deep link, notification_type, "
            "language_source, event_id. Deny-by-default: see telemetry.py."
        ),
    )
    error_message = models.TextField(blank=True, default="")
    device_count = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "How many devices a push actually reached. NULL on every other "
            "channel, and on push rows written before 0.22.0. 0 means the row "
            "is in the in-app feed and NO handset was reached — a state "
            "status='sent' cannot express and used to hide. Split delivery "
            "dashboards on this: status='sent' answers 'is it in the feed', "
            "device_count > 0 answers 'did it leave the building'."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the recipient marked this feed row read (POST feed/read/). "
            "NULL means unread, and unread is the only state a feed row is "
            "born in — the column is about the RECIPIENT's attention, not "
            "about delivery, which `status` already records. Only push rows "
            "are ever read: those are the ones GET feed/ returns."
        ),
    )

    def save(self, *args, **kwargs):
        """The journal filters itself, at the table's own boundary.

        Putting the rule here rather than only at the call site in
        ``services`` is what makes it a property of the TABLE: a host
        writing its own delivery row, a future channel, and a data migration
        all get the same guarantee, and no future call site can opt out of
        it by forgetting. ``title``/``body`` are rendered copy a human reads
        back in the feed, so they are only stripped of credential carriers,
        not filtered by key.
        """
        from .telemetry import redact_text, scrub_data

        self.data = scrub_data(self.notification_type, self.data)
        self.title = redact_text(self.title)
        self.body = redact_text(self.body)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Notification Log"
        verbose_name_plural = "Notification Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user_id", "-created_at"],
                name="notif_user_created_idx",
            ),
            # The unread count is read on EVERY feed page (it is part of the
            # envelope), so it must not be a scan of the recipient's whole
            # journal. Partial on the unread rows: the set that shrinks as a
            # person reads, which is the set the count walks.
            models.Index(
                fields=["user_id"],
                condition=models.Q(read_at__isnull=True),
                name="notif_user_unread_idx",
            ),
        ]

    def __str__(self):
        return f"{self.notification_type}/{self.channel} → {self.recipient} ({self.status})"


@access.ops  # delivery ledger: written by services.py, read-only in admin (AS-5)
class NotificationDelivery(models.Model):
    """One row per delivery this deployment has claimed or completed.

    The idempotency key, moved out of a check-then-act on the journal.
    ``process_notification`` used to ask ``NotificationLog.objects.filter(
    data__event_id=..., status="sent").exists()`` before doing anything,
    which is wrong twice:

    * **Not atomic.** Two consumers handed the same event by an at-least-once
      broker both read "no row yet" and both send. The window is the whole
      render+SMTP round trip, which is exactly when a redelivery arrives.
    * **Too coarse.** One ``sent`` row suppressed the WHOLE event. An OTP
      that reached the recipient's email but had no phone number to reach on
      SMS could never be retried on SMS: the email row answered for both.

    A claim is per ``(event_id, channel, recipient, template_version)`` and
    the uniqueness is the database's, not a Python ``if``. ``template_version``
    is this library's version of a letter — the effective template path, or
    ``"raw"`` for the raw-content escape hatch — so re-pointing a type at a
    new template does not have its delivery suppressed by a claim taken for
    the old one.

    ``state`` separates "somebody is sending this right now" from "this was
    delivered". A claim is released on failure so a retry can take it again;
    a claim whose process died is taken over after
    ``STAPEL_NOTIFICATIONS["DELIVERY_CLAIM_TTL"]`` seconds, because a crash
    between claim and send must not silence a notification forever.
    """

    CLAIMED = "claimed"
    DELIVERED = "delivered"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    event_id = models.CharField(max_length=255, db_index=True)
    channel = models.CharField(max_length=10)
    recipient = models.CharField(max_length=255)
    template_version = models.CharField(max_length=255, blank=True, default="")
    state = models.CharField(max_length=10, default=CLAIMED)  # claimed | delivered
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notification Delivery"
        verbose_name_plural = "Notification Deliveries"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event_id", "channel", "recipient", "template_version"],
                name="notif_delivery_claim_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.event_id}/{self.channel} → {self.recipient} ({self.state})"


@access.secret  # bearer push token carrier; `token` field auto-masked by StapelModelAdmin (AS-5)
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

    @property
    def token_fingerprint(self) -> str:
        """SHA-256 of the token, hex — the device's identity without the key.

        A caller listing its devices has one question the row's id cannot
        answer: *is this device — the one I am running on right now —
        registered?* The only value both sides hold is the token itself, and
        the token is a bearer credential: whoever has it can address this
        device's push channel, so it is never echoed back out of this service
        (the register/unregister responses carry it only because the caller
        just sent it).

        A digest answers the question and grants nothing: the client hashes
        the token it already has and matches. It is stable, it is not
        reversible, and it is only ever visible to the account the device is
        registered to.
        """
        import hashlib

        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()

    class Meta:
        verbose_name = "Device Push Token"
        verbose_name_plural = "Device Push Tokens"

    def __str__(self):
        return f"{self.platform}:{self.token[:20]}... (user={self.user_id})"
