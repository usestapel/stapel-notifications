"""Admin for stapel-notifications — a delivery log is full of personal data.

WHAT THIS MODULE GOT WRONG UNTIL 0.23.0. Every admin here declared
`list_display` and `search_fields` and neither `fields` nor `exclude`, so the
detail page rendered every column — and in this app the columns ARE the
personal data: who was written to (`recipient`, `email`, `phone`,
`telegram_chat_id`) and what was said to them (`title`, `body`, `data`).

A fleet audited on 2026-09-16 wanted to give its operators a read of the
delivery log — "did the receipt go out, did it bounce" — without handing them
every customer's address and the text of every letter. It could not: the
permission is called `view_notificationlog` and there was no way for a fixture
to mean less than "all of it". So the honest answer was to grant nothing, and
the operators got nothing.

``PERSONAL_FIELDS`` names those columns per admin. They are excluded from the
detail view by default, and kept out of `list_display` and `search_fields`,
because printing an address in a results table is the same disclosure as
printing it on a page, and searching BY one is worse: it confirms a guess.
Delivery STATE — channel, state, template version, error, timestamps — stays,
because that is the question the table exists to answer.

A host that has decided its staff may see addresses subclasses and narrows the
tuple. That is a reviewable line in that deployment, rather than a silent
consequence of a permission name.
"""
from django.contrib import admin

from stapel_core.django.admin.base import StapelModelAdmin

from .models import (
    UserNotificationSettings,
    UserContact,
    TranslationCache,
    NotificationDelivery,
    NotificationLog,
    DevicePushToken,
)


class _PersonalDataAdmin(admin.ModelAdmin):
    """Hides PERSONAL_FIELDS on the detail page. See the module docstring."""

    PERSONAL_FIELDS: tuple[str, ...] = ()

    def get_exclude(self, request, obj=None):
        return tuple(self.PERSONAL_FIELDS) or None


class _PersonalDataStapelAdmin(StapelModelAdmin):
    """Same, for the admins that carry the @access declaration."""

    PERSONAL_FIELDS: tuple[str, ...] = ()

    def get_exclude(self, request, obj=None):
        return tuple(self.PERSONAL_FIELDS) or None



@admin.register(UserNotificationSettings)
class UserNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ['user_id', 'email_messages', 'email_system', 'push_messages', 'push_system', 'updated_at']
    search_fields = ['user_id']
    readonly_fields = ['updated_at']


@admin.register(UserContact)
class UserContactAdmin(_PersonalDataAdmin):
    # This table is nothing BUT addresses. What is left once they are hidden
    # is "does this account have a contact route at all, and is it active",
    # which is the operational question.
    PERSONAL_FIELDS = ('email', 'phone', 'telegram_chat_id')
    list_display = ['user_id', 'is_active', 'updated_at']
    search_fields = ['user_id']
    readonly_fields = ['updated_at']


@admin.register(TranslationCache)
class TranslationCacheAdmin(StapelModelAdmin):
    list_display = ['key', 'updated_at']
    search_fields = ['key']
    readonly_fields = ['updated_at']


@admin.register(NotificationLog)
class NotificationLogAdmin(_PersonalDataStapelAdmin):
    # `recipient` is the address; `title`/`body` are the letter; `data` is the
    # payload it was rendered from and has carried names and amounts.
    PERSONAL_FIELDS = ('recipient', 'title', 'body', 'data')
    list_display = ['id', 'notification_type', 'channel', 'status', 'language', 'created_at']
    list_filter = ['status', 'channel', 'notification_type']
    # user_id, not recipient: the same row, reachable by the identifier an
    # operator already has, without confirming an address they only guessed.
    search_fields = ['user_id', 'notification_type']
    readonly_fields = ['id', 'created_at']
    ordering = ['-created_at']


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(_PersonalDataStapelAdmin):
    """Why a redelivery was suppressed — the question this table answers.

    It answers it from the event and the state, so the address is not needed
    to read it.
    """

    PERSONAL_FIELDS = ('recipient',)
    list_display = ['event_id', 'channel', 'state', 'template_version', 'created_at']
    list_filter = ['state', 'channel']
    search_fields = ['event_id']
    readonly_fields = ['id', 'created_at']
    ordering = ['-created_at']


@admin.register(DevicePushToken)
class DevicePushTokenAdmin(_PersonalDataStapelAdmin):
    # A push token is a credential for reaching a person's device.
    PERSONAL_FIELDS = ('token',)
    list_display = ['user_id', 'platform', 'is_active', 'created_at', 'updated_at']
    list_filter = ['platform', 'is_active']
    search_fields = ['user_id']
    readonly_fields = ['created_at', 'updated_at']
