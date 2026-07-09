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

#: AppSettings-shaped literal dict (capability-config.md §2): a top-level
#: DEFAULTS lets the capabilities.json emitter introspect axis keys/kinds
#: without re-parsing the AppSettings() call.
DEFAULTS = {
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
    # Branding: logo + colors, threaded into every email template via
    # the base layout (templates/notifications/email/_base.html).
    # LOGO_URL set   → templates embed <img src="LOGO_URL"> and the
    #                  inline CID attachment is skipped.
    # LOGO_URL empty → current behavior: the packaged static logo is
    #                  attached inline and referenced as cid:logo.
    "LOGO_URL": "",
    "BRAND_PRIMARY": "#00AEEF",        # logo/accent color
    "BRAND_PRIMARY_DARK": "#2A90D9",   # buttons + links
    "BRAND_BG": "#F5F5F6",             # page background
    "BRAND_TEXT": "#1C1D20",           # headings + body copy
    # Languages to prefetch with `manage.py sync_translations` (the
    # lazy resolve-on-miss path covers anything not listed here).
    "LANGUAGES": ["en"],
}

notifications_settings = AppSettings(
    "STAPEL_NOTIFICATIONS",
    defaults=DEFAULTS,
)

__all__ = ["notifications_settings"]
