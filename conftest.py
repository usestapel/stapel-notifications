def _unshadow_channels():
    """Resolve ``channels`` to the installed Django Channels, not to ours.

    This repo has its own top-level-importable ``channels/`` package — the
    notification delivery channels (email/push/sms/telegram) — and pytest puts
    the repo root at the front of ``sys.path``. So inside this suite (and in a
    bare ``python`` started here) ``import channels`` finds our package and
    ``import channels.db`` fails, which reads exactly like a missing
    dependency and is not one.

    Every host imports the ASGI library under that name, so the test process
    must resolve it the way a host does — otherwise the optional realtime
    substrate is untestable here. Importing it once with the repo root off the
    path is enough: the module object caches the right ``__path__`` for the
    rest of the session. Our own package is unaffected — nothing imports it as
    top-level ``channels``; it is always ``stapel_notifications.channels``.
    """
    import sys
    from pathlib import Path

    repo = str(Path(__file__).resolve().parent)
    saved = list(sys.path)
    sys.path[:] = [p for p in sys.path if p not in ("", ".", repo)]
    try:
        import channels  # noqa: F401
    except ImportError:
        pass  # Channels is an optional extra here; the realtime tests skip.
    finally:
        sys.path[:] = saved


def pytest_configure(config):
    # Before settings.configure(): stapel_realtime, when installed, is an
    # INSTALLED_APP whose ready() reaches for Channels.
    _unshadow_channels()

    from django.conf import settings
    if not settings.configured:
        # Single source of truth for this block lives in _codegen_settings.py
        # so the test harness and the contract-emission harness (make
        # contract) can never drift (contract-pipeline.md §3). Tests keep the
        # bare mount + permissive REST_FRAMEWORK, exactly as before the
        # extraction.
        from stapel_notifications._codegen_settings import settings_kwargs

        settings.configure(**settings_kwargs())


import pytest  # noqa: E402


@pytest.fixture
def function_registry_sandbox():
    """Snapshot/restore the comm function registry so tests can register
    fake providers (e.g. translate.resolve) without clobbering real ones
    registered at app startup."""
    from stapel_core.comm.registry import function_registry

    providers = dict(function_registry._providers)
    schemas = dict(function_registry._schemas)
    yield function_registry
    with function_registry._lock:
        function_registry._providers.clear()
        function_registry._providers.update(providers)
        function_registry._schemas.clear()
        function_registry._schemas.update(schemas)


@pytest.fixture
def profiles_language(function_registry_sandbox):
    """A stand-in for stapel-profiles' ``profiles.language`` provider.

    Notifications never imports profiles — it asks a name. Tests answer that
    name the way the real owner does: ``{"app_language": ..., "auto_detected_language": ...}``,
    both null for a user profiles has never heard of.

    Yields a dict ``{user_id: (chosen, detected)}`` the test fills in.
    """
    from stapel_notifications.language import PROFILES_LANGUAGE

    answers: dict[str, tuple[str | None, str | None]] = {}

    def provider(payload):
        chosen, detected = answers.get(str(payload["user_id"]), (None, None))
        return {"app_language": chosen, "auto_detected_language": detected}

    function_registry_sandbox.register(PROFILES_LANGUAGE, provider)
    return answers


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        username="testuser",
        email="testuser@example.com",
        password="testpass123",
    )


@pytest.fixture
def other_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        username="otheruser",
        email="otheruser@example.com",
        password="testpass123",
    )


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def authed_client(user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=user)
    return client
