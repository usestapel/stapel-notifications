"""
Notification service orchestrator.

Resolves language, contact info, translations, and dispatches to channels.
"""

import logging
import string

from django.template.loader import render_to_string

from stapel_core.notifications.tokens import generate_unsubscribe_token

from .models import (
    UserNotificationSettings,
    UserContact,
    TranslationCache,
    NotificationLog,
)
from .conf import notifications_settings
from .routing import get_email_template, get_routing
from .translation_keys import NOTIFICATION_KEYS
from .channels.email import send_email
from .channels.push import send_push
from .channels.sms import send_sms

logger = logging.getLogger(__name__)


def _get_keys_for_type(notification_type: str) -> list[str]:
    """Get all translation keys for a notification type."""
    prefix = f"notification.{notification_type}."
    keys = [k for k in NOTIFICATION_KEYS if k.startswith(prefix)]
    # Also include footer keys
    keys.extend(k for k in NOTIFICATION_KEYS if k.startswith("notification.footer."))
    return keys


def _resolve_translations(keys: list[str], lang: str) -> dict[str, str]:
    """Resolve translation keys to translated strings."""
    translations = {}
    cached = {tc.key: tc.values for tc in TranslationCache.objects.filter(key__in=keys)}

    for key in keys:
        if key in cached:
            translations[key] = cached[key].get(lang) or cached[key].get("en") or NOTIFICATION_KEYS.get(key, key)
        else:
            translations[key] = NOTIFICATION_KEYS.get(key, key)

    return translations


_VALID_PREF_FIELDS = {
    "email_messages",
    "email_system",
    "push_messages",
    "push_system",
    "sms_messages",
    "sms_system",
}


def _should_send(group: str, channel: str, settings_obj: UserNotificationSettings | None) -> bool:
    """Check if notification should be sent based on user preferences."""
    # Auth group is always mandatory
    if group == "auth":
        return True

    if not settings_obj:
        return True

    # Check channel+group specific preference
    pref_field = f"{channel}_{group}"
    if pref_field not in _VALID_PREF_FIELDS:
        logger.warning("No preference field '%s' for channel=%s group=%s, defaulting to send", pref_field, channel, group)
        return True
    return getattr(settings_obj, pref_field, True)


def process_notification(
    notification_type: str,
    user_id: str | None,
    variables: dict,
    email: str | None = None,
    phone: str | None = None,
    language: str | None = None,
    event_id: str | None = None,
) -> None:
    """
    Process a notification request: resolve language, contacts, translations,
    and dispatch to all configured channels.
    """
    # Idempotency: skip if this event was already processed
    if event_id and NotificationLog.objects.filter(data__event_id=event_id, status="sent").exists():
        logger.info("Skipping duplicate event_id=%s", event_id)
        return

    routing = get_routing(notification_type)
    if not routing:
        logger.error(
            "Unknown notification type: %s (register it via "
            "STAPEL_NOTIFICATIONS['TYPES'])", notification_type,
        )
        return

    group = routing.get("group", "")

    # Resolve user settings and contact info
    settings_obj = None
    contact = None
    if user_id:
        settings_obj = UserNotificationSettings.objects.filter(user_id=user_id).first()
        contact = UserContact.objects.filter(user_id=user_id).first()

    # Language resolution: profile override > event language > auto-detected > English
    lang = "en"
    if settings_obj and settings_obj.language:
        lang = settings_obj.language
    elif language:
        lang = language
    elif settings_obj and settings_obj.auto_detected_language:
        lang = settings_obj.auto_detected_language

    # Resolve recipient contact info
    recipient_email = email or (contact.email if contact else None)
    recipient_phone = phone or (contact.phone if contact else None)

    # Resolve translations
    keys = _get_keys_for_type(notification_type)
    translations = _resolve_translations(keys, lang)

    # Build template variables: merge translations (short key) + user variables
    prefix = f"notification.{notification_type}."
    all_vars = {}
    for key, value in translations.items():
        # Use short key names for templates (e.g. "heading" instead of "notification.otp_code.heading")
        if key.startswith(prefix):
            short_key = key[len(prefix):]
            all_vars[short_key] = value
        elif key.startswith("notification.footer."):
            short_key = "footer_" + key.split(".")[-1]
            all_vars[short_key] = value

    # Only allow known variable names — prevent overwriting translation keys
    reserved_keys = set(all_vars.keys())
    for k, v in variables.items():
        if k not in reserved_keys:
            all_vars[k] = v

    # Company branding — used in templates and formatted translation strings
    import datetime
    all_vars.setdefault("company_name", notifications_settings.COMPANY_NAME)
    all_vars.setdefault("company_url", notifications_settings.COMPANY_URL)
    all_vars.setdefault("company_address", notifications_settings.COMPANY_ADDRESS)
    all_vars.setdefault(
        "company_year",
        str(notifications_settings.COMPANY_YEAR or datetime.date.today().year),
    )

    # Add unsubscribe/manage URLs for non-auth groups
    frontend_url = notifications_settings.FRONTEND_URL
    if group != "auth" and user_id:
        token = generate_unsubscribe_token(user_id, group, "email")
        all_vars["unsubscribe_url"] = f"{frontend_url}/profiles/notifications/unsubscribe/?token={token}"
        all_vars["manage_notifications_url"] = f"{frontend_url}/settings/notifications"

    # Logo as inline CID attachment (referenced in email templates)
    all_vars["logo_url"] = "cid:logo"

    # Format translation values with variables (e.g. "{code}" → "1234")
    # Uses _SafeFormatter to prevent attribute/index access in format strings
    formatter = _SafeFormatter()
    for key in list(all_vars.keys()):
        val = all_vars[key]
        if isinstance(val, str) and '{' in val:
            try:
                all_vars[key] = formatter.vformat(val, (), _SafeFormatDict(all_vars))
            except (KeyError, ValueError, IndexError):
                pass

    # Dispatch to each channel
    for channel in routing["channels"]:
        if not _should_send(group, channel, settings_obj):
            NotificationLog.objects.create(
                user_id=user_id,
                notification_type=notification_type,
                channel=channel,
                status="skipped",
                language=lang,
                recipient=_get_recipient(channel, recipient_email, recipient_phone, user_id),
            )
            continue

        try:
            _dispatch(
                channel, notification_type, group,
                recipient_email, recipient_phone, user_id,
                all_vars, lang,
            )
            NotificationLog.objects.create(
                user_id=user_id,
                notification_type=notification_type,
                channel=channel,
                status="sent",
                language=lang,
                recipient=_get_recipient(channel, recipient_email, recipient_phone, user_id),
                title=all_vars.get("push_title", all_vars.get("heading", "")),
                body=all_vars.get("push_body", all_vars.get("body", "")),
                data={"notification_type": notification_type, **({"event_id": event_id} if event_id else {}), **{k: v for k, v in variables.items() if isinstance(v, (str, int, float, bool))}},
            )
        except Exception as e:
            logger.error(
                "Failed to send %s/%s to user %s: %s",
                notification_type, channel, user_id, e,
            )
            NotificationLog.objects.create(
                user_id=user_id,
                notification_type=notification_type,
                channel=channel,
                status="failed",
                language=lang,
                recipient=_get_recipient(channel, recipient_email, recipient_phone, user_id),
                error_message=str(e)[:500],
            )


