"""Package-level public API: __all__ + PEP 562 lazy exports."""

import subprocess
import sys

import stapel_notifications


def test_all_lists_the_public_api():
    assert set(stapel_notifications.__all__) == {
        "notifications_settings",
        "request_notification",
        "process_notification",
        "get_channels",
        "get_group",
        "get_email_template",
        "registered_types",
    }


def test_lazy_exports_resolve_to_canonical_objects():
    from stapel_core.notifications import request_notification

    from stapel_notifications import routing, services
    from stapel_notifications.conf import notifications_settings

    assert stapel_notifications.notifications_settings is notifications_settings
    assert stapel_notifications.request_notification is request_notification
    assert stapel_notifications.process_notification is services.process_notification
    assert stapel_notifications.get_channels is routing.get_channels
    assert stapel_notifications.get_group is routing.get_group
    assert stapel_notifications.get_email_template is routing.get_email_template
    assert stapel_notifications.registered_types is routing.registered_types


def test_dir_includes_public_api():
    assert set(stapel_notifications.__all__) <= set(dir(stapel_notifications))


def test_unknown_attribute_raises_attribute_error():
    import pytest

    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        stapel_notifications.nope


def test_package_import_pulls_no_django():
    """`import stapel_notifications` must work without Django settings."""
    code = (
        "import sys; "
        "import stapel_notifications; "
        "assert 'django' not in sys.modules, 'django imported eagerly'; "
        "assert 'stapel_notifications.services' not in sys.modules; "
        "print(len(stapel_notifications.__all__))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "7"
