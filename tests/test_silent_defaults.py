"""Four defaults that worked, looked fine, and lied. 0.22.0 closes them.

Every one was found by auditing the registry for settings whose DEFAULT
produces running, plausible, wrong behaviour with no error, no warning and no
log line — the opposite of ``EMAIL_PROVIDER="unconfigured"``, which raises.
They are grouped here rather than scattered because they are one class of
defect, and the class is the thing worth pinning.

The fifth finding, the ``BRAND_*`` palette, is deliberately NOT here: a
vendor palette that looks unstyled is a cosmetic default, not a claim about
who sent the message.
"""
import re

import pytest
from django.core import checks as django_checks
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import (
    NotificationLog,
    TranslationCache,
    UserContact,
)
from stapel_notifications.services import process_notification

CYRILLIC = re.compile(r"[А-Яа-яЁё]")


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _CapturingEmailProvider:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append({
            "recipient": recipient, "subject": subject,
            "html": html_body, "headers": headers,
        })


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"

BRANDED = {
    "EMAIL_PROVIDER": CAPTURE,
    "COMPANY_NAME": "Acme",
    "FRONTEND_URL": "https://app.acme.example",
}


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    with override_settings(STAPEL_NOTIFICATIONS=BRANDED):
        yield _CapturingEmailProvider.sent


def _findings(check, ident):
    return [f for f in check(None) if f.id == ident]


# ── 1. Identity is never inherited ──────────────────────────────


class TestIdentityHasNoDefault:

    def test_company_name_no_longer_defaults_to_the_framework(self):
        """The subject line, the header wordmark and the SMS body all read
        this. It used to say "Stapel" to somebody else's customers."""
        assert notifications_settings.defaults["COMPANY_NAME"] == ""

    def test_gatewayapi_sender_no_longer_defaults_to_the_framework(self):
        assert notifications_settings.defaults["GATEWAYAPI_SENDER"] == ""

    @override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE})
    def test_an_empty_company_name_refuses_the_boot(self):
        from stapel_notifications.checks import check_sender_identity_is_declared

        notifications_settings.reload()
        found = _findings(
            check_sender_identity_is_declared, "stapel_notifications.E007"
        )
        assert [e for e in found if "COMPANY_NAME" in e.msg], found
        assert all(isinstance(e, django_checks.Error) for e in found)

    @override_settings(STAPEL_NOTIFICATIONS=BRANDED)
    def test_a_configured_name_is_silent(self):
        from stapel_notifications.checks import check_sender_identity_is_declared

        notifications_settings.reload()
        assert _findings(
            check_sender_identity_is_declared, "stapel_notifications.E007"
        ) == []

    @override_settings(STAPEL_NOTIFICATIONS={})
    def test_a_deployment_still_wiring_itself_up_is_not_nagged(self):
        """No delivering channel means it is not claiming to be anybody yet."""
        from stapel_notifications.checks import check_sender_identity_is_declared

        notifications_settings.reload()
        assert _findings(
            check_sender_identity_is_declared, "stapel_notifications.E007"
        ) == []

    @override_settings(STAPEL_NOTIFICATIONS={
        "SMS_PROVIDER": "gatewayapi", "COMPANY_NAME": "Acme",
        "GATEWAYAPI_TOKEN": "t",
    })
    def test_gatewayapi_without_a_sender_id_refuses_the_boot(self):
        """The provider already raises for a missing TOKEN. This is the other
        half, and it was the silent one — the thread title on the handset."""
        from stapel_notifications.checks import check_sender_identity_is_declared

        notifications_settings.reload()
        found = _findings(
            check_sender_identity_is_declared, "stapel_notifications.E007"
        )
        assert [e for e in found if "GATEWAYAPI_SENDER" in e.msg], found

    @override_settings(STAPEL_NOTIFICATIONS={
        "SMS_PROVIDER": "twilio", "COMPANY_NAME": "Acme",
        "TWILIO_ACCOUNT_SID": "s", "TWILIO_AUTH_TOKEN": "t",
    })
    def test_the_sender_check_is_specific_to_the_provider_that_uses_it(self):
        from stapel_notifications.checks import check_sender_identity_is_declared

        notifications_settings.reload()
        found = _findings(
            check_sender_identity_is_declared, "stapel_notifications.E007"
        )
        assert [e for e in found if "GATEWAYAPI_SENDER" in e.msg] == []


