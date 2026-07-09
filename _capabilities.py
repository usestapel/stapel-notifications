"""stapel-notifications capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli

#: The three channel-backend selectors are the module's CTO-facing axes
#: (provider choice per channel). Credentials, branding, template variables
#: and LANGUAGES are tuning; TYPES / EMAIL_TEMPLATES are merge-registry
#: extension points (curated in docs/capabilities.meta.json), not axes.
_AXES = ("EMAIL_PROVIDER", "SMS_PROVIDER", "PUSH_PROVIDER")


def main(argv=None):
    from stapel_notifications._codegen import _configure

    _configure()
    from stapel_notifications.conf import DEFAULTS
    from stapel_notifications.urls import GATE_REGISTRY

    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/notifications/api",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in _AXES,
        axis_group=axis_group_rules(suffix={"_PROVIDER": "notifications.providers"}),
        prog="stapel-notifications-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
