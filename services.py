"""
Notification service orchestrator.

Resolves language, contact info, translations, and dispatches to channels.
"""

import logging
import re
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
from .language import resolve as resolve_language
from .routing import get_email_template, get_routing, is_transactional
from .translation_keys import NOTIFICATION_KEYS, keys_for_type
from .channels.email import send_email
from .channels.push import send_push
from .channels.sms import send_sms

logger = logging.getLogger(__name__)


def _get_keys_for_type(notification_type: str) -> list[str]:
    """Get all translation keys for a notification type.

    The naming rule itself lives in ``translation_keys.keys_for_type`` so the
    ``docs/templates.json`` emitter derives the per-type context from the same
    function the runtime uses, not from a second copy of the convention.
    """
    from .routing import registered_types

    return keys_for_type(
        notification_type,
        known_types=registered_types(),
        extra_keys=(notifications_settings.TEXT or {}),
    )


def _text_overrides() -> dict:
    """``STAPEL_NOTIFICATIONS["TEXT"]`` — the host's copy registry."""
    return notifications_settings.TEXT or {}


def _override_for(key: str, lang: str, overrides: dict) -> tuple[str | None, str | None]:
    """``(pinned, default)`` for one key from the host's TEXT registry.

    ``pinned`` is copy the host declared for THIS language and wins outright:
    a host that spelled out the Russian subject means it, and neither a
    TranslationCache row nor a translate service — both of which only ever saw
    the OLD English — may outvote it.

    ``default`` replaces the built-in English string wherever it is used as a
    default: as the gettext msgid, and as the final fallback. That is what
    keeps an override translatable — a host that overrides in English and
    ships a ``locale/ru`` catalogue keyed on the new msgid still gets Russian,
    so an override can never freeze a notification into one language.
    """
    value = overrides.get(key)
    if value is None:
        return None, None
    if isinstance(value, str):
        return None, value
    if isinstance(value, dict):
        pinned = value.get(lang) or value.get(lang.split("-")[0])
        return pinned, value.get("en")
    logger.warning(
        "STAPEL_NOTIFICATIONS['TEXT'][%r] must be a str or {lang: str}, got %s "
        "— ignored", key, type(value).__name__,
    )
    return None, None


def _gettext_default(default: str, lang: str) -> str | None:
    """The host's own gettext catalogue, asked under ``lang``.

    A host that shipped `locale/ru/LC_MESSAGES/django.po` has already done
    the standard Django thing, and until now this package could not see
    it: its strings live in NOTIFICATION_KEYS and its only route to
    another language was a translate service. So a correctly-internationalised
    project still sent English email and nothing said why (meettoday,
    2026-07-29).

    The English default doubles as the msgid, which is exactly how
    gettext is meant to be used. Returns None when the catalogue has no
    entry — gettext echoes the msgid back, and echoing is not translating.
    """
    from django.utils import translation

    with translation.override(lang):
        translated = translation.gettext(default)
    return translated if translated != default else None


