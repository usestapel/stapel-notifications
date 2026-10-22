from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stapel_notifications"
    label = 'notifications'
    verbose_name = "Stapel Notifications"

    def ready(self):
        from stapel_core.gdpr import gdpr_registry
        from .gdpr import NotificationsGDPRProvider
        gdpr_registry.register(NotificationsGDPRProvider())

        # Action subscriptions (in-process in a monolith, bus consumer in
        # microservices — same code, transport chosen by STAPEL_COMM).
        from . import actions  # noqa: F401

        # request_notification() is a bus publish. On an in-process bus no
        # consumer process can exist, so the handler subscribes in-process
        # here (inert on a broker, where consume_notifications reads it).
        from .intake import ensure_in_process_consumer

        try:
            ensure_in_process_consumer()
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "notifications: could not subscribe the in-process consumer"
            )

        # System checks (notifications.W001/W002: a deployment that cannot
        # ask a recipient's language writes to everybody in the sender's).
        from . import checks  # noqa: F401

        # The erasure protocol, implemented once in stapel-core:
        # gdpr.erasure.requested -> erase -> gdpr.section.erased with a
        # deterministic receipt inside the erase's transaction, the
        # gdpr.owner.probe answer from the same subscriber, and the
        # deprecated user.deleted account path. No protocol code here.
        from stapel_core.gdpr import register_gdpr_owner

        from .erasure import GDPR_OWNER, GDPR_SUBJECT_TYPES, erase_subject

        register_gdpr_owner(GDPR_OWNER, GDPR_SUBJECT_TYPES, erase_subject)
