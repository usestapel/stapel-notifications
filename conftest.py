def pytest_configure(config):
    from django.conf import settings
    if not settings.configured:
        # The translate→notifications integration test (test_i18n_loop.py)
        # runs both apps in one process when stapel_translate is installed;
        # everything else works without it.
        try:
            import stapel_translate  # noqa: F401
            _translate_apps = ["stapel_core.django.taskstore", "stapel_translate"]
        except ImportError:
            _translate_apps = []

        settings.configure(
            SECRET_KEY="test-secret-key-not-for-production",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "django.contrib.messages",
                # contrib.admin so the ModelAdmin registrations in admin.py
                # are importable (and covered) in tests.
                "django.contrib.admin",
                "stapel_core.django.users",
                "rest_framework",
                "stapel_notifications",
                *_translate_apps,
            ],
            AUTH_USER_MODEL="users.User",
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
            ROOT_URLCONF="stapel_notifications.urls",
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [],
                    "APP_DIRS": True,
                    "OPTIONS": {"context_processors": []},
                }
            ],
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                }
            },
            # In-memory bus — no Kafka/Redis broker needed
            STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
            # Deliver comm actions synchronously in-process (no outbox tables)
            STAPEL_COMM={"OUTBOX_ENABLED": False, "ACTION_TRANSPORT": "inprocess"},
            # Skip migrations — create tables directly from models
            MIGRATION_MODULES={
                "users": None,
                "notifications": None,
                "translate": None,
                "stapel_tasks": None,
            },
        )


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
