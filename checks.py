"""Django system checks for stapel-notifications.

Policy (docs/library-standard.md §3.7): W-level for a deployment that will
run but will do something the operator did not ask for. A deployment that
cannot ask anybody which language they read in still sends mail — in the
sender's language, to everybody, forever, and the only feedback loop is a
recipient complaining. That belongs in ``manage.py check``, not in a
support ticket.
"""
from django.core import checks

from .language import PROFILES_LANGUAGE

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
