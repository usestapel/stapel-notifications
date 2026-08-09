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
