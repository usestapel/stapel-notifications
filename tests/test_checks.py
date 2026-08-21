"""System checks: a deployment that cannot ask a recipient's language.

The defect this guards is not a crash — it is a service that runs, sends
mail, returns 200, and addresses every recipient in the language of whoever
triggered the send. That has no runtime symptom until a human complains, so
it has to be answerable at boot.
"""
import pytest
from django.test import override_settings

from stapel_notifications.checks import check_recipient_language_is_askable
from stapel_notifications.language import PROFILES_LANGUAGE


def test_warns_when_nobody_provides_the_language_function(function_registry_sandbox):
    with function_registry_sandbox._lock:
        function_registry_sandbox._providers.pop(PROFILES_LANGUAGE, None)

    (warning,) = check_recipient_language_is_askable(None)
    assert warning.id == "stapel_notifications.W001"
    assert PROFILES_LANGUAGE in warning.msg


def test_silent_when_the_provider_is_registered(profiles_language):
    assert check_recipient_language_is_askable(None) == []


@pytest.mark.parametrize(
    "routes,expected",
    [
        ({}, ["stapel_notifications.W002"]),
        ({"cdn.": "http://svc-cdn:8000/cdn"}, ["stapel_notifications.W002"]),
        ({"profiles.": "http://svc-profiles:8000/profiles"}, []),
    ],
)
def test_a_remote_transport_needs_a_route_to_profiles(routes, expected):
    """Split deployment: the provider is in another process, so the local
    registry is empty by design — what must exist is a route."""
    with override_settings(
        STAPEL_COMM={"FUNCTION_TRANSPORT": "http", "FUNCTION_ROUTES": routes}
    ):
        assert [w.id for w in check_recipient_language_is_askable(None)] == expected


# ── The channel half of the preference vocabulary (E004) ─────────


@pytest.fixture(autouse=True)
def _reload_settings():
    from stapel_notifications.conf import notifications_settings

    notifications_settings.reload()
    yield
    notifications_settings.reload()


def test_a_channel_with_no_preference_field_is_an_error():
    """E001 caught the unknown GROUP; nothing caught the unknown CHANNEL.

    'system' is a real group, so E001 stays silent — but there is no
    ``webhook_system`` field on UserNotificationSettings, so the recipient
    has no switch for this mail anywhere in the API. ``_should_send`` used
    to answer an unrecognised pair with "send".
    """
    from stapel_notifications.checks import (
        check_notification_channels_have_a_preference,
        check_notification_groups_are_known,
    )

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {
                "invoice_ready": {"channels": ["webhook"], "group": "system"},
            }
        }
    ):
        (error,) = check_notification_channels_have_a_preference(None)
        assert error.id == "stapel_notifications.E004"
        assert "webhook" in error.msg
        # ...and E001 really was silent about it, which is why E004 exists.
        assert check_notification_groups_are_known(None) == []


def test_the_packaged_registry_has_a_preference_for_every_routed_channel():
    from stapel_notifications.checks import check_notification_channels_have_a_preference

    assert check_notification_channels_have_a_preference(None) == []


def test_an_unknown_group_is_not_reported_twice():
    """A typo'd group is E001's story; E004 must not pile on."""
    from stapel_notifications.checks import check_notification_channels_have_a_preference

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"x": {"channels": ["email"], "group": "sistem"}}
        }
    ):
        assert check_notification_channels_have_a_preference(None) == []


def test_the_auth_group_is_exempt():
    """Mandatory by design, and deliberately without a preference field."""
    from stapel_notifications.checks import check_notification_channels_have_a_preference

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"x": {"channels": ["email", "sms", "push"], "group": "auth"}}
        }
    ):
        assert check_notification_channels_have_a_preference(None) == []


# ── Channel providers (E003 / W005) ──────────────────────────────


def test_an_unresolvable_provider_name_stops_the_boot():
    """The name used to resolve to the channel's mock — silently."""
    from stapel_notifications.checks import check_channel_providers_resolve

    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "resedn"}):
        (error,) = check_channel_providers_resolve(None)
        assert error.id == "stapel_notifications.E003"
        assert "resedn" in error.msg


