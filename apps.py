from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stapel_notifications"
    label = 'notifications'
    verbose_name = "Iron Notifications"

    def ready(self):
        from stapel_core.gdpr import gdpr_registry
        from .gdpr import NotificationsGDPRProvider
        gdpr_registry.register(NotificationsGDPRProvider())
