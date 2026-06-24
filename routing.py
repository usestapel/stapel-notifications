"""
Notification type → channel routing configuration.

Groups:
    auth     — mandatory security notifications (no unsubscribe)
    messages — user-to-user messages (can disable per channel)
    system   — platform notifications (can disable per channel)
"""

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
}


def get_channels(notification_type: str) -> list[str]:
    """Get channels for a notification type."""
    routing = NOTIFICATION_ROUTING.get(notification_type)
    if not routing:
        return []
    return routing["channels"]


def get_group(notification_type: str) -> str:
    """Get group for a notification type."""
    routing = NOTIFICATION_ROUTING.get(notification_type)
    if not routing:
        return ""
    return routing["group"]
