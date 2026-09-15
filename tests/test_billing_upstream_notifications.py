"""The three billing letters — upstream for stapel-billing 0.14.0.

Registry-only from this side: no producer lives in this package. stapel-billing
subscribes to its own ``payment.completed`` / ``payment.failed`` /
``subscription.changed`` facts and requests these types; everything here is
about what happens once one is requested.

WHAT THE `billing` GROUP IS FOR, AND THE TEST THAT MATTERS MOST

``billing`` is the second MANDATORY group after ``auth``. Before 0.20.0 the
only mandatory group was ``auth``, and ``_should_send`` compared against that
string literal — so any group added beside it was switch-off-able by default.
A receipt for money the platform took is not platform news: a person who
turned ``email_system`` off asked not to hear about features, not to stop
being told when their card is charged. ``test_a_receipt_reaches_someone_who_
turned_system_mail_off`` is the test that would have caught shipping these
three under ``system``.

I18N

en lives in ``translation_keys.NOTIFICATION_KEYS`` (which is also the gettext
msgid); ru and es live in this package's own ``locale/`` catalogues, so a
deployment with no translate service still sends a receipt in the reader's
language. The locale tests below read those catalogues, not fixtures invented
here — a translation test that supplies its own translations proves the
substitution works and nothing about whether the letters are translated.
"""
import re

import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import (
    NotificationLog,
    UserContact,
    UserNotificationSettings,
)
from stapel_notifications.routing import (
    MANDATORY_GROUPS,
    get_group,
    may_carry_unsubscribe,
)
from stapel_notifications.services import process_notification

CYRILLIC = re.compile(r"[А-Яа-яЁё]")

TYPES = (
    "billing.payment_succeeded",
    "billing.payment_failed",
    "billing.subscription_ending",
)


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


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    with override_settings(
        STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": CAPTURE}
    ):
        yield _CapturingEmailProvider.sent


RECEIPT = {
    "amount": "$21.00",
    "item_name": "Pro",
    "period_start": "2026-09-09",
    "period_end": "2026-10-09",
    "invoice_url": "https://invoice.example/i/abc",
}

DECLINE = {
    "amount": "$15.00",
    "item_name": "Pro",
    "decline_reason": "the card did not have enough available funds",
    "retry_url": "https://app.example/billing",
}

ENDING = {
    "item_name": "Pro",
    "period_end": "2026-09-28",
    "resubscribe_url": "https://app.example/billing",
}


def _text(html: str) -> str:
    return re.sub(r"<[^>]*>", " ", html)


# ── The classification ──────────────────────────────────────────


class TestTheseLettersCannotBeSwitchedOff:

    @pytest.mark.parametrize("ntype", TYPES)
    def test_the_type_is_in_the_billing_group(self, ntype):
        assert get_group(ntype) == "billing"

    def test_the_billing_group_is_mandatory(self):
        assert "billing" in MANDATORY_GROUPS

    @pytest.mark.parametrize("ntype", TYPES)
    def test_no_unsubscribe_affordance_is_offered(self, ntype):
        """A one-click opt-out on a receipt is an opt-out from being told
        about charges. RFC 8058 one-click can be pressed by a scanner."""
        assert may_carry_unsubscribe(ntype) is False

    def test_a_mandatory_group_mints_no_preference_field(self):
        """Which is why this needed no column and no migration: there is
        nothing to ask the recipient."""
        from stapel_notifications.services import valid_pref_fields

        assert not [f for f in valid_pref_fields() if f.endswith("_billing")]


