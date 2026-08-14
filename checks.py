"""Django system checks for stapel-notifications.

Policy (docs/library-standard.md §3.7): W-level for a deployment that will
run but will do something the operator did not ask for. A deployment that
cannot ask anybody which language they read in still sends mail — in the
sender's language, to everybody, forever, and the only feedback loop is a
recipient complaining. That belongs in ``manage.py check``, not in a
support ticket.

E-level for the registrations and settings that are defects under every
reading — an unknown group, a channel with no preference field, a provider
name that resolves to nothing, and a host override that demotes a built-in
security type out of its class. Each produces mail a recipient cannot
control, mail nobody sends, or a passcode carrying a one-click opt-out from
all security mail; none has a legitimate use, so they stop the boot rather
than warn.
"""
import re

from django.conf import settings as django_settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from .language import PROFILES_LANGUAGE
from .routing import (
    NOTIFICATION_ROUTING,
    SECURITY_GROUPS,
    UNSUBSCRIBABLE_GROUPS,
    VALID_GROUPS,
    unsubscribe_allowed,
)

_HINT = (
    "Install/enable stapel-profiles (>= 0.12.1) in this service, or point "
    "STAPEL_COMM['FUNCTION_TRANSPORT'] + ['FUNCTION_ROUTES'] at the service "
    "that owns profiles, or register your own provider for "
    f"'{PROFILES_LANGUAGE}' returning "
    "{'app_language': str|None, 'auto_detected_language': str|None}. "
    "Silence with SILENCED_SYSTEM_CHECKS if this deployment really is "
    "single-language."
)


@checks.register(checks.Tags.compatibility)
def check_recipient_language_is_askable(app_configs, **kwargs):
    """W: can this deployment learn a recipient's language at all?

    Only the in-process transport can be answered here with certainty — a
    remote transport needs a route, which we can check, but liveness of the
    other service is not a boot-time question.
    """
    from stapel_core.comm import comm_setting, function_registry

    transport = comm_setting("FUNCTION_TRANSPORT", "inprocess")

    if transport == "inprocess":
        if PROFILES_LANGUAGE in function_registry.names():
            return []
        return [checks.Warning(
            f"No provider for the '{PROFILES_LANGUAGE}' comm Function, so no "
            "recipient's own language can ever be resolved: every "
            "notification will be sent in the language of whoever triggered "
            "it (delivery rows will read language_source='sender').",
            hint=_HINT,
            id="stapel_notifications.W001",
        )]

    if transport == "http":
        routes = comm_setting("FUNCTION_ROUTES", {}) or {}
        if not any(PROFILES_LANGUAGE.startswith(prefix) for prefix in routes):
            return [checks.Warning(
                f"FUNCTION_TRANSPORT='http' but no STAPEL_COMM['FUNCTION_ROUTES'] "
                f"prefix matches '{PROFILES_LANGUAGE}': the call cannot be routed, "
                "so every notification will be sent in the sender's language.",
                hint=_HINT,
                id="stapel_notifications.W002",
            )]

    return []


# ── Unsubscribe policy: the registration defects (routing.py) ─────────

#: Words that name an account-security or authentication message. Used only
#: to WARN: a type whose name says "passcode" while its group says "bulk
#: mail" is nearly always a misclassification, and the cost of the miss is a
#: recipient one machine-actionable click away from silencing every security
#: email the platform sends them. A false positive is answered by declaring
#: ``"security": True`` — which is the right declaration anyway.
_SECURITY_WORDS = re.compile(
    r"(?:^|[._-])(?:"
    r"otp|passcode|password|passwd|pin|login|logout|signin|signup|session|"
    r"sessions|mfa|2fa|totp|otc|auth|authenticator|verify|verification|"
    r"security|secure|device|devices|credential|credentials|token|recovery|"
    r"reset|unlock|lockout|suspicious|breach|compromise|impersonat\w*"
    r")(?:$|[._-])"
)

