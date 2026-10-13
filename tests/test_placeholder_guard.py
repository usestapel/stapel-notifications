"""A cached translation that lost its {placeholders} is not used.

Measured on a client fleet: ten library keys were cached, in every
language, as design-mock fillers without their placeholders ("(c) 2026
Service company", "Your <brand> verification code" without {code}), and every
letter's footer read the filler. The source text is the honest fallback.
"""
import pytest

from stapel_notifications.models import TranslationCache
from stapel_notifications.services import _resolve_translations, _same_placeholders

pytestmark = pytest.mark.django_db

LEGAL = "notification.footer.legal"
SUBJECT = "notification.otp_code.subject"


def test_a_cached_value_without_the_placeholders_falls_back_to_the_source():
    TranslationCache.objects.create(
        key=LEGAL,
        values={"en": "(c) 2026 Service company", "ru": "(c) 2026 Service company"},
    )
    for lang in ("en", "ru"):
        text = _resolve_translations([LEGAL], lang)[LEGAL]
        assert "Service company" not in text
        assert "{company_name}" in text


def test_a_cached_value_with_the_placeholders_is_used():
    TranslationCache.objects.create(
        key=SUBJECT, values={"ru": "Код подтверждения {company_name}: {code}"}
    )
    assert _resolve_translations([SUBJECT], "ru")[SUBJECT] == (
        "Код подтверждения {company_name}: {code}"
    )


def test_the_comparison_is_by_name_set():
    assert _same_placeholders("{a} and {b}", "{b} then {a}")
    assert _same_placeholders("Dein Code: {code}", "Your {company_name} code: {code}")
    assert not _same_placeholders("no code", "your {code}")
    assert not _same_placeholders("{code} {extra}", "{code}")
    assert _same_placeholders("plain", "plain too")
