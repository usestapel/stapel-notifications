"""The erasure receipt, and the probe that proves the path is consumed.

This module has always erased on ``user.deleted`` and always said nothing
about it — the "silent owner" finding. stapel-gdpr's orchestrator does not
self-certify: an ``ErasurePart`` with no receipt keeps the request in
``erasing`` until it times out thirty days later, which is
indistinguishable from an owner whose consumer was never deployed. The pins
here are, in order:

* every erasure answers ``gdpr.section.erased`` with **counts** — "it ran"
  and "it removed a contact, two push tokens and three delivery claims, and
  anonymised four journal rows" are different claims, and only the second
  can be audited;
* the delivery ledger, which is keyed by the raw address and carries no
  ``user_id``, is reached through the contact before that contact is
  destroyed — the one place a lookup order is load-bearing;
* a redelivery erases nothing and still receipts;
* the probe is answered **from this same module**, which is the only reason
  ``gdpr.owner.alive`` is evidence about the erasure path rather than about
  a running container.
"""

import json
import types
import uuid
from pathlib import Path

import jsonschema
import pytest

from stapel_core.comm import subscribe_action
from stapel_notifications.actions import (
    handle_erasure_requested,
    handle_owner_probe,
    handle_user_deleted,
)
from stapel_notifications.erasure import (
    GDPR_OWNER,
    GDPR_SUBJECT_TYPES,
    erase_account,
)
from stapel_notifications.models import (
    DevicePushToken,
    NotificationDelivery,
    NotificationLog,
    UserContact,
    UserNotificationSettings,
)

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


def _validate(payload: dict, name: str) -> None:
    jsonschema.validate(
        payload,
        json.loads((SCHEMAS / "emits" / f"{name}.json").read_text()),
        format_checker=jsonschema.FormatChecker(),
    )


def _request(subject_type, subject_key, correlation_id=None):
    return types.SimpleNamespace(
        payload={
            "correlation_id": str(correlation_id or uuid.uuid4()),
            "subject_type": subject_type,
            "subject_key": str(subject_key),
        },
        event_id="evt-1",
        service="gdpr",
    )


@pytest.fixture
def receipts():
    events = []
    subscribe_action("gdpr.section.erased", events.append)
    return events


@pytest.fixture
def alive():
    events = []
    subscribe_action("gdpr.owner.alive", events.append)
    return events


def _seed(user_id):
    """One of everything this module holds about a person."""
    UserContact.objects.create(
        user_id=user_id, email="u@example.com", phone="+45999"
    )
    UserNotificationSettings.objects.create(user_id=user_id, email_system=False)
    DevicePushToken.objects.create(
        user_id=user_id, token=f"tok-{user_id}", platform="ios"
    )
    NotificationDelivery.objects.create(
        event_id="evt-9", channel="email", recipient="u@example.com"
    )
    NotificationDelivery.objects.create(
        event_id="evt-9", channel="sms", recipient="+45999"
    )
    return NotificationLog.objects.create(
        user_id=user_id,
        notification_type="new_message",
        channel="email",
        status="sent",
        language="de",
        recipient="u@example.com",
        title="Hello Ada",
        body="You have a new message",
        error_message="550 no such user u@example.com",
    )


@pytest.mark.django_db
class TestErasure:
    def test_the_addressable_rows_are_destroyed(self):
        uid = uuid.uuid4()
        _seed(uid)

        erase_account(uid)

        assert not UserContact.objects.filter(user_id=uid).exists()
        assert not UserNotificationSettings.objects.filter(user_id=uid).exists()
        assert not DevicePushToken.objects.filter(user_id=uid).exists()

    def test_the_journal_survives_without_the_person(self):
        """A delivery audit trail with a hole in it is not an erasure, it is
        a missing record — so the row stays and every column that names or
        quotes the person goes, ``error_message`` included: a transport's
        reply routinely quotes the address back."""
        uid = uuid.uuid4()
        log = _seed(uid)

        erase_account(uid)

        log.refresh_from_db()
        assert log.status == "sent"
        assert log.notification_type == "new_message"
        assert log.user_id is None
        assert log.recipient == ""
        assert log.title == ""
        assert log.body == ""
        assert log.error_message == ""

    def test_delivery_claims_are_reached_through_the_contact(self):
        """The ledger is keyed by the raw address and has no ``user_id``, so
        the only link from the person to their claims is the contact row
        this erasure is about to destroy."""
        uid = uuid.uuid4()
        _seed(uid)

        counts = erase_account(uid)

        assert NotificationDelivery.objects.count() == 0
        assert counts["delivery_claims"] == 2

    def test_it_counts_what_it_removed(self):
        uid = uuid.uuid4()
        _seed(uid)

        counts = erase_account(uid)

        assert counts == {
            "contacts": 1,
            "push_tokens": 1,
            "settings": 1,
            "delivery_claims": 2,
            "log_rows_anonymized": 1,
        }

    def test_somebody_elses_rows_are_untouched(self):
        doomed = uuid.uuid4()
        kept = uuid.uuid4()
        _seed(doomed)
        UserContact.objects.create(user_id=kept, email="other@example.com")
        NotificationDelivery.objects.create(
            event_id="evt-9", channel="email", recipient="other@example.com"
        )

        erase_account(doomed)

        assert UserContact.objects.filter(user_id=kept).exists()
        assert NotificationDelivery.objects.filter(
            recipient="other@example.com"
        ).exists()