@pytest.mark.django_db
class TestAReceiptReachesSomeoneWhoTurnedOtherMailOff:

    def test_a_receipt_reaches_someone_who_turned_system_mail_off(
        self, user, capture_email
    ):
        """THE test for the group choice. Under `system` this sends nothing."""
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        UserNotificationSettings.objects.create(
            user_id=user.id, email_system=False, email_messages=False
        )

        process_notification(
            notification_type="billing.payment_succeeded",
            user_id=str(user.id),
            variables=dict(RECEIPT),
        )

        assert len(capture_email) == 1, (
            "a payer who switched off system mail was not sent their receipt"
        )

    def test_the_same_recipient_still_does_not_get_system_mail(
        self, user, capture_email
    ):
        """The mirror, so the test above cannot pass by ignoring preferences."""
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        UserNotificationSettings.objects.create(
            user_id=user.id, email_system=False
        )

        process_notification(
            notification_type="listing_expiring",
            user_id=str(user.id),
            variables={"listing_title": "x", "days_remaining": "3"},
        )

        assert capture_email == []


# ── What each letter actually says ──────────────────────────────


@pytest.mark.django_db
class TestTheReceipt:

    def _send(self, user, capture_email, **overrides):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        variables = dict(RECEIPT)
        variables.update(overrides)
        process_notification(
            notification_type="billing.payment_succeeded",
            user_id=str(user.id),
            variables=variables,
        )
        return capture_email[0]

    def test_it_states_the_amount_and_what_was_bought(self, user, capture_email):
        mail = self._send(user, capture_email)
        body = _text(mail["html"])

        assert "$21.00" in body
        assert "Pro" in body
        assert "$21.00" in mail["subject"]

    def test_it_states_the_period_a_subscription_covers(self, user, capture_email):
        body = _text(self._send(user, capture_email)["html"])

        assert "2026-09-09" in body
        assert "2026-10-09" in body

    def test_it_links_the_invoice(self, user, capture_email):
        assert "https://invoice.example/i/abc" in self._send(
            user, capture_email
        )["html"]

    def test_a_purchase_with_no_period_renders_no_period_line(
        self, user, capture_email
    ):
        """A top-up covers no month; the sentence must not appear half-filled."""
        mail = self._send(user, capture_email, period_start="", period_end="")
        body = _text(mail["html"])

        assert "{period_end}" not in body
        assert "covers the period" not in body
        assert "$21.00" in body, "the receipt itself still went out"

    def test_a_payment_with_no_document_renders_no_button(
        self, user, capture_email
    ):
        mail = self._send(user, capture_email, invoice_url="")

        assert "View your invoice" not in _text(mail["html"])
        assert "$21.00" in _text(mail["html"])


@pytest.mark.django_db
class TestTheDeclineNotice:

    def _send(self, user, capture_email, **overrides):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        variables = dict(DECLINE)
        variables.update(overrides)
        process_notification(
            notification_type="billing.payment_failed",
            user_id=str(user.id),
            variables=variables,
        )
        return capture_email[0]

    def test_it_says_what_the_bank_said_in_words(self, user, capture_email):
        body = _text(self._send(user, capture_email)["html"])

        assert "did not have enough available funds" in body

    def test_it_offers_the_way_to_fix_it(self, user, capture_email):
        assert "https://app.example/billing" in self._send(
            user, capture_email
        )["html"]

    def test_with_no_reason_the_reason_line_is_absent(self, user, capture_email):
        """Not 'Reason given by your bank:' followed by nothing."""
        body = _text(self._send(user, capture_email, decline_reason="")["html"])

        assert "Reason given by your bank" not in body
        assert "$15.00" in body


@pytest.mark.django_db
class TestTheSubscriptionEndingNotice:

    def _send(self, user, capture_email, **overrides):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        variables = dict(ENDING)
        variables.update(overrides)
        process_notification(
            notification_type="billing.subscription_ending",
            user_id=str(user.id),
            variables=variables,
        )
        return capture_email[0]

    def test_it_names_the_date_access_stops(self, user, capture_email):
        mail = self._send(user, capture_email)

        assert "2026-09-28" in _text(mail["html"])
        assert "2026-09-28" in mail["subject"]

    def test_it_offers_the_way_back(self, user, capture_email):
        body = _text(self._send(user, capture_email)["html"])

        assert "Keep my subscription" in body


