from django.contrib import admin
from .models import (
    UserNotificationSettings,
    UserContact,
    TranslationCache,
    NotificationLog,
    DevicePushToken,
)


@admin.register(UserNotificationSettings)
class UserNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ['user_id', 'language', 'email_messages', 'email_system', 'push_messages', 'push_system', 'updated_at']
    search_fields = ['user_id']
    readonly_fields = ['updated_at']


@admin.register(UserContact)
class UserContactAdmin(admin.ModelAdmin):
    list_display = ['user_id', 'email', 'phone', 'updated_at']
    search_fields = ['user_id', 'email', 'phone']
    readonly_fields = ['updated_at']


@admin.register(TranslationCache)
class TranslationCacheAdmin(admin.ModelAdmin):
    list_display = ['key', 'updated_at']
    search_fields = ['key']
    readonly_fields = ['updated_at']


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'notification_type', 'channel', 'status', 'recipient', 'language', 'created_at']
    list_filter = ['status', 'channel', 'notification_type']
    search_fields = ['user_id', 'recipient', 'notification_type']
    readonly_fields = ['id', 'created_at']
    ordering = ['-created_at']


@admin.register(DevicePushToken)
class DevicePushTokenAdmin(admin.ModelAdmin):
    list_display = ['user_id', 'platform', 'is_active', 'created_at', 'updated_at']
    list_filter = ['platform', 'is_active']
    search_fields = ['user_id', 'token']
    readonly_fields = ['created_at', 'updated_at']