@pytest.mark.django_db
class TestAnEmptyAddressRendersNoParagraph:

    def test_no_address_means_no_block(self, user, capture_email):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type="otp_code", user_id=str(user.id),
            variables={"code": "1234", "expiry_minutes": 5},
        )
        html = capture_email[0]["html"]
        # The paragraph is gone, not present-and-empty.
        assert "Acme" in html, "sanity: the letter did render"
        assert not re.search(r"<p[^>]*>\s*</p>", html), (
            "an empty footer paragraph is still being rendered"
        )

    def test_an_address_still_renders(self, user):
        _CapturingEmailProvider.sent = []
        with override_settings(
            STAPEL_NOTIFICATIONS={**BRANDED, "COMPANY_ADDRESS": "1 Acme Way"}
        ):
            UserContact.objects.create(user_id=user.id, email="u@example.com")
            process_notification(
                notification_type="otp_code", user_id=str(user.id),
                variables={"code": "1234", "expiry_minutes": 5},
            )
        assert "1 Acme Way" in _CapturingEmailProvider.sent[0]["html"]


# ── 2. An unsubscribe that cannot be clicked is not offered ─────


@pytest.mark.django_db
class TestUnsubscribeNeedsAnAbsoluteBase:

    def _send(self, user):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type="listing_expiring", user_id=str(user.id),
            variables={"listing_title": "x", "days_remaining": "3"},
        )
        return _CapturingEmailProvider.sent[0]

    def test_with_an_absolute_base_the_header_is_emitted(self, user, capture_email):
        mail = self._send(user)
        assert mail["headers"]["List-Unsubscribe"].startswith("<https://")
        assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_without_one_the_header_is_omitted_not_invalid(self, user):
        """It used to emit `<​/profiles/notifications/unsubscribe/?token=…>` —
        not a valid RFC 2369 URL, and a dead relative href in the footer, while
        still promising one-click opt-out."""
        _CapturingEmailProvider.sent = []
        with override_settings(
            STAPEL_NOTIFICATIONS={**BRANDED, "FRONTEND_URL": ""}
        ):
            mail = self._send(user)

        assert "List-Unsubscribe" not in mail["headers"]
        assert "List-Unsubscribe-Post" not in mail["headers"]
        assert "/profiles/notifications/unsubscribe/" not in mail["html"]

    def test_a_relative_base_counts_as_no_base(self, user):
        _CapturingEmailProvider.sent = []
        with override_settings(
            STAPEL_NOTIFICATIONS={**BRANDED, "FRONTEND_URL": "/app"}
        ):
            mail = self._send(user)
        assert "List-Unsubscribe" not in mail["headers"]

    @override_settings(STAPEL_NOTIFICATIONS={
        "EMAIL_PROVIDER": CAPTURE, "COMPANY_NAME": "Acme", "FRONTEND_URL": "",
    })
    def test_the_condition_is_named_at_boot(self):
        from stapel_notifications.checks import (
            check_unsubscribe_has_somewhere_to_point,
        )

        notifications_settings.reload()
        found = _findings(
            check_unsubscribe_has_somewhere_to_point, "stapel_notifications.E006"
        )
        assert found and isinstance(found[0], django_checks.Error)

    @override_settings(STAPEL_NOTIFICATIONS=BRANDED)
    def test_an_absolute_base_is_silent(self):
        from stapel_notifications.checks import (
            check_unsubscribe_has_somewhere_to_point,
        )

        notifications_settings.reload()
        assert _findings(
            check_unsubscribe_has_somewhere_to_point, "stapel_notifications.E006"
        ) == []


# ── 3. The language cache miss is per (key, language) ───────────


