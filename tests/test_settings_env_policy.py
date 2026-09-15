"""The environment does not get to choose who sends a passcode.

``EMAIL_PROVIDER`` / ``SMS_PROVIDER`` / ``PUSH_PROVIDER`` name the class this
process imports and runs to deliver one-time codes, password resets and
account-closure notices. They used to be plain keys, so ``AppSettings``
resolved them from ``os.environ`` — a stray or leaked variable in the pod
picked the delivery backend. They are ``import_strings`` now, which makes
them implicitly ``no_env`` (stapel-core 0.24.0).

**0.21.0 narrowed that closure, and made it stricter where it counts.** A
blanket closure was half-right: it stopped the attack and it also stopped the
legitimate "pick the mail backend per environment", so deployments wrote
``os.getenv`` into their own settings modules and the documented variable
worked there and nowhere else. ``env_enum`` (stapel-core 0.70.0) draws the
line at the threat instead: a SHORT NAME this package ships may come from the
environment — choosing among providers already installed cannot introduce
code — and a DOTTED PATH may not, because that is new code on the privileged
path.

Note which direction the security property moved. ``EMAIL_PROVIDER=
myattacker.providers.Exfiltrate`` used to be silently ignored; it now RAISES.
The attacker still cannot choose the class, and the operator now finds out.

The halves this pins:

* a dotted path from the environment is refused, loudly (the fix, hardened),
* a short name from the environment IS honoured (the need, met),
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
def test_env_var_cannot_name_a_class_to_import(key, monkeypatch):
    """The attack, refused — and now audibly.

    Before 0.21.0 this resolved to the default and said nothing. Silence was
    safe and useless: the operator who set the variable never learned it did
    nothing, which is the same defect one layer up.
    """
    monkeypatch.setenv(key, "myattacker.providers.Exfiltrate")
    notifications_settings.reload()

    with pytest.raises(ImproperlyConfigured) as exc:
        getattr(notifications_settings, key)
    assert "dotted import path" in str(exc.value)
    assert "settings module" in str(exc.value)


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_env_var_may_choose_among_the_names_this_package_ships(key, monkeypatch):
    """The need, met — and the reason no host has to write os.getenv again."""
    monkeypatch.setenv(key, "mock")
    notifications_settings.reload()

    assert getattr(notifications_settings, key) == "mock"


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_an_unknown_short_name_from_the_environment_is_refused(key, monkeypatch):
    """A typo must not resolve to the default either: same silence."""
    monkeypatch.setenv(key, "resedn")
    notifications_settings.reload()

    with pytest.raises(ImproperlyConfigured) as exc:
        getattr(notifications_settings, key)
    assert "resedn" in str(exc.value)


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
def test_check_reports_a_rejected_env_var_at_boot(key, monkeypatch):
    """Refusing at first read is not enough on a notifications service.

    "The moment the key is read" is the first passcode after a deploy that
    looked green, so the same finding is reported by ``manage.py check``:
    ``stapel_core.conf.E003``, an Error because the process is NOT quietly
    running a safe value.

    This replaces a W001 assertion. W001 is now correctly SILENT for these
    keys — the variable is no longer ignored — and a check still crying
    "ignored" about a variable that works would be the lie this release
    removed, reintroduced from the other side.
    """
    from stapel_core.conf_checks import (
        E003_ENV_ENUM_REJECTED,
        W001_ENV_VAR_IGNORED,
        check_env_enum_values,
        check_ignored_env_vars,
    )

    monkeypatch.setenv(key, "myattacker.providers.Exfiltrate")

    ours = [
        e for e in check_env_enum_values(None)
        if e.id == E003_ENV_ENUM_REJECTED and "STAPEL_NOTIFICATIONS" in e.msg
    ]
    assert [e for e in ours if key in e.msg], [e.msg for e in ours]

    stale = [
        w for w in check_ignored_env_vars(None)
        if w.id == W001_ENV_VAR_IGNORED and "STAPEL_NOTIFICATIONS" in w.msg
        and key in w.msg
    ]
    assert stale == [], "W001 still calls a variable ignored that now works"


def test_no_provider_is_env_overridable_by_default():
    """``env_overridable`` — the ALL-VALUES hatch, dotted paths included —
    stays empty on purpose. ``env_enum`` is the narrow door and is the only
    one this package opens."""
    assert notifications_settings.env_overridable == frozenset()
    assert set(PROVIDER_SETTINGS) <= notifications_settings.import_strings
    assert set(PROVIDER_SETTINGS) <= set(notifications_settings.env_enum)


@pytest.mark.parametrize("key", PROVIDER_SETTINGS)
def test_the_vocabulary_is_the_channel_registry_itself(key):
    """Derived, never a second list: a provider added to a channel module is
    selectable from the environment the same day, and a name retired there
    stops being accepted without anyone editing conf.py."""
    from stapel_notifications.checks import _provider_axes

    registry = {setting: reg for setting, _channel, reg in _provider_axes()}[key]
    assert set(notifications_settings.env_vocabulary(key)) == set(registry)
