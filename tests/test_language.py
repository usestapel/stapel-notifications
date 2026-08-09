"""Asking profiles for a recipient's language — including when it fails.

The point of asking instead of mirroring is that failure is *visible*. So
the failure paths are the ones worth pinning: a provider that raises, a
provider that answers nonsense, and a provider that is not there at all,
must each degrade to the sender's language while SAYING they did — never
silently, and never by pretending the recipient stated a preference.
"""
import uuid

import pytest

from stapel_notifications import language as lang_module
from stapel_notifications.language import PROFILES_LANGUAGE, ask_profiles, resolve


@pytest.fixture
def uid():
    return str(uuid.uuid4())


def test_a_provider_that_raises_is_reported_not_swallowed(
    function_registry_sandbox, uid, caplog
):
    def broken(payload):
        raise RuntimeError("profiles is having a day")

    with function_registry_sandbox._lock:
        function_registry_sandbox._providers[PROFILES_LANGUAGE] = broken

    with caplog.at_level("WARNING", logger="stapel_notifications.language"):
        assert ask_profiles(uid) == (None, None, False)
    assert any("RECIPIENT LANGUAGE UNASKABLE" in r.message for r in caplog.records)


def test_a_provider_answering_nonsense_is_not_trusted(
    function_registry_sandbox, uid, caplog
):
    with function_registry_sandbox._lock:
        function_registry_sandbox._providers[PROFILES_LANGUAGE] = lambda payload: "ru"

    with caplog.at_level("ERROR", logger="stapel_notifications.language"):
        assert ask_profiles(uid) == (None, None, False)
    assert any("expected a mapping" in r.message for r in caplog.records)


def test_an_absent_provider_says_so_once_per_send(
    function_registry_sandbox, uid, caplog
):
    with function_registry_sandbox._lock:
        function_registry_sandbox._providers.pop(PROFILES_LANGUAGE, None)

    with caplog.at_level("WARNING", logger="stapel_notifications.language"):
        assert ask_profiles(uid) == (None, None, False)
    assert any(PROFILES_LANGUAGE in r.message for r in caplog.records)


def test_resolution_marks_a_guess_as_a_guess(profiles_language, uid):
    from django.utils import translation

    profiles_language[uid] = ("de", None)
    with translation.override("ru"):
        chosen = resolve(uid, None)
        guessed = resolve(None, None)

    assert (chosen.language, chosen.source, chosen.is_guess) == (
        "de", lang_module.SOURCE_RECIPIENT_CHOICE, False,
    )
    assert (guessed.language, guessed.source, guessed.is_guess) == (
        "ru", lang_module.SOURCE_SENDER, True,
    )


def test_an_unreachable_plane_never_masquerades_as_a_preference(
    function_registry_sandbox, uid
):
    """``recipient_reachable=False`` is the fact the old mirror could not
    express: we did not learn that the recipient has no preference, we
    failed to ask."""
    from django.utils import translation

    with function_registry_sandbox._lock:
        function_registry_sandbox._providers.pop(PROFILES_LANGUAGE, None)

    with translation.override("ru"):
        choice = resolve(uid, None)

    assert choice.source == lang_module.SOURCE_SENDER
    assert choice.recipient_reachable is False
    assert choice.is_guess is True