def test_configured_providers_resolve_by_default():
    from stapel_notifications.checks import check_channel_providers_resolve

    assert check_channel_providers_resolve(None) == []


@override_settings(DEBUG=False)
def test_a_mock_provider_in_a_non_debug_deployment_warns():
    """A mock inherited into production is total, silent mail loss."""
    from stapel_notifications.checks import check_channel_providers_deliver

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_PROVIDER": "mock",
            "SMS_PROVIDER": "twilio",
            "PUSH_PROVIDER": "fcm",
        }
    ):
        (warning,) = check_channel_providers_deliver(None)
        assert warning.id == "stapel_notifications.W005"
        assert "EMAIL_PROVIDER" in warning.msg


@override_settings(DEBUG=False)
def test_the_shipped_default_warns_until_a_backend_is_chosen():
    from stapel_notifications.checks import check_channel_providers_deliver

    with override_settings(STAPEL_NOTIFICATIONS={}):
        ids = {w.id for w in check_channel_providers_deliver(None)}
        settings_named = {
            key
            for w in check_channel_providers_deliver(None)
            for key in ("EMAIL_PROVIDER", "SMS_PROVIDER")
            if key in w.msg
        }
    assert ids == {"stapel_notifications.W005"}
    assert settings_named == {"EMAIL_PROVIDER", "SMS_PROVIDER"}


@override_settings(DEBUG=False)
def test_an_unrouted_closed_channel_is_not_a_warning():
    """Telegram ships closed and unrouted, so a stock deployment must stay
    quiet about it: W005 speaks for channels the registry actually uses."""
    from stapel_notifications.checks import check_channel_providers_deliver

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_PROVIDER": "smtp",
            "SMS_PROVIDER": "gatewayapi",
            "PUSH_PROVIDER": "fcm",
        }
    ):
        assert check_channel_providers_deliver(None) == []


@override_settings(DEBUG=False)
def test_routing_to_a_channel_with_no_backend_warns():
    """...and the moment a host routes a type to telegram without naming a
    bot client, the same check says so rather than letting the messages
    disappear."""
    from stapel_notifications.checks import check_channel_providers_deliver

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_PROVIDER": "smtp",
            "SMS_PROVIDER": "gatewayapi",
            "PUSH_PROVIDER": "fcm",
            "TYPES": {"x": {"channels": ["telegram"], "group": "system"}},
        }
    ):
        (warning,) = check_channel_providers_deliver(None)
        assert warning.id == "stapel_notifications.W005"
        assert "TELEGRAM_PROVIDER" in warning.msg


def test_an_unresolvable_telegram_provider_stops_the_boot():
    from stapel_notifications.checks import check_channel_providers_resolve

    with override_settings(STAPEL_NOTIFICATIONS={"TELEGRAM_PROVIDER": "telgram"}):
        (error,) = check_channel_providers_resolve(None)
        assert error.id == "stapel_notifications.E003"
        assert "telgram" in error.msg


def test_telegram_is_a_channel_a_type_may_be_routed_to():
    """E004's other half: routing to telegram must NOT be an error — the
    library carries telegram_messages/telegram_system for it."""
    from stapel_notifications.checks import check_notification_channels_have_a_preference

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "TYPES": {"x": {"channels": ["telegram"], "group": "system"}}
        }
    ):
        assert check_notification_channels_have_a_preference(None) == []


@override_settings(DEBUG=True)
def test_debug_says_this_is_not_production():
    from stapel_notifications.checks import check_channel_providers_deliver

    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"}):
        assert check_channel_providers_deliver(None) == []


@override_settings(DEBUG=False)
def test_real_providers_are_silent():
    from stapel_notifications.checks import check_channel_providers_deliver

    with override_settings(
        STAPEL_NOTIFICATIONS={
            "EMAIL_PROVIDER": "smtp",
            "SMS_PROVIDER": "gatewayapi",
            "PUSH_PROVIDER": "fcm",
        }
    ):
        assert check_channel_providers_deliver(None) == []