_SECURITY_HINT = (
    "Move the type into the 'auth' group (mandatory, never unsubscribable), "
    "or — if the recipient must keep the right to switch it off — leave the "
    "group as it is and add \"security\": True to the routing entry, which "
    "removes the unsubscribe affordance without touching the preference. "
    "Silence with SILENCED_SYSTEM_CHECKS if the name is a false alarm."
)


def _effective_types() -> dict:
    """Every registered type with its EFFECTIVE routing entry."""
    from .conf import notifications_settings

    return {**NOTIFICATION_ROUTING, **(notifications_settings.TYPES or {})}


@checks.register(checks.Tags.compatibility)
def check_notification_groups_are_known(app_configs, **kwargs):
    """E: a type registered under a group nothing in the system knows.

    A group is also the name of the per-channel preference field
    (``email_system``, ``push_messages``, …). A type under 'sistem' has no
    field, so ``_should_send`` falls through to "send" and the recipient can
    never switch it off — mandatory mail that is not security mail. Under
    the old ``group != "auth"`` rule the same typo also handed it a
    one-click unsubscribe.
    """
    errors = []
    for notification_type, routing in sorted(_effective_types().items()):
        group = (routing or {}).get("group")
        if group in VALID_GROUPS:
            continue
        errors.append(checks.Error(
            f"STAPEL_NOTIFICATIONS['TYPES'][{notification_type!r}] has "
            f"group={group!r}, which is not one of {sorted(VALID_GROUPS)}. "
            "The group names the recipient's preference field, so this type "
            "is mail nobody can switch off.",
            hint=(
                "Use 'auth' for mandatory security/authentication mail, "
                "'messages' for user-to-user, 'system' for everything else. "
                "The set is closed on purpose (routing.VALID_GROUPS)."
            ),
            id="stapel_notifications.E001",
        ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_no_security_type_is_unsubscribable(app_configs, **kwargs):
    """E: a host override that demoted a built-in security type.

    ``get_routing`` lets ``STAPEL_NOTIFICATIONS['TYPES']`` REPLACE a built-in
    entry wholesale — it does not merge. So a host that only wanted to drop
    the SMS channel from ``otp_code``::

        "TYPES": {"otp_code": {"channels": ["email"]}}

    silently dropped ``"group": "auth"`` with it. There is no reading of that
    edit under which a passcode should become unsubscribable, so it is an
    error, not a warning.
    """
    errors = []
    for notification_type, routing in sorted(_effective_types().items()):
        builtin = NOTIFICATION_ROUTING.get(notification_type) or {}
        was_security = builtin.get("group") in SECURITY_GROUPS or builtin.get("security")
        if not was_security:
            continue
        routing = routing or {}
        if routing.get("group") in SECURITY_GROUPS or routing.get("security"):
            continue
        errors.append(checks.Error(
            f"STAPEL_NOTIFICATIONS['TYPES'][{notification_type!r}] overrides a "
            f"built-in security notification (packaged group "
            f"{builtin.get('group')!r}) with group={routing.get('group')!r} and "
            "no \"security\": True. A settings override REPLACES the built-in "
            "entry, it does not merge into it — the security classification "
            "was dropped by the override.",
            hint=(
                "Repeat the classification in the override: "
                f"{{'channels': [...], 'group': {builtin.get('group')!r}}}. " + _SECURITY_HINT
            ),
            id="stapel_notifications.E002",
        ))
    return errors


@checks.register(checks.Tags.security)
def check_raw_content_is_a_decision(app_configs, **kwargs):
    """W: this deployment lets a caller supply the body of branded mail.

    Warn-level, not error: there are deployments where every producer is
    first-party and authenticated and the hatch is genuinely wanted. What
    must not happen is inheriting it — a setting somebody turned on for one
    internal tool three releases ago is the difference between "a bug lets
    you send mail" and "a bug lets you send mail *as us*".
    """
    from .raw_content import HTML, mode

    if mode() != HTML:
        return []
    return [checks.Warning(
        "STAPEL_NOTIFICATIONS['RAW_CONTENT']='html': any producer that can "
        "reach the notification bus can send mail rendered inside this "
        "brand's layout, with markup and links of its own choosing, to a "
        "recipient of its own choosing, under a notification type that need "
        "not be registered. That is first-party phishing with valid SPF/DKIM.",
        hint=(
            "Register the types you send with their own templates and set "
            "RAW_CONTENT to 'off', or to 'text' if callers must be able to "
            "supply a one-off body (their markup is reduced to its text). "
            "Silence with SILENCED_SYSTEM_CHECKS once the producers on this "
            "bus are authenticated and scoped."
        ),
        id="stapel_notifications.W004",
    )]


@checks.register(checks.Tags.compatibility)
def check_security_shaped_types_are_classified(app_configs, **kwargs):
    """W: a type whose NAME reads as security mail, in a bulk-mail group.

    Heuristic on purpose, and warn-level on purpose: nobody can decide from
    a string whether 'device_added' is a security notice or a product tour
    step. What is decidable is that the two answers have very different
    costs, and only one of them is silent.
    """
    warnings = []
    for notification_type, routing in sorted(_effective_types().items()):
        if not unsubscribe_allowed(routing):
            continue
        if not _SECURITY_WORDS.search(notification_type):
            continue
        warnings.append(checks.Warning(
            f"Notification type {notification_type!r} is named like an "
            f"account-security message but sits in group "
            f"{(routing or {}).get('group')!r} "
            f"({sorted(UNSUBSCRIBABLE_GROUPS)} carry List-Unsubscribe + "
            "List-Unsubscribe-Post: One-Click). A mail client or an "
            "anti-abuse scanner can POST that URL with no human involved, "
            "and this library's token unsubscribes the recipient from the "
            "whole group — so one automated click stops the mail that tells "
            "them their account is under attack.",
            hint=_SECURITY_HINT,
            id="stapel_notifications.W003",
        ))
    return warnings


# ── The preference field a channel+group pair must have ───────────────

@checks.register(checks.Tags.compatibility)
def check_notification_channels_have_a_preference(app_configs, **kwargs):
    """E: a routed channel with no preference field for the type's group.

    E001 checks the GROUP half of ``services._VALID_PREF_FIELDS``; this is
    the CHANNEL half, and it was the half nobody was told about. A type
    registered as::

        "TYPES": {"invoice_ready": {"channels": ["webhook"], "group": "system"}}

    names a real group, so E001 is silent — but there is no
    ``webhook_system`` field on ``UserNotificationSettings``, so the
    recipient has no switch for it anywhere in the API. ``_should_send``
    now refuses that pair instead of sending it (an unrecognised preference
    used to mean "send"), which turns the defect from unstoppable mail into
    mail that never leaves; either way the registration is wrong and the
    host should learn it at boot.

    ``auth`` is exempt on purpose: that group is mandatory by design and
    deliberately has no preference field.
    """
    from .services import _VALID_PREF_FIELDS

    errors = []
    for notification_type, routing in sorted(_effective_types().items()):
        routing = routing or {}
        group = routing.get("group")
        # An unknown group is E001's story, not this one — do not report the
        # same registration twice under two ids.
        if group not in UNSUBSCRIBABLE_GROUPS:
            continue
        for channel in routing.get("channels") or []:
            if f"{channel}_{group}" in _VALID_PREF_FIELDS:
                continue
            errors.append(checks.Error(
                f"STAPEL_NOTIFICATIONS['TYPES'][{notification_type!r}] routes to "
                f"channel {channel!r} in group {group!r}, but "
                f"UserNotificationSettings has no {channel}_{group} field. The "
                "recipient has no switch for this mail anywhere in the API, and "
                "_should_send refuses a preference it cannot read — so this "
                "type is silently undeliverable on that channel.",
                hint=(
                    "Route the type to a channel this library carries a "
                    "preference for (email, sms, push), or drop the channel "
                    "from the entry. The pairs are fixed by the model: "
                    f"{sorted(_VALID_PREF_FIELDS)}."
                ),
                id="stapel_notifications.E004",
            ))
    return errors


# ── Channel providers: what actually happens to a passcode ───────────

#: Short names whose provider accepts a message and delivers it NOWHERE.
#: ``mock`` writes a log line; ``unconfigured`` raises. Both mean "no mail
#: leaves this deployment on this channel".
_NON_DELIVERING = frozenset({"mock", "unconfigured"})


def _provider_axes():
    """(setting key, channel name, provider registry) for the three channels.

    Imported lazily: ``channels.push`` imports models at module scope, and
    this module is loaded from ``AppConfig.ready``.
    """
    from .channels.email import _PROVIDERS as EMAIL_PROVIDERS
    from .channels.push import _PROVIDERS as PUSH_PROVIDERS
    from .channels.sms import _PROVIDERS as SMS_PROVIDERS

    return (
        ("EMAIL_PROVIDER", "email", EMAIL_PROVIDERS),
        ("SMS_PROVIDER", "sms", SMS_PROVIDERS),
        ("PUSH_PROVIDER", "push", PUSH_PROVIDERS),
    )


@checks.register(checks.Tags.security)
def check_channel_providers_resolve(app_configs, **kwargs):
    """E: a provider setting that names nothing this process can load.

    The resolver used to answer an unknown short name — or a dotted path
    whose import raised — with the channel's MOCK provider and a WARNING.
    The mock returns, and ``services._dispatch`` counts a provider that
    returned as a delivery, so a typo or a missing dependency after a deploy
    downgraded a working mailer to "log the subject, journal it as sent".
    It now raises at send time; asking the same question at boot is what
    turns that into a failed deploy instead of a quiet week of lost OTPs.
    """
    from .channels.sms import _resolve_provider_class
    from .conf import notifications_settings

    errors = []
    for setting, channel, registry in _provider_axes():
        try:
            _resolve_provider_class(
                getattr(notifications_settings, setting), registry, channel, setting
            )
        except ImproperlyConfigured as exc:
            errors.append(checks.Error(
                str(exc),
                hint=(
                    "Fix the value, or install whatever the dotted path needs. "
                    "There is deliberately no fallback: the fallback was a mock "
                    "that reported success."
                ),
                id="stapel_notifications.E003",
            ))
    return errors


@checks.register(checks.Tags.security)
def check_channel_providers_deliver(app_configs, **kwargs):
    """W: a routed channel whose provider sends nothing, outside DEBUG.

    Warn-level, not error: a deployment that never sends SMS, or a staging
    box that must not mail real people, is a legitimate reason to run a
    non-delivering provider — which is exactly why it must not be possible
    to INHERIT one. ``mock`` set for a local checkout three releases ago and
    carried into production is total, silent mail loss on a channel that
    every built-in security type routes to.

    Silent under DEBUG: that deployment has already said it is not
    production.
    """
    from .conf import notifications_settings

    if django_settings.DEBUG:
        return []

    routed = {
        channel
        for routing in _effective_types().values()
        for channel in ((routing or {}).get("channels") or [])
    }

    warnings = []
    for setting, channel, _registry in _provider_axes():
        if channel not in routed:
            continue
        name = (getattr(notifications_settings, setting) or "").strip().lower()
        if name not in _NON_DELIVERING:
            continue
        what = (
            "writes a log line and returns"
            if name == "mock"
            else "raises on every send, because no backend was ever chosen"
        )
        warnings.append(checks.Warning(
            f"STAPEL_NOTIFICATIONS['{setting}']={name!r} with DEBUG=False: the "
            f"{channel} provider {what}, so nothing this deployment sends on "
            f"the {channel} channel reaches anybody — including the passcodes, "
            "password resets and account-closure notices the built-in types "
            f"route to {channel}.",
            hint=(
                f"Point {setting} at a real provider (short name or dotted "
                "path). Silence with SILENCED_SYSTEM_CHECKS if this "
                "deployment is deliberately not delivering on this channel."
            ),
            id="stapel_notifications.W005",
        ))
    return warnings