def _dispatch(
    channel: str,
    notification_type: str,
    group: str,
    recipient_email: str | None,
    recipient_phone: str | None,
    user_id: str | None,
    all_vars: dict,
    lang: str,
) -> None:
    """Dispatch to a specific channel."""
    if channel == "email":
        if not recipient_email:
            logger.debug("Skipping email channel for %s: no email address", notification_type)
            return
        template = get_email_template(notification_type)
        if not template:
            raise ValueError(f"No email template for notification type: {notification_type}")
        html = render_to_string(template, all_vars)
        subject = all_vars.get("subject", f"{all_vars.get('company_name', '')} Notification".strip())
        headers = {}
        if group != "auth" and "unsubscribe_url" in all_vars:
            headers["List-Unsubscribe"] = f"<{all_vars['unsubscribe_url']}>"
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        send_email(recipient_email, subject, html, headers)

    elif channel == "push":
        if not user_id:
            raise ValueError("No user_id for push notification")
        title = all_vars.get("push_title", all_vars.get("heading", all_vars.get("company_name", "")))
        body = all_vars.get("push_body", all_vars.get("body", ""))
        data = {"notification_type": notification_type}
        # Add deep link data from variables
        for key in ("chat_url", "listing_url", "notifications_chat_url"):
            if key in all_vars:
                data[key] = all_vars[key]
        sent_count = send_push(user_id, title, body, data)
        if sent_count == 0:
            logger.warning("No active push tokens for user %s, notification_type=%s", user_id, notification_type)

    elif channel == "sms":
        if not recipient_phone:
            logger.debug("Skipping sms channel for %s: no phone number", notification_type)
            return
        sms_text = all_vars.get("sms", all_vars.get("body", ""))
        send_sms(recipient_phone, sms_text)

    else:
        raise ValueError(f"Unknown channel: {channel}")


def _get_recipient(channel: str, email: str | None, phone: str | None, user_id: str | None) -> str:
    """Get recipient identifier for logging."""
    if channel == "email":
        return email or "unknown"
    elif channel == "sms":
        return phone or "unknown"
    elif channel == "push":
        return str(user_id) if user_id else "unknown"
    return "unknown"


class _SafeFormatDict(dict):
    """Dict that returns {key} for missing keys during format_map."""
    def __missing__(self, key):
        return '{' + key + '}'


class _SafeFormatter(string.Formatter):
    """Formatter that blocks attribute/index access to prevent injection."""

    def get_field(self, field_name, args, kwargs):
        # Only allow simple field names — no dots or brackets
        if '.' in field_name or '[' in field_name:
            raise KeyError(field_name)
        return super().get_field(field_name, args, kwargs)
