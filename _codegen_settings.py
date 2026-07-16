"""Single-module Django settings for stapel-notifications's harnesses.

Single source of truth for the ``settings.configure(...)`` block shared by:

  - the pytest suite (``conftest.py``) — mounts notifications on its *bare*
    urlconf (``stapel_notifications.urls``), the historical test layout; and
  - the contract-emission harness (``_codegen.py`` / ``make contract``) —
    mounts notifications on its *canonical* public API prefix
    (``stapel_notifications.codegen_urls`` → ``notifications/api/``) and
    enables drf-spectacular, so the emitted ``schema.json`` / ``flows.json``
    paths are byte-identical to the monolith aggregate's notifications slice
    (contract-pipeline.md §2).

Keeping one copy here means the harness and the tests can never drift in their
``INSTALLED_APPS`` / mock config — the exact hazard contract-pipeline.md §3
calls out ("~30 lines that *reference* the already-existing config, not a
second copy of it"). Copied from stapel-auth's etalon
(``_codegen_settings.py``); adapted to this module's actual conftest content
(no social_django/JWT/Twilio — notifications carries none of that).
"""
from __future__ import annotations


def settings_kwargs(
    *,
    root_urlconf: str = "stapel_notifications.urls_v1",
    contract: bool = False,
) -> dict:
    """Return the ``settings.configure(**kwargs)`` for a single-module
    notifications instance.

    ``root_urlconf`` selects the mount: bare (``stapel_notifications.urls``)
    for the test suite, canonical-prefix (``stapel_notifications.codegen_urls``
    → ``notifications/api/``) for contract emission.

    ``contract=True`` swaps in the *production* ``REST_FRAMEWORK`` (the
    canonical stapel-core config, inlined as plain dotted paths — importing it
    would trip the same chicken-and-egg as spectacular). This matters for
    byte-identity: the monolith emits with
    ``DEFAULT_SCHEMA_CLASS=PermissionAwareAutoSchema`` and the real
    permission/renderer classes, and DRF caches ``REST_FRAMEWORK`` on first
    access, so it must be right at ``configure()`` time — a post-hoc
    assignment is too late. The test suite keeps its permissive config
    (``contract=False``, unset ``DEFAULT_PERMISSION_CLASSES`` — DRF's own
    default — matching the historical conftest, which never set
    ``REST_FRAMEWORK`` at all).

    ``SPECTACULAR_SETTINGS`` is deliberately *not* set. drf-spectacular builds
    its settings singleton at *import* time (``getattr(settings,
    'SPECTACULAR_SETTINGS', {})`` at module load), before a
    ``configure()``-based harness can populate it, so a Django-level
    ``SPECTACULAR_SETTINGS`` is silently ignored and the emitter runs on drf
    **defaults**. That is exactly what the monolith aggregate emits with too
    (the committed ``schema.json`` has ``info.title=""``, no
    ``x-stapel-*`` extensions). The one knob that still must be forced,
    ``SCHEMA_PATH_PREFIX``, is patched on the singleton directly by the
    harness (see ``_codegen._configure``).
    """
    try:
        import stapel_translate  # noqa: F401

        translate_apps = ["stapel_core.django.taskstore", "stapel_translate"]
    except ImportError:
        translate_apps = []

    if contract:
        # Mirror stapel_core.django.settings.REST_FRAMEWORK exactly (the config
        # the monolith emits under). Inlined, not imported, to dodge the
        # import-time settings read; kept in lockstep by test_contract.py's
        # identity gate.
        rest_framework = {
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
            ],
            "DEFAULT_PERMISSION_CLASSES": [
                "stapel_core.django.api.permissions.IsServiceRequest",
                "stapel_core.django.api.permissions.IsSuperUser",
            ],
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.JSONRenderer",
                "rest_framework.renderers.BrowsableAPIRenderer",
            ],
            "DEFAULT_SCHEMA_CLASS": "stapel_core.django.openapi.schemas.PermissionAwareAutoSchema",
            "EXCEPTION_HANDLER": "stapel_core.django.api.errors.stapel_exception_handler",
        }
    else:
        rest_framework = None

    kwargs = dict(
        SECRET_KEY="test-secret-key-not-for-production",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.messages",
            # contrib.admin so the ModelAdmin registrations in admin.py
            # are importable (and covered) in tests.
            "django.contrib.admin",
            # CommonDjangoConfig ships the stapel_core management commands
            # (generate_error_keys, used by the errors.json drift gate).
            "stapel_core.django.apps.CommonDjangoConfig",
            "stapel_core.django.users",
            "rest_framework",
            "drf_spectacular",
            "stapel_notifications",
            *translate_apps,
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
        ROOT_URLCONF=root_urlconf,
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
            "stapel_taskstore": None,
        },
    )
    if rest_framework is not None:
        kwargs["REST_FRAMEWORK"] = rest_framework
    return kwargs


# The multi-module common path prefix drf-spectacular auto-detects in the
# monolith aggregate. Forced on the drf-spectacular settings singleton by the
# harness so a single-module instance derives the same operationIds (see
# _codegen._configure and the SCHEMA_PATH_PREFIX note above). Uniform across
# all five pair-backends.
CODEGEN_SCHEMA_PATH_PREFIX = "/"