@pytest.mark.django_db
class TestTheReceipt:
    def test_it_receipts_with_counts(self, receipts):
        uid = uuid.uuid4()
        _seed(uid)
        correlation = uuid.uuid4()

        handle_erasure_requested(_request("account", uid, correlation))

        assert len(receipts) == 1
        payload = receipts[0].payload
        assert payload["correlation_id"] == str(correlation)
        assert payload["owner"] == GDPR_OWNER
        assert payload["subject_type"] == "account"
        assert payload["subject_key"] == str(uid)
        assert payload["counts"]["contacts"] == 1
        assert payload["counts"]["log_rows_anonymized"] == 1
        _validate(payload, "gdpr.section.erased")

    def test_a_redelivery_erases_nothing_and_still_receipts(self, receipts):
        """At-least-once delivery: the second copy must not leave the
        orchestrator's part unconfirmed, and must not claim a second
        erasure either."""
        uid = uuid.uuid4()
        _seed(uid)
        event = _request("account", uid)

        handle_erasure_requested(event)
        handle_erasure_requested(event)

        assert len(receipts) == 2
        assert receipts[1].payload["counts"] == {
            "contacts": 0,
            "push_tokens": 0,
            "settings": 0,
            "delivery_claims": 0,
            "log_rows_anonymized": 0,
        }

    def test_a_subject_type_we_do_not_claim_gets_no_receipt(self, receipts):
        """A notification is addressed to a person, never to a workspace. A
        receipt from an owner that erased nothing is worse than silence —
        the orchestrator counts it and finalizes."""
        handle_erasure_requested(_request("workspace", uuid.uuid4()))

        assert receipts == []

    def test_an_unusable_key_gets_no_receipt(self, receipts, caplog):
        with caplog.at_level("ERROR", logger="stapel_notifications.actions"):
            handle_erasure_requested(_request("account", "not-a-uuid"))

        assert receipts == []
        assert any("unusable" in r.message for r in caplog.records)

    def test_a_malformed_request_gets_no_receipt(self, receipts):
        handle_erasure_requested(
            types.SimpleNamespace(
                payload={"subject_type": "account"}, event_id="e9", service="gdpr"
            )
        )

        assert receipts == []


@pytest.mark.django_db
class TestDeprecatedUserDeletedPath:
    """``user.deleted`` fires alongside the new event until gdpr 0.6.0."""

    def test_it_erases_through_the_same_code(self):
        uid = uuid.uuid4()
        _seed(uid)

        handle_user_deleted(
            types.SimpleNamespace(payload={"user_id": uid}, event_id="e1")
        )

        assert not UserContact.objects.filter(user_id=uid).exists()

    def test_it_receipts_when_the_event_carries_a_correlation(self, receipts):
        """The silent-owner finding: this handler erased and said nothing,
        so a host still on the account-only protocol timed out."""
        uid = uuid.uuid4()
        _seed(uid)
        correlation = uuid.uuid4()

        handle_user_deleted(
            types.SimpleNamespace(
                payload={"user_id": str(uid), "correlation_id": str(correlation)},
                event_id="e1",
            )
        )

        assert len(receipts) == 1
        assert receipts[0].payload["correlation_id"] == str(correlation)
        assert receipts[0].payload["subject_type"] == "account"
        _validate(receipts[0].payload, "gdpr.section.erased")

    def test_without_a_correlation_it_erases_and_stays_quiet(self, receipts):
        uid = uuid.uuid4()
        _seed(uid)

        handle_user_deleted(
            types.SimpleNamespace(payload={"user_id": uid}, event_id="e1")
        )

        assert receipts == []


@pytest.mark.django_db
class TestTheProbe:
    def test_it_answers_with_owner_and_subject_types(self, alive):
        handle_owner_probe(
            types.SimpleNamespace(
                payload={"correlation_id": str(uuid.uuid4())},
                event_id="p1",
                service="gdpr",
            )
        )

        assert len(alive) == 1
        payload = alive[0].payload
        assert payload["owner"] == GDPR_OWNER
        assert payload["subject_types"] == list(GDPR_SUBJECT_TYPES)
        _validate(payload, "gdpr.owner.alive")

    def test_the_claimed_types_are_the_ones_the_eraser_handles(self):
        from stapel_notifications.erasure import ERASERS

        assert set(GDPR_SUBJECT_TYPES) == set(ERASERS)

    def test_it_is_answered_from_the_erasure_subscriber(self):
        """Co-location IS the contract: answering the probe from anywhere
        else would make ``alive`` a statement about a deployed container
        rather than about a consumed erasure path."""
        assert (
            handle_owner_probe.__module__
            == handle_erasure_requested.__module__
            == "stapel_notifications.actions"
        )

    def test_the_owner_name_is_the_providers_section(self):
        from stapel_notifications.gdpr import NotificationsGDPRProvider

        assert NotificationsGDPRProvider.section == GDPR_OWNER