def _resolve_translations(keys: list[str], lang: str) -> dict[str, str]:
    """Resolve translation keys to translated strings.

    Five sources, in order: a language the host PINNED in
    ``STAPEL_NOTIFICATIONS["TEXT"]``, the local TranslationCache, a lazy pull
    through the ``translate.resolve`` comm Function, the host's gettext
    catalogue, and finally the built-in English default — which the host's
    TEXT entry may itself have replaced.

    The gettext step is what makes the library usable without a translate
    service: hosts that already ship .po files get translated notifications
    for free. The last step is a genuine fallback and is now reported as
    such — see the warning below.
    """
    translations = {}
    cached = {tc.key: tc.values for tc in TranslationCache.objects.filter(key__in=keys)}

    missing = [k for k in keys if k not in cached]
    if missing:
        from .translations import resolve_and_cache

        try:
            resolved = resolve_and_cache(missing, lang)
        except Exception as exc:
            logger.debug(
                "lazy translate.resolve failed for %d key(s), trying the "
                "host's gettext catalogue next: %s", len(missing), exc,
            )
        else:
            for key, text in resolved.items():
                cached[key] = {lang: text}

    def _has_nothing_to_translate(s: str) -> bool:
        """True for a string that is the same in every language.

        "© {company_year} {company_name}" and "{company_address}" carry no
        translatable words — only placeholders and punctuation. gettext
        echoes such a msgid back unchanged, which is indistinguishable
        from "not in the catalogue", so without this they would be
        reported as missing on every single email. A warning that fires
        when nothing is wrong stops being read.
        """
        return not re.search(r"[^\W\d_]", re.sub(r"\{[^}]*\}", "", s))

    overrides = _text_overrides()
    untranslated = []
    for key in keys:
        pinned, override_default = _override_for(key, lang, overrides)
        if pinned:
            translations[key] = pinned
            continue
        default = override_default or NOTIFICATION_KEYS.get(key, key)
        if key in cached:
            text = cached[key].get(lang)
            if text:
                translations[key] = text
                continue
        text = _gettext_default(default, lang)
        if text:
            translations[key] = text
            continue
        translations[key] = (
            (cached.get(key) or {}).get("en") or default
        )
        if not _has_nothing_to_translate(default):
            untranslated.append(key)

    if untranslated and lang.split("-")[0] != "en":
        # Not debug: "you asked for ru and are getting English" is the
        # thing an operator needs to see. It looks like success otherwise
        # — the mail sends, it is just in the wrong language.
        logger.warning(
            "notifications: %d/%d string(s) had no %s translation — sent in "
            "the built-in English. Ship a locale/%s catalogue or wire the "
            "translate service. Keys: %s",
            len(untranslated), len(keys), lang, lang.split("-")[0],
            ", ".join(untranslated[:5]),
        )

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


def default_language() -> str:
    """The project's fallback language — never a hardcoded "en".

    A service built for a Russian-speaking market wants `ru` as its last
    resort, and every English string it falls back to is a defect. Reads
    STAPEL_LANGUAGE["DEFAULT"], then Django's LANGUAGE_CODE.
    """
    from stapel_core.language import default_language as _core_default

    return _core_default()


def _active_language() -> str | None:
    """The language this process currently has active, if any.

    In a web process LocaleMiddleware has already resolved it from the
    request; in a consumer process there is usually nothing active and
    this returns the project's LANGUAGE_CODE. Either way it beats a
    hardcoded "en" as a last resort. Returns None when translations are
    deactivated entirely, so the caller can fall through.
    """
    from django.utils.translation import get_language

    return get_language()


