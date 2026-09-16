"""A delivery log is full of personal data, and `view_` must not mean all of it.

Every admin here declared `list_display` and `search_fields` and neither
`fields` nor `exclude`, so the detail page rendered every column — and in this
app the columns ARE the personal data: who was written to, and what was said.

A fleet audited 2026-09-16 wanted to give operators a read of the delivery log
("did the receipt go out, did it bounce") without handing them every
customer's address and the text of every letter. There was no way for a
permission fixture to mean less than "all of it", so it granted nothing and
the operators got nothing. These pin the safe default, and the escape hatch.
"""
import pytest
from django.contrib import admin as dj_admin

from stapel_notifications.admin import (
    DevicePushTokenAdmin,
    NotificationDeliveryAdmin,
    NotificationLogAdmin,
    UserContactAdmin,
)
from stapel_notifications.models import (
    DevicePushToken,
    NotificationDelivery,
    NotificationLog,
    UserContact,
)


def _excluded(admin_cls, model):
    return set(admin_cls(model, dj_admin.AdminSite()).get_exclude(request=None) or ())


def _rendered(admin_cls, model):
    return {f.name for f in model._meta.fields} - _excluded(admin_cls, model)


class TestAddressesAreNotRendered:
    @pytest.mark.parametrize(
        "admin_cls,model,field",
        [
            (NotificationLogAdmin, NotificationLog, "recipient"),
            (NotificationDeliveryAdmin, NotificationDelivery, "recipient"),
        ],
    )
    def test_the_recipient_is_hidden(self, admin_cls, model, field):
        assert field in _excluded(admin_cls, model)

    def test_a_contact_row_shows_no_address_at_all(self):
        excluded = _excluded(UserContactAdmin, UserContact)
        assert {"email", "phone", "telegram_chat_id"} <= excluded
        # What is left is the operational question.
        assert "is_active" in _rendered(UserContactAdmin, UserContact)

    def test_a_push_token_is_a_credential_for_someones_device(self):
        assert "token" in _excluded(DevicePushTokenAdmin, DevicePushToken)


class TestLetterContentIsNotRendered:
    def test_the_title_body_and_payload_are_hidden(self):
        assert {"title", "body", "data"} <= _excluded(
            NotificationLogAdmin, NotificationLog
        )

    def test_but_delivery_state_IS_rendered(self):
        # Otherwise this is "grant nothing" with extra steps.
        rendered = _rendered(NotificationLogAdmin, NotificationLog)
        assert {"status", "channel", "notification_type", "created_at"} <= rendered


class TestTheListAndTheSearchLeakNothingEither:
    def test_no_address_is_printed_in_a_results_table(self):
        # Printing an address in a list is the same disclosure as printing it
        # on a page.
        for admin_cls in (NotificationLogAdmin, NotificationDeliveryAdmin):
            assert "recipient" not in admin_cls.list_display
        assert "email" not in UserContactAdmin.list_display
        assert "phone" not in UserContactAdmin.list_display

    def test_and_none_is_searchable(self):
        # Searching BY an address is worse than showing one: it confirms a
        # guess. The user id reaches the same row.
        assert "recipient" not in NotificationLogAdmin.search_fields
        assert "user_id" in NotificationLogAdmin.search_fields
        assert "recipient" not in NotificationDeliveryAdmin.search_fields
        assert "token" not in DevicePushTokenAdmin.search_fields
        for field in ("email", "phone", "telegram_chat_id"):
            assert field not in UserContactAdmin.search_fields


class TestAHostCanStillDecideOtherwise:
    def test_narrowing_the_tuple_re_renders_the_field(self):
        class HostLogAdmin(NotificationLogAdmin):
            PERSONAL_FIELDS = ("body",)

        rendered = _rendered(HostLogAdmin, NotificationLog)
        assert "recipient" in rendered
        assert "body" not in rendered

    def test_the_default_stays_closed_for_everyone_else(self):
        assert "recipient" in _excluded(NotificationLogAdmin, NotificationLog)