@pytest.mark.django_db
class TestRunningOurOwnSyncCommandStopsBreakingTranslation:
    """THE regression test.

    `sync_translations` populates every key in LANGUAGES (default `["en"]`).
    The miss check was `key not in cached`, so after that command every render
    in any language hit an English-only row, skipped the lazy resolve, and
    fell through to the built-in English — while the journal recorded the
    language that was ASKED for. A deployment that skipped the recommended
    command worked; one that followed the docs was broken.
    """

    def _seed_english_only(self, keys):
        for key in keys:
            TranslationCache.objects.update_or_create(
                key=key, defaults={"values": {"en": "English text"}}
            )

    def _keys(self):
        from stapel_notifications.services import _get_keys_for_type

        return _get_keys_for_type("otp_code")

    def test_a_russian_render_still_reaches_the_lazy_resolve(self, user, capture_email):
        """With only English cached, ru must NOT be treated as a cache hit."""
        self._seed_english_only(self._keys())
        asked = {}

        def fake_resolve(keys, language):
            asked[language] = list(keys)
            return {k: "РУССКИЙ ТЕКСТ" for k in keys}

        import stapel_notifications.translations as tr

        original = tr.resolve_and_cache
        tr.resolve_and_cache = fake_resolve
        try:
            UserContact.objects.create(user_id=user.id, email="u@example.com")
            process_notification(
                notification_type="otp_code", user_id=str(user.id),
                variables={"code": "1234", "expiry_minutes": 5}, language="ru",
            )
        finally:
            tr.resolve_and_cache = original

        assert "ru" in asked, (
            "an English-only cache row was read as a Russian hit — the bug"
        )
        assert CYRILLIC.search(capture_email[0]["html"])

    def test_two_languages_from_one_cache_row(self, user):
        """The whole point: one key, two languages, each rendered correctly."""
        keys = self._keys()
        for key in keys:
            TranslationCache.objects.update_or_create(
                key=key, defaults={"values": {"en": "Code {code}", "ru": "Код {code}"}}
            )
        UserContact.objects.create(user_id=user.id, email="u@example.com")

        seen = {}
        for language in ("en", "ru"):
            _CapturingEmailProvider.sent = []
            with override_settings(STAPEL_NOTIFICATIONS=BRANDED):
                process_notification(
                    notification_type="otp_code", user_id=str(user.id),
                    variables={"code": "1234", "expiry_minutes": 5},
                    language=language,
                )
            seen[language] = _CapturingEmailProvider.sent[0]["html"]

        assert CYRILLIC.search(seen["ru"]), "ru fell back to English"
        assert not CYRILLIC.search(seen["en"]), "en picked up Russian"

    def test_the_journal_language_matches_what_was_rendered(self, user, capture_email):
        """The half that made this invisible: `language` records what was
        asked for, so a Russian row over an English letter looked correct."""
        keys = self._keys()
        for key in keys:
            TranslationCache.objects.update_or_create(
                key=key, defaults={"values": {"en": "Code {code}", "ru": "Код {code}"}}
            )
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type="otp_code", user_id=str(user.id),
            variables={"code": "1234", "expiry_minutes": 5}, language="ru",
        )

        # otp_code routes to email AND sms; the sms row is a "skipped"
        # reachability row for a recipient with no phone.
        row = NotificationLog.objects.get(
            notification_type="otp_code", channel="email"
        )
        assert row.language.startswith("ru")
        assert CYRILLIC.search(capture_email[0]["html"]), (
            f"journal says language={row.language!r} but the letter is English"
        )

    def test_english_is_not_re_resolved_when_it_is_already_cached(self, user, capture_email):
        """Non-vacuity: the fix must not turn every render into a comm call."""
        self._seed_english_only(self._keys())
        calls = []

        def fake_resolve(keys, language):
            calls.append(language)
            return {}

        import stapel_notifications.translations as tr

        original = tr.resolve_and_cache
        tr.resolve_and_cache = fake_resolve
        try:
            UserContact.objects.create(user_id=user.id, email="u@example.com")
            process_notification(
                notification_type="otp_code", user_id=str(user.id),
                variables={"code": "1234", "expiry_minutes": 5}, language="en",
            )
        finally:
            tr.resolve_and_cache = original

        assert calls == [], "English was already cached and was asked for again"