def process_notification(
    notification_type: str,
    user_id: str | None,
    variables: dict,
    email: str | None = None,
    phone: str | None = None,
    language: str | None = None,
    event_id: str | None = None,
    content_html: str | None = None,
    content_text: str | None = None,
) -> None:
    """
    Process a notification request: resolve language, contacts, translations,
    and dispatch to all configured channels.

    ``content_html`` / ``content_text`` are the raw-content escape hatch:
    when given, the email body is rendered from them (wrapped in the base
    brand layout) instead of a registered per-type template, and an
    unregistered ``notification_type`` is allowed (group defaults to
    "system", channels derived from the content given).
    """
    # Idempotency: skip if this event was already processed
    if event_id and NotificationLog.objects.filter(data__event_id=event_id, status="sent").exists():
        logger.info("Skipping duplicate event_id=%s", event_id)
        return

    has_content = bool(content_html or content_text)
    routing = get_routing(notification_type)
    if not routing:
        if not has_content:
            logger.error(
                "Unknown notification type: %s (register it via "
                "STAPEL_NOTIFICATIONS['TYPES'] or pass content_html/"
                "content_text)", notification_type,
            )
            return
        # Ad-hoc notification: raw content, no registry entry required.
        channels = ["email"]
        if content_text:
            channels += (["push"] if user_id else []) + (["sms"] if phone else [])
        routing = {"channels": channels, "group": "system"}

    group = routing.get("group", "")

    # Resolve user settings and contact info
    settings_obj = None
    contact = None
    if user_id:
        settings_obj = UserNotificationSettings.objects.filter(user_id=user_id).first()
        contact = UserContact.objects.filter(user_id=user_id, is_active=True).first()

    # Language: the recipient's own choice (asked of profiles by name, not
    # mirrored here), then the caller's, then the recipient's last observed
    # language, then — as a stated decision for the unregistered invitee —
    # the sender's. The whole chain and its reasoning live in language.py.
    choice = resolve_language(user_id, language)
    lang = choice.language

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

    # The footer link shows the HOST, not the brand name again. A brand can
    # run many instances (3571.meettoday.app, meettoday.app, a customer's
    # own deployment), and a footer reading "meettoday" for the third time
    # in one email tells the reader nothing about which one wrote to them.
    company_url = all_vars.get("company_url") or ""
    if company_url:
        from urllib.parse import urlparse

        parsed = urlparse(company_url if "//" in company_url else f"//{company_url}")
        all_vars.setdefault("company_host", parsed.netloc or company_url)
    else:
        all_vars.setdefault("company_host", "")
    all_vars.setdefault("company_address", notifications_settings.COMPANY_ADDRESS)
    all_vars.setdefault(
        "company_year",
        str(notifications_settings.COMPANY_YEAR or datetime.date.today().year),
    )

    # Brand colors — consumed by the email base layout (_base.html)
    all_vars.setdefault("brand_primary", notifications_settings.BRAND_PRIMARY)
    all_vars.setdefault("brand_primary_dark", notifications_settings.BRAND_PRIMARY_DARK)
    all_vars.setdefault("brand_bg", notifications_settings.BRAND_BG)
    all_vars.setdefault("brand_text", notifications_settings.BRAND_TEXT)

    # Add unsubscribe/manage URLs for non-auth groups.
    #
    # A transactional type is excluded even though its group allows
    # unsubscribing: it is a one-to-one message triggered by a named person,
    # so there is no list to leave, and the affordance is actively harmful —
    # see routing.is_transactional. No unsubscribe_url means the base layout
    # also falls back to the minimal footer, which is the point: the
    # "you agreed to receive messages from us" consent line is a lie on a
    # personal invitation.
    frontend_url = notifications_settings.FRONTEND_URL
    if group != "auth" and user_id and not is_transactional(notification_type):
        token = generate_unsubscribe_token(user_id, group, "email")
        all_vars["unsubscribe_url"] = f"{frontend_url}/profiles/notifications/unsubscribe/?token={token}"
        all_vars["manage_notifications_url"] = f"{frontend_url}/settings/notifications"

    # Logo: whatever the host configured, or nothing. There is no packaged
    # fallback image any more — an empty value makes the header render the
    # company name as text instead of an <img> that cannot load.
    all_vars["logo_url"] = notifications_settings.LOGO_URL

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

    # Dispatch to each channel. ``any_delivered`` / ``any_reachability_gap``
    # feed the post-loop check below: a skip caused by "no address on this
    # channel" (or an outright dispatch failure) is a REACHABILITY problem,
    # distinct from a skip caused by the recipient's own notification
    # preference (``_should_send`` False) — the latter is the system working
    # as designed and must stay quiet.
    any_delivered = False
    any_reachability_gap = False
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
            delivered = _dispatch(
                channel, notification_type, group,
                recipient_email, recipient_phone, user_id,
                all_vars, lang,
                content_html=content_html,
                content_text=content_text,
            )
            if not delivered:
                # Nothing was handed to a provider — there was no address on
                # this channel for this recipient. Recording that as "sent"
                # (which it was, for years) made the delivery log lie about
                # the single most common shape in the library: an OTP for an
                # email-only recipient still wrote an sms row reading
                # "sent → unknown". It also fed the idempotency guard above,
                # which keys on status="sent", so a retry that COULD have
                # delivered was suppressed by a delivery that never happened.
                logger.warning(
                    "Skipped %s/%s: no %s address for this recipient",
                    notification_type, channel, channel,
                )
                NotificationLog.objects.create(
                    user_id=user_id,
                    notification_type=notification_type,
                    channel=channel,
                    status="skipped",
                    language=lang,
                    recipient=_get_recipient(channel, recipient_email, recipient_phone, user_id),
                    error_message=f"no {channel} address for this recipient",
                )
                any_reachability_gap = True
                continue
            any_delivered = True
            NotificationLog.objects.create(
                user_id=user_id,
                notification_type=notification_type,
                channel=channel,
                status="sent",
                language=lang,
                recipient=_get_recipient(channel, recipient_email, recipient_phone, user_id),
                title=all_vars.get("push_title", all_vars.get("heading", "")),
                body=all_vars.get("push_body", all_vars.get("body", "")),
                data={
                    # Caller variables first: the log's own keys below are
                    # facts about the delivery and must not be overwritable
                    # by a template variable that happens to share a name.
                    **{k: v for k, v in variables.items() if isinstance(v, (str, int, float, bool))},
                    "notification_type": notification_type,
                    # WHY this letter is in this language. A row reading
                    # "sender" is a letter addressed on a guess; counting
                    # them is how an operator sees a broken language plane
                    # without waiting for someone to complain about the
                    # wrong language (SELECT data->>'language_source').
                    "language_source": choice.source,
                    **({"recipient_language_unaskable": True} if not choice.recipient_reachable else {}),
                    **({"event_id": event_id} if event_id else {}),
                },
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
            any_reachability_gap = True

    # This is the "must not be able to stay silent" guarantee: a caller
    # that gets an event queued (``request_notification`` returned True, a
    # 201 upstream) has no synchronous signal at all — dispatch happens
    # later, off a Kafka consumer, with nothing to hand a failure back to.
    # Per-channel rows already recorded the "skipped"/"failed" detail above
    # at WARNING; nobody reads NotificationLog proactively. When NONE of
    # the routed channels reached the recipient AND at least one of them
    # failed for a reachability reason (not merely "the recipient opted
    # out"), escalate to ERROR with a distinct, greppable prefix so
    # log-based alerting (Sentry issue capture, a CloudWatch/Loki alarm on
    # this string, etc.) has something to catch. Found live: a workspace
    # invitation got a 201 and created the row, but nobody was ever told —
    # "no email address for this recipient" sat at WARNING in a consumer
    # log and nothing downstream ever looked at it.
    if routing["channels"] and any_reachability_gap and not any_delivered:
        logger.error(
            "NOTIFICATION UNDELIVERABLE: %s reached no channel at all "
            "(tried %s) for user_id=%s email=%s phone=%s event_id=%s — "
            "the recipient was never notified",
            notification_type, routing["channels"], user_id,
            recipient_email, recipient_phone, event_id,
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
    content_html: str | None = None,
    content_text: str | None = None,
) -> bool:
    """Dispatch to a specific channel.

    Returns True when the message was handed to the channel's provider, and
    False when there was nothing to deliver it TO — no email address / no
    phone number for this recipient. That distinction is the caller's to
    log: "no address" is not a delivery and must not be recorded as one
    (see ``process_notification``). A provider that is reached and then
    fails raises, as before.
    """
    if channel == "email":
        if not recipient_email:
            return False
        if content_html or content_text:
            # Raw-content escape hatch: wrap the caller-provided body in the
            # base brand layout instead of a registered per-type template.
            html = render_to_string(
                "notifications/email/_raw_content.html",
                {**all_vars, "content_html": content_html, "content_text": content_text},
            )
        else:
            template = get_email_template(notification_type)
            if not template:
                raise ValueError(f"No email template for notification type: {notification_type}")
            html = render_to_string(template, all_vars)
        subject = all_vars.get("subject", f"{all_vars.get('company_name', '')} Notification".strip())
        headers = {}
        # Checked here as well as at the point unsubscribe_url is minted: a
        # caller may pass unsubscribe_url as a plain variable, and a
        # transactional message must not grow a one-click opt-out that way
        # either (routing.is_transactional).
        if (
            group != "auth"
            and "unsubscribe_url" in all_vars
            and not is_transactional(notification_type)
        ):
            headers["List-Unsubscribe"] = f"<{all_vars['unsubscribe_url']}>"
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        send_email(recipient_email, subject, html, headers)
        return True

    elif channel == "push":
        if not user_id:
            raise ValueError("No user_id for push notification")
        title = all_vars.get("push_title", all_vars.get("heading", all_vars.get("company_name", "")))
        body = all_vars.get("push_body", all_vars.get("body", content_text or ""))
        data = {"notification_type": notification_type}
        # Add deep link data from variables
        for key in ("chat_url", "listing_url", "notifications_chat_url"):
            if key in all_vars:
                data[key] = all_vars[key]
        sent_count = send_push(user_id, title, body, data)
        if sent_count == 0:
            logger.warning("No active push tokens for user %s, notification_type=%s", user_id, notification_type)
        return True

    elif channel == "sms":
        if not recipient_phone:
            return False
        sms_text = all_vars.get("sms", all_vars.get("body", content_text or ""))
        send_sms(recipient_phone, sms_text)
        return True

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
