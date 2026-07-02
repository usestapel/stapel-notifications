"""
Notification type → channel routing.

The built-in catalog below covers the framework's own notifications; a host
project extends or overrides it WITHOUT forking via the settings namespace::

    STAPEL_NOTIFICATIONS = {
        "TYPES": {
            "invoice_ready": {
                "channels": ["email", "push"],
                "group": "system",
                "template": "email/invoice_ready.html",
            },
            # override a built-in:
            "new_message": {"channels": ["push"], "group": "messages"},
        },
    }

Groups:
    auth     — mandatory security notifications (no unsubscribe)
    messages — user-to-user messages (can disable per channel)
    system   — platform notifications (can disable per channel)
"""
from .conf import notifications_settings

NOTIFICATION_ROUTING = {
    # Group A: Auth/Security (mandatory, no unsubscribe)
    "otp_code":              {"channels": ["email", "sms"],          "group": "auth"},
    "auth_change_requested": {"channels": ["email", "sms", "push"], "group": "auth"},
    "auth_change_reminder":  {"channels": ["email", "sms", "push"], "group": "auth"},
    "auth_change_urgent":    {"channels": ["email", "sms", "push"], "group": "auth"},
    "auth_change_completed": {"channels": ["email", "sms", "push"], "group": "auth"},
    "magic_link_login":      {"channels": ["email"],                 "group": "auth"},
    "new_device_login":      {"channels": ["email"],                 "group": "auth"},
    "suspicious_login":      {"channels": ["email"],                 "group": "auth"},
    "all_sessions_revoked":  {"channels": ["email"],                 "group": "auth"},

    # Group B: Messages (user can disable per channel)
    "new_message":           {"channels": ["push", "email"],        "group": "messages"},

    # Group C: System (user can disable per channel)
    "report_reviewed":       {"channels": ["push", "email"],        "group": "system"},
    "listing_expiring":      {"channels": ["push", "email"],        "group": "system"},
    "listing_blocked":       {"channels": ["push", "email"],        "group": "system"},
    "workspace.invitation": {
        "channels": ["email"],
        "group": "system",
        "template": "email/workspace_invitation.html",
    },
}

# Built-in email templates for types that do not carry their own
# "template" key (kept separate for backward compatibility).
DEFAULT_EMAIL_TEMPLATES = {
    "otp_code": "email/otp_code.html",
    "auth_change_requested": "email/auth_change.html",
    "auth_change_reminder": "email/auth_change.html",
    "auth_change_urgent": "email/auth_change.html",
    "auth_change_completed": "email/auth_change.html",
    "new_message": "email/new_message.html",
    "report_reviewed": "email/report_reviewed.html",
    "listing_expiring": "email/listing_expiring.html",
    "listing_blocked": "email/listing_blocked.html",
    "magic_link_login": "email/magic_link_login.html",
    "new_device_login": "email/new_device_login.html",
    "suspicious_login": "email/suspicious_login.html",
    "all_sessions_revoked": "email/all_sessions_revoked.html",
}


def get_routing(notification_type: str) -> dict | None:
    """Effective routing entry: settings TYPES override the built-ins."""
    overrides = notifications_settings.TYPES or {}
    if notification_type in overrides:
        return overrides[notification_type]
    return NOTIFICATION_ROUTING.get(notification_type)


def registered_types() -> list[str]:
    return sorted({**NOTIFICATION_ROUTING, **(notifications_settings.TYPES or {})})


def get_channels(notification_type: str) -> list[str]:
    """Get channels for a notification type."""
    routing = get_routing(notification_type)
    if not routing:
        return []
    return routing.get("channels", [])


def get_group(notification_type: str) -> str:
    """Get group for a notification type."""
    routing = get_routing(notification_type)
    if not routing:
        return ""
    return routing.get("group", "")


def get_email_template(notification_type: str) -> str | None:
    """Template for a type: per-type "template" key wins, then built-ins."""
    routing = get_routing(notification_type) or {}
    return routing.get("template") or DEFAULT_EMAIL_TEMPLATES.get(notification_type)
