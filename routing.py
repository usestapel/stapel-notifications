"""
Notification type → channel routing.

The built-in catalog below covers the framework's own notifications; a host
project extends or overrides it WITHOUT forking via the settings namespace::

    STAPEL_NOTIFICATIONS = {
        "TYPES": {
            "invoice_ready": {
                "channels": ["email", "push"],
                "group": "system",
                "template": "myapp/email/invoice_ready.html",
            },
            # override a built-in:
            "new_message": {"channels": ["push"], "group": "messages"},
        },
        # or map/override templates without touching the routing entry:
        "EMAIL_TEMPLATES": {
            "invoice_ready": "myapp/email/invoice_ready.html",
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

    # Account lifecycle / GDPR (mandatory, no unsubscribe)
    "gdpr.export_ready":       {"channels": ["email"], "group": "auth"},
    "gdpr.inactivity_warning": {"channels": ["email"], "group": "auth"},
    "gdpr.inactivity_closed":  {"channels": ["email"], "group": "auth"},

    # Group B: Messages (user can disable per channel)
    "new_message":           {"channels": ["push", "email"],        "group": "messages"},

    # Group C: System (user can disable per channel)
    "report_reviewed":       {"channels": ["push", "email"],        "group": "system"},
    "listing_expiring":      {"channels": ["push", "email"],        "group": "system"},
    "listing_blocked":       {"channels": ["push", "email"],        "group": "system"},
    "workspace.invitation":  {"channels": ["email"],                "group": "system"},
    # Invite variant for a not-yet-registered recipient: the acceptance link
    # both creates the account and joins the workspace. Kept as its own type
    # (not an override of "workspace.invitation") so a host project can route
    # or template it independently — a clean routing-override seam.
    "workspace.invitation.new_user": {"channels": ["email"],        "group": "system"},

    # Org-provisioned account (org creates a login/password user directly) —
    # auth-class notification: mandatory, no unsubscribe, same as the other
    # account-credential mails above.
    "workspace.provisioned_account": {"channels": ["email"],        "group": "auth"},
    # Org security policy (require_mfa) suspends/restores membership access —
    # account-access notifications, same auth-class treatment.
    "workspace.mfa_suspension":      {"channels": ["email"],        "group": "auth"},
    "workspace.mfa_restored":         {"channels": ["email"],        "group": "auth"},
}

# Built-in email templates for types that do not carry their own
# "template" key.  Namespaced under templates/notifications/email/ so
# host projects' own "email/*" templates cannot collide.
DEFAULT_EMAIL_TEMPLATES = {
    "otp_code": "notifications/email/otp_code.html",
    "auth_change_requested": "notifications/email/auth_change.html",
    "auth_change_reminder": "notifications/email/auth_change.html",
    "auth_change_urgent": "notifications/email/auth_change.html",
    "auth_change_completed": "notifications/email/auth_change.html",
    "new_message": "notifications/email/new_message.html",
    "report_reviewed": "notifications/email/report_reviewed.html",
    "listing_expiring": "notifications/email/listing_expiring.html",
    "listing_blocked": "notifications/email/listing_blocked.html",
    "magic_link_login": "notifications/email/magic_link_login.html",
    "new_device_login": "notifications/email/new_device_login.html",
    "suspicious_login": "notifications/email/suspicious_login.html",
    "all_sessions_revoked": "notifications/email/all_sessions_revoked.html",
    "gdpr.export_ready": "notifications/email/gdpr_export_ready.html",
    "gdpr.inactivity_warning": "notifications/email/gdpr_inactivity_warning.html",
    "gdpr.inactivity_closed": "notifications/email/gdpr_inactivity_closed.html",
    "workspace.invitation": "notifications/email/workspace_invitation.html",
    "workspace.invitation.new_user": "notifications/email/workspace_invitation_new_user.html",
    "workspace.provisioned_account": "notifications/email/workspace_provisioned_account.html",
    "workspace.mfa_suspension": "notifications/email/workspace_mfa_suspension.html",
    "workspace.mfa_restored": "notifications/email/workspace_mfa_restored.html",
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
    """Template for a type.

    Precedence: per-type ``"template"`` key in the routing entry →
    ``STAPEL_NOTIFICATIONS["EMAIL_TEMPLATES"]`` override → built-in default.
    """
    routing = get_routing(notification_type) or {}
    overrides = notifications_settings.EMAIL_TEMPLATES or {}
    return (
        routing.get("template")
        or overrides.get(notification_type)
        or DEFAULT_EMAIL_TEMPLATES.get(notification_type)
    )
