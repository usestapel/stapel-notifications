"""The environment does not get to choose who sends a passcode.

``EMAIL_PROVIDER`` / ``SMS_PROVIDER`` / ``PUSH_PROVIDER`` name the class this
process imports and runs to deliver one-time codes, password resets and
account-closure notices. They used to be plain keys, so ``AppSettings``
resolved them from ``os.environ`` — a stray or leaked variable in the pod
picked the delivery backend. They are ``import_strings`` now, which makes
them implicitly ``no_env`` (stapel-core 0.24.0).

The three halves this pins:

* the env var is ignored (the fix),
* the ``STAPEL_NOTIFICATIONS`` dict is still honoured (no over-reach),
* a plain key on the same instance is still env-overridable (non-vacuity —
  otherwise the first assertion would pass on a broken settings object).
"""
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from stapel_notifications.conf import PROVIDER_SETTINGS, notifications_settings


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_env_var_cannot_choose_a_provider(key, monkeypatch):
    monkeypatch.setenv(key, "myattacker.providers.Exfiltrate")
    notifications_settings.reload()

    assert getattr(notifications_settings, key) == notifications_settings.defaults[key]


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_the_settings_dict_still_chooses_a_provider(key, monkeypatch):
    """No over-reach: the project's own settings module stays trusted, and
    wins over both the default and a same-named env var."""
    monkeypatch.setenv(key, "myattacker.providers.Exfiltrate")
    with override_settings(STAPEL_NOTIFICATIONS={key: "mock"}):
        assert getattr(notifications_settings, key) == "mock"


def test_a_plain_key_is_still_read_from_the_environment(monkeypatch):
    """Non-vacuity: the env step is closed for the implementation seam only,
    not broken for the namespace."""
    monkeypatch.setenv("COMPANY_NAME", "Acme")
    notifications_settings.reload()

    assert notifications_settings.COMPANY_NAME == "Acme"


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_declaring_a_provider_import_string_does_not_break_resolution(key):
    """The value stays a string, resolved by the registry-aware resolver.

    ``import_strings`` normally makes ``AppSettings`` call ``import_string``
    on attribute access, which would raise on every built-in short name
    (``"twilio"``, ``"mock"``, the shipped ``"unconfigured"``/``"fcm"``) and
    would turn a typo into a bare ``ImportError`` instead of the
    ``ImproperlyConfigured`` that ``notifications.E003`` reports at boot.
    """
    from stapel_notifications.channels.sms import _resolve_provider_class
    from stapel_notifications.checks import _provider_axes

    registry = {setting: reg for setting, _channel, reg in _provider_axes()}[key]

    with override_settings(STAPEL_NOTIFICATIONS={key: "mock"}):
        value = getattr(notifications_settings, key)
        assert isinstance(value, str)
        assert _resolve_provider_class(value, registry, "test", key) is registry["mock"]

    with override_settings(
        STAPEL_NOTIFICATIONS={key: "stapel_notifications.channels.sms._MockSMSProvider"}
    ):
        from stapel_notifications.channels.sms import _MockSMSProvider

        assert _resolve_provider_class(
            getattr(notifications_settings, key), registry, "test", key
        ) is _MockSMSProvider

    with override_settings(STAPEL_NOTIFICATIONS={key: "myapp.nope.Missing"}):
        with pytest.raises(ImproperlyConfigured):
            _resolve_provider_class(
                getattr(notifications_settings, key), registry, "test", key
            )


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_check_names_the_ignored_env_var(key, monkeypatch):
    """Ignoring a variable is silent, so the rule carries its own alarm:
    ``stapel_core.conf.W001`` names it at ``manage.py check`` time."""
    from stapel_core.conf_checks import W001_ENV_VAR_IGNORED, check_ignored_env_vars

    monkeypatch.setenv(key, "myattacker.providers.Exfiltrate")

    ours = [
        w for w in check_ignored_env_vars(None)
        if w.id == W001_ENV_VAR_IGNORED and "STAPEL_NOTIFICATIONS" in w.msg
    ]
    assert [w for w in ours if key in w.msg], [w.msg for w in ours]
    # names only — the check must never echo the value of an env var
    assert not any("Exfiltrate" in w.msg for w in ours)


def test_no_provider_is_env_overridable_by_default():
    """The allowlist is empty on purpose: a deployment that must select an
    implementation from the environment adds its key there deliberately."""
    assert notifications_settings.env_overridable == frozenset()
    assert set(PROVIDER_SETTINGS) <= notifications_settings.import_strings