# ── 4. Push: the row keeps 'sent' and stops implying a handset ──


@pytest.mark.django_db
class TestAPushRowSaysHowManyHandsetsItReached:

    PUSH = {**BRANDED, "PUSH_PROVIDER": "mock"}

    def _send(self, user):
        process_notification(
            notification_type="new_message", user_id=str(user.id),
            variables={"sender_name": "A", "message_preview": "hi"},
        )

    def test_zero_devices_still_writes_the_feed_row(self, user):
        """Kept on purpose: the in-app feed IS this journal, so a web-only
        user must not lose their feed item to make a number honest."""
        with override_settings(STAPEL_NOTIFICATIONS=self.PUSH):
            self._send(user)

        row = NotificationLog.objects.get(channel="push")
        assert row.status == "sent"

    def test_but_the_row_now_says_zero(self, user):
        with override_settings(STAPEL_NOTIFICATIONS=self.PUSH):
            self._send(user)

        row = NotificationLog.objects.get(channel="push")
        assert row.device_count == 0, (
            "status='sent' cannot express 'in the feed, on no handset'"
        )

    def test_a_registered_device_is_counted(self, user):
        from stapel_notifications.models import DevicePushToken

        DevicePushToken.objects.create(
            user_id=user.id, token="tok-1", platform="ios", is_active=True
        )
        with override_settings(STAPEL_NOTIFICATIONS=self.PUSH):
            self._send(user)

        row = NotificationLog.objects.get(channel="push")
        assert row.device_count == 1

    def test_other_channels_leave_it_null(self, user, capture_email):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type="otp_code", user_id=str(user.id),
            variables={"code": "1234", "expiry_minutes": 5},
        )
        row = NotificationLog.objects.get(channel="email")
        assert row.device_count is None, "NULL means 'not a push', not zero"

    def test_a_dashboard_can_separate_feed_from_handset(self, user):
        """The reporting shape the column exists for."""
        from stapel_notifications.models import DevicePushToken

        with override_settings(STAPEL_NOTIFICATIONS=self.PUSH):
            self._send(user)
            DevicePushToken.objects.create(
                user_id=user.id, token="tok-1", platform="ios", is_active=True
            )
            process_notification(
                notification_type="new_message", user_id=str(user.id),
                variables={"sender_name": "B", "message_preview": "yo"},
            )

        push = NotificationLog.objects.filter(channel="push")
        assert push.filter(status="sent").count() == 2      # in the feed
        assert push.filter(device_count__gt=0).count() == 1  # left the building

    def test_zero_devices_reaches_the_undeliverable_escalation(self, user, caplog):
        """A push-only type to a web-only user used to suppress the
        escalation entirely, because `any_delivered` was set."""
        import logging

        with override_settings(STAPEL_NOTIFICATIONS={
            **self.PUSH,
            "TYPES": {"push.only": {"channels": ["push"], "group": "system"}},
        }):
            notifications_settings.reload()
            with caplog.at_level(logging.ERROR):
                process_notification(
                    notification_type="push.only", user_id=str(user.id),
                    variables={},
                )

        assert any(
            "NOTIFICATION UNDELIVERABLE" in r.message for r in caplog.records
        ), "nobody was reached and nothing escalated"

    def test_a_real_device_does_not_escalate(self, user, caplog):
        """The guard on the test above."""
        import logging

        from stapel_notifications.models import DevicePushToken

        DevicePushToken.objects.create(
            user_id=user.id, token="tok-1", platform="ios", is_active=True
        )
        with override_settings(STAPEL_NOTIFICATIONS={
            **self.PUSH,
            "TYPES": {"push.only": {"channels": ["push"], "group": "system"}},
        }):
            notifications_settings.reload()
            with caplog.at_level(logging.ERROR):
                process_notification(
                    notification_type="push.only", user_id=str(user.id),
                    variables={},
                )

        assert not any(
            "NOTIFICATION UNDELIVERABLE" in r.message for r in caplog.records
        )