# ── i18n ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTheLettersAreWrittenInTheRecipientsLanguage:
    """Reads this package's own locale catalogues — the ru and es a
    deployment gets with no translate service at all."""

    def _send(self, user, capture_email, ntype, variables, language):
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type=ntype,
            user_id=str(user.id),
            variables=dict(variables),
            language=language,
        )
        return capture_email[0]

    def test_the_receipt_in_russian(self, user, capture_email):
        mail = self._send(
            user, capture_email, "billing.payment_succeeded", RECEIPT, "ru"
        )
        body = _text(mail["html"])

        assert CYRILLIC.search(mail["subject"]), mail["subject"]
        assert "Платёж получен" in mail["subject"]
        assert "Мы получили ваш платёж" in body
        assert "$21.00" in body, "the amount is not translated, it is data"

    def test_the_receipt_in_spanish(self, user, capture_email):
        mail = self._send(
            user, capture_email, "billing.payment_succeeded", RECEIPT, "es"
        )
        body = _text(mail["html"])

        assert "Pago recibido" in mail["subject"]
        assert "Hemos recibido tu pago" in body
        assert CYRILLIC.search(body) is None

    def test_the_receipt_in_english(self, user, capture_email):
        mail = self._send(
            user, capture_email, "billing.payment_succeeded", RECEIPT, "en"
        )

        assert "Payment received" in mail["subject"]
        assert "We received your payment" in _text(mail["html"])

    @pytest.mark.parametrize("language,needle", [
        ("ru", "Не удалось списать оплату"),
        ("es", "No hemos podido cobrar tu pago"),
        ("en", "We could not take your payment"),
    ])
    def test_the_decline_notice_in_each_language(
        self, user, capture_email, language, needle
    ):
        mail = self._send(
            user, capture_email, "billing.payment_failed", DECLINE, language
        )
        assert needle in _text(mail["html"])

    @pytest.mark.parametrize("language,needle", [
        ("ru", "Подписка не будет продлена"),
        ("es", "Tu suscripción no se renovará"),
        ("en", "Your subscription will not renew"),
    ])
    def test_the_ending_notice_in_each_language(
        self, user, capture_email, language, needle
    ):
        mail = self._send(
            user, capture_email, "billing.subscription_ending", ENDING, language
        )
        assert needle in _text(mail["html"])

    def test_every_slot_of_every_billing_letter_has_ru_and_es(self):
        """Parity over the catalogues, so a slot added later cannot ship
        English inside a Russian letter — the 2026-08-08 defect, which was
        one untranslated badge in an otherwise translated template."""
        from django.utils import translation

        from stapel_notifications.translation_keys import (
            NOTIFICATION_KEYS,
            keys_for_type,
        )
        from stapel_notifications.routing import registered_types

        missing = []
        for ntype in TYPES:
            for key in keys_for_type(ntype, known_types=registered_types()):
                if key.startswith("notification.footer."):
                    continue
                english = NOTIFICATION_KEYS[key]
                for language in ("ru", "es"):
                    with translation.override(language):
                        if translation.gettext(english) == english:
                            missing.append(f"{key} [{language}]")
        assert missing == [], f"untranslated billing copy: {missing}"


# ── The journal ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestTheDeliveryIsJournalled:

    def test_a_receipt_leaves_a_row_naming_the_type_and_the_outcome(
        self, user, capture_email
    ):
        """The evidence an operator reads back when a payer says they got
        nothing — and the row that was absent for every one of the six
        charges this release was opened on."""
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        process_notification(
            notification_type="billing.payment_succeeded",
            user_id=str(user.id),
            variables=dict(RECEIPT),
        )

        row = NotificationLog.objects.get(
            notification_type="billing.payment_succeeded"
        )
        assert row.channel == "email"
        assert row.status == "sent"
        assert str(row.user_id) == str(user.id)
