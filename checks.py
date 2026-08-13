"""Django system checks for stapel-notifications.

Policy (docs/library-standard.md §3.7): W-level for a deployment that will
run but will do something the operator did not ask for. A deployment that
cannot ask anybody which language they read in still sends mail — in the
sender's language, to everybody, forever, and the only feedback loop is a
recipient complaining. That belongs in ``manage.py check``, not in a
support ticket.

E-level for the two type registrations that are defects under every reading
— an unknown group, and a host override that demotes a built-in security
type out of its class. Both produce mail a recipient cannot control or a
passcode carrying a one-click opt-out from all security mail; neither has a
legitimate use, so they stop the boot rather than warn.
"""
import re

from django.core import checks

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
