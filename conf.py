"""Settings namespace for stapel-notifications.

Everything a host project previously had to fork is an override here::

    STAPEL_NOTIFICATIONS = {
        # add/override notification types without touching routing.py
        "TYPES": {
            "invoice_ready": {
                "channels": ["email", "push"],
                "group": "system",
                "template": "myapp/email/invoice_ready.html",
            },
        },
        # map/override email templates per type (merged over the built-ins)
        "EMAIL_TEMPLATES": {
            "new_message": "myapp/email/new_message.html",
        },
        # channel backends: built-in short name or any dotted path
        "EMAIL_PROVIDER": "myproject.email.SendgridProvider",
        "SMS_PROVIDER": "twilio",
        "PUSH_PROVIDER": "fcm",
    }

Resolution per key: STAPEL_NOTIFICATIONS dict → flat Django setting of the
same name (legacy: EMAIL_PROVIDER, TWILIO_* keep working) → env → default.
"""
from stapel_core.conf import AppSettings

notifications_settings = AppSettings(
    "STAPEL_NOTIFICATIONS",
    defaults={
        # Notification-type registry, merged OVER routing.NOTIFICATION_ROUTING.
        # {"<type>": {"channels": [...], "group": "auth|messages|system",
        #             "template": "myapp/email/x.html"}}
        "TYPES": {},
        # Per-type email template overrides, merged over
        # routing.DEFAULT_EMAIL_TEMPLATES.
        "EMAIL_TEMPLATES": {},
        # Channel backends: short registry name or dotted path to a provider
        # class with .send(...)
        "EMAIL_PROVIDER": "mock",
        "SMS_PROVIDER": "mock",
        "PUSH_PROVIDER": "fcm",
        # Provider credentials (read lazily, never frozen at import)
        "RESEND_API_KEY": "",
        "MAILGUN_API_KEY": "",
        "MAILGUN_DOMAIN": "",
        "GATEWAYAPI_TOKEN": "",
        "GATEWAYAPI_SENDER": "Stapel",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_PHONE_NUMBER": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
        # Template variables
        "COMPANY_NAME": "Stapel",
        "COMPANY_URL": "",
        "COMPANY_ADDRESS": "",
        "COMPANY_YEAR": "",
        "FRONTEND_URL": "",
    },
)

__all__ = ["notifications_settings"]
