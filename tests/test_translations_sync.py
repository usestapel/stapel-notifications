"""Pull-side translation sync: sync_translations command + lazy resolve-on-miss.

Uses a fake translate.resolve provider registered against the exact loop
contract: input {"keys": [str], "language": str} → output {"values":
{key: text}}; keys missing in both requested and default language are
omitted from the result.
"""

import pytest
from django.core.management import call_command

from stapel_notifications.models import TranslationCache
from stapel_notifications.services import _resolve_translations
from stapel_notifications.translation_keys import NOTIFICATION_KEYS


@pytest.fixture
def fake_resolve(function_registry_sandbox):
    """Fake provider: German for two otp keys, nothing else."""
    from stapel_core.comm import register_function

    store = {
        "de": {
            "notification.otp_code.heading": "Dein Bestätigungscode",
            "notification.otp_code.body": "Nutze den folgenden Code:",
        },
        "en": {
            "notification.otp_code.heading": "Your verification code",
        },
    }
    calls = []

    def resolve(payload):
        calls.append(payload)
        lang_store = store.get(payload["language"], {})
        return {
            "values": {
                k: lang_store[k] for k in payload["keys"] if k in lang_store
            }
        }

    with function_registry_sandbox._lock:
        function_registry_sandbox._providers.pop("translate.resolve", None)
    register_function("translate.resolve", resolve)
    return calls


@pytest.fixture
def broken_resolve(function_registry_sandbox):
    from stapel_core.comm import register_function

    def resolve(payload):
        raise RuntimeError("translate is down")

    with function_registry_sandbox._lock:
        function_registry_sandbox._providers.pop("translate.resolve", None)
    register_function("translate.resolve", resolve)


# ── sync_translations (initial population) ──────────────────────


@pytest.mark.django_db
class TestSyncTranslationsCommand:
    def test_populates_cache_for_all_notification_keys(self, fake_resolve):
        call_command("sync_translations", languages="de")
        (payload,) = fake_resolve
        assert payload["language"] == "de"
        assert set(payload["keys"]) == set(NOTIFICATION_KEYS)
        row = TranslationCache.objects.get(key="notification.otp_code.heading")
        assert row.values == {"de": "Dein Bestätigungscode"}
        # keys translate does not know stay out of the cache
        assert not TranslationCache.objects.filter(
            key="notification.footer.legal"
        ).exists()

    def test_multiple_languages_merge_into_values(self, fake_resolve):
        call_command("sync_translations", languages="de,en")
        row = TranslationCache.objects.get(key="notification.otp_code.heading")
        assert row.values == {
            "de": "Dein Bestätigungscode",
            "en": "Your verification code",
        }

    def test_default_languages_from_settings(self, fake_resolve):
        from django.test import override_settings

        from stapel_notifications.conf import notifications_settings

        with override_settings(STAPEL_NOTIFICATIONS={"LANGUAGES": ["de"]}):
            notifications_settings.reload()
            try:
                call_command("sync_translations")
            finally:
                notifications_settings.reload()
        (payload,) = fake_resolve
        assert payload["language"] == "de"

    def test_translate_down_reports_error_but_does_not_crash(self, broken_resolve):
        call_command("sync_translations", languages="de")
        assert TranslationCache.objects.count() == 0


# ── Lazy resolve-on-miss in the render path ─────────────────────


@pytest.mark.django_db
class TestLazyResolveOnMiss:
    KEY = "notification.otp_code.heading"

    def test_cache_miss_pulls_stores_and_uses_value(self, fake_resolve):
        translations = _resolve_translations([self.KEY], "de")
        assert translations[self.KEY] == "Dein Bestätigungscode"
        # stored for the next render
        row = TranslationCache.objects.get(key=self.KEY)
        assert row.values["de"] == "Dein Bestätigungscode"

    def test_cached_key_is_not_refetched(self, fake_resolve):
        TranslationCache.objects.create(key=self.KEY, values={"de": "Cached"})
        translations = _resolve_translations([self.KEY], "de")
        assert translations[self.KEY] == "Cached"
        assert fake_resolve == []  # no resolve call for cache hits

    def test_translate_down_degrades_to_builtin_default(self, broken_resolve):
        translations = _resolve_translations([self.KEY], "de")
        assert translations[self.KEY] == NOTIFICATION_KEYS[self.KEY]  # en fallback
        assert TranslationCache.objects.count() == 0

    def test_function_not_registered_degrades_to_builtin_default(
        self, function_registry_sandbox
    ):
        with function_registry_sandbox._lock:
            function_registry_sandbox._providers.pop("translate.resolve", None)
        translations = _resolve_translations([self.KEY], "de")
        assert translations[self.KEY] == NOTIFICATION_KEYS[self.KEY]
