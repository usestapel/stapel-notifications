"""stapel-notifications — multi-channel notifications (email / SMS / push).

Public API (lazily resolved, PEP 562 — importing this package pulls in
no Django code until an attribute is actually accessed):

    notifications_settings  — the ``STAPEL_NOTIFICATIONS`` settings namespace
    request_notification    — publish a notification request to the bus
    process_notification    — resolve language/contacts/templates and dispatch
    get_channels            — channels configured for a notification type
    get_group               — preference group of a notification type
    get_email_template      — effective email template for a notification type
    registered_types        — all known notification types (built-in + host)
"""

__all__ = [
    "get_channels",
    "get_email_template",
    "get_group",
    "notifications_settings",
    "process_notification",
    "registered_types",
    "request_notification",
]

# name -> (module, attribute); relative modules resolve inside this package
_EXPORTS = {
    "notifications_settings": (".conf", "notifications_settings"),
    "request_notification": ("stapel_core.notifications", "request_notification"),
    "process_notification": (".services", "process_notification"),
    "get_channels": (".routing", "get_channels"),
    "get_group": (".routing", "get_group"),
    "get_email_template": (".routing", "get_email_template"),
    "registered_types": (".routing", "registered_types"),
}


def __getattr__(name):
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    value = getattr(import_module(module_path, __name__), attr)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
