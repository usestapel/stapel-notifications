"""End-to-end translate→notifications i18n loop over the real comm seam.

Both apps run in one process (see conftest: stapel_translate joins
INSTALLED_APPS when importable; STAPEL_COMM is in-process with the outbox
disabled). The test drives the loop exactly as production does:

    translate: TranslationValue written → emit_translations_changed(...)
        └─ action "translations.changed" (thin invalidation)
            └─ notifications handler → call("translate.resolve", ...)
                └─ translate's real Function provider resolves from its DB
                    └─ notifications TranslationCache updated
                        └─ rendered email body uses the new value
"""

import importlib.util

import pytest
from django.test import override_settings

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("stapel_translate") is None,
    reason="stapel-translate is not installed",
)


class _CapturingEmailProvider:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append({"subject": subject, "html": html_body})


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"


def _translate_resolve_registered():
    from stapel_core.comm import function_registry

    return "translate.resolve" in function_registry.names()


@pytest.mark.django_db
def test_translate_to_notifications_loop_updates_cache_and_rendered_email():
    if not _translate_resolve_registered():
        pytest.skip("translate.resolve Function is not registered")

    from stapel_translate.events import emit_translations_changed
    from stapel_translate.models import TranslationEntry

    from stapel_notifications.models import TranslationCache
    from stapel_notifications.services import process_notification

    key = "notification.otp_code.heading"
    en_default = "Your verification code"
    de_value = "Dein Bestätigungscode"

    # 1. Translate side: real model API — entry + per-language values.
    #    (Writing a value may itself emit translations.changed; clear the
    #    cache afterwards so step 2 proves the explicit event→pull path.)
    entry = TranslationEntry.objects.create(key=key, source="backend:notifications")
    entry.set_value("en", en_default, verified=True)
    entry.set_value("de", de_value, verified=True)

    TranslationCache.objects.all().delete()

    # 2. Thin invalidation event through the real emit helper; in-process
    #    delivery runs the notifications subscriber, which pulls the value
    #    through translate's real translate.resolve Function.
    emit_translations_changed("de", [key])

    row = TranslationCache.objects.get(key=key)
    assert row.values["de"] == de_value

    # 3. A rendered email in that language shows the new value, not the
    #    built-in en fallback.
    _CapturingEmailProvider.sent = []
    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}):
        process_notification(
            notification_type="otp_code",
            user_id=None,
            variables={"code": "1234", "expiry_minutes": 5},
            email="dest@example.com",
            language="de",
        )
    (mail,) = _CapturingEmailProvider.sent
    assert de_value in mail["html"]
    assert en_default not in mail["html"]


@pytest.mark.django_db
def test_lazy_resolve_on_miss_pulls_from_real_translate():
    """No event needed: a render-time cache miss pulls straight through the
    real translate.resolve Function."""
    if not _translate_resolve_registered():
        pytest.skip("translate.resolve Function is not registered")

    from stapel_translate.models import TranslationEntry

    from stapel_notifications.models import TranslationCache
    from stapel_notifications.services import _resolve_translations

    key = "notification.new_message.heading"
    entry = TranslationEntry.objects.create(key=key, source="backend:notifications")
    entry.set_value("da", "Ny besked fra {sender_name}", verified=True)

    translations = _resolve_translations([key], "da")
    assert translations[key] == "Ny besked fra {sender_name}"
    assert TranslationCache.objects.get(key=key).values["da"] == "Ny besked fra {sender_name}"
