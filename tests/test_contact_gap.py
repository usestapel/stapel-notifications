"""The contact seam: mirror, sentinel, parked retry, reconcile.

The failure these cover, end to end, as it happened: auth stopped announcing
deliverable addresses on every path but one, the mirror here stayed empty,
and transactional mail was journalled ``skipped — no email address for this
recipient`` for four months with no alert, no metric and no way back. Each
class below pins one of the four answers.
"""
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from stapel_notifications import contact_gap
from stapel_notifications.models import (
    NotificationLog,
    ParkedDispatch,
    UserContact,
)


def _event(payload, event_id="evt-1"):
    class _E:
        pass

    e = _E()
    e.payload = payload
    e.event_id = event_id
    return e


def _uid():
    return uuid.uuid4()


# ── the mirror ───────────────────────────────────────────────────────────────


class ContactChangedActionTests(TestCase):
    """The apply half of the projection."""

    def test_creates_the_mirror_row(self):
        from stapel_notifications.actions import handle_user_contact_changed

        uid = _uid()
        handle_user_contact_changed(_event({
            "user_id": str(uid), "email": "a@example.com", "phone": "",
        }))
        contact = UserContact.objects.get(user_id=uid)
        self.assertEqual(contact.email, "a@example.com")
        self.assertTrue(contact.is_active)

    def test_is_idempotent_under_at_least_once_delivery(self):
        from stapel_notifications.actions import handle_user_contact_changed

        uid = _uid()
        payload = {"user_id": str(uid), "email": "a@example.com", "phone": ""}
        handle_user_contact_changed(_event(payload))
        handle_user_contact_changed(_event(payload))
        self.assertEqual(UserContact.objects.filter(user_id=uid).count(), 1)

    def test_an_empty_address_clears_the_mirror(self):
        """An account that gave up its address must stop being written to."""
        from stapel_notifications.actions import handle_user_contact_changed

        uid = _uid()
        UserContact.objects.create(user_id=uid, email="old@example.com")
        handle_user_contact_changed(_event({
            "user_id": str(uid), "email": "", "phone": "",
        }))
        self.assertEqual(UserContact.objects.get(user_id=uid).email, "")

    def test_a_fresh_sync_reactivates_a_closure_deactivated_contact(self):
        from stapel_notifications.actions import handle_user_contact_changed

        uid = _uid()
        UserContact.objects.create(
            user_id=uid, email="a@example.com", is_active=False
        )
        handle_user_contact_changed(_event({
            "user_id": str(uid), "email": "a@example.com", "phone": "",
        }))
        self.assertTrue(UserContact.objects.get(user_id=uid).is_active)

    def test_a_payload_without_a_user_id_is_not_a_crash_loop(self):
        from stapel_notifications.actions import handle_user_contact_changed

        with self.assertLogs("stapel_notifications.actions", "ERROR"):
            handle_user_contact_changed(_event({"email": "a@example.com"}))
        self.assertEqual(UserContact.objects.count(), 0)


# ── the sentinel ─────────────────────────────────────────────────────────────


class SentinelTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_a_skip_for_a_known_account_logs_error_with_the_fingerprint(self):
        with self.assertLogs("stapel_notifications.contact_gap", "ERROR") as cm:
            logged = contact_gap.report_missing_contact(
                notification_type="billing.payment_succeeded",
                channel="email", user_id=_uid(), event_id="evt-9",
            )
        self.assertTrue(logged)
        self.assertIn(contact_gap.FINGERPRINT, cm.output[0])

    def test_it_never_carries_an_address_or_a_name(self):
        with self.assertLogs("stapel_notifications.contact_gap", "ERROR") as cm:
            contact_gap.report_missing_contact(
                notification_type="billing.payment_succeeded",
                channel="email", user_id=_uid(),
            )
        line = cm.output[0]
        self.assertNotIn("@", line.split("event_id=")[0].replace("e-mail", ""))

    def test_an_anonymous_recipient_stays_quiet(self):
        """No user_id is the unauthenticated case — ordinary, not an alert."""
        self.assertFalse(contact_gap.report_missing_contact(
            notification_type="otp_code", channel="sms", user_id=None,
        ))

    def test_it_is_rate_limited_per_type_and_recipient(self):
        uid = _uid()
        kwargs = dict(
            notification_type="billing.payment_succeeded",
            channel="email", user_id=uid,
        )
        with self.assertLogs("stapel_notifications.contact_gap", "ERROR"):
            self.assertTrue(contact_gap.report_missing_contact(**kwargs))
        self.assertFalse(contact_gap.report_missing_contact(**kwargs))
        self.assertFalse(contact_gap.report_missing_contact(**kwargs))
        # A different recipient is a different bucket.
        with self.assertLogs("stapel_notifications.contact_gap", "ERROR"):
            self.assertTrue(contact_gap.report_missing_contact(
                notification_type="billing.payment_succeeded",
                channel="email", user_id=_uid(),
            ))

    def test_the_gauge_counts_the_last_24h_of_address_less_skips(self):
        uid = _uid()
        for _ in range(3):
            NotificationLog.objects.create(
                user_id=uid, notification_type="billing.payment_succeeded",
                channel="email", status="skipped", recipient="",
                error_message="no email address for this recipient",
            )
        NotificationLog.objects.create(
            user_id=uid, notification_type="billing.payment_succeeded",
            channel="email", status="skipped", recipient="",
            error_message="",  # a preference skip — not this class
        )
        old = NotificationLog.objects.create(
            user_id=uid, notification_type="billing.payment_succeeded",
            channel="email", status="skipped", recipient="",
            error_message="no email address for this recipient",
        )
        NotificationLog.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=30)
        )
        self.assertEqual(contact_gap.contacts_missing_count(24), 3)
        self.assertEqual(contact_gap.publish_contacts_missing_gauge(24), 3)


# ── the parked retry ─────────────────────────────────────────────────────────


_RETRY_TYPES = {
    # A host type, with a template, so the dispatch really renders and
    # really reaches the e-mail channel — a retry test that stubbed the
    # channel would prove nothing about the thing being retried.
    "recordings.ready": {
        "channels": ["email"], "group": "system", "transactional": True,
        "template": "notifications/email/gdpr_export_ready.html",
    },
}


@override_settings(STAPEL_NOTIFICATIONS={
    "COMPANY_NAME": "Acme", "TYPES": _RETRY_TYPES,
    "EMAIL_PROVIDER": "mock",
    "RETRY_ON_CONTACT": ["recordings.ready", "billing.payment_"],
    "RETRY_ON_CONTACT_WINDOW_HOURS": 72,
})
class RetryEligibilityTests(TestCase):

    def test_the_allowlisted_types_match_by_prefix(self):
        self.assertTrue(contact_gap.is_retry_eligible("recordings.ready"))
        self.assertTrue(
            contact_gap.is_retry_eligible("billing.payment_succeeded")
        )

    def test_an_unlisted_type_is_not_eligible(self):
        self.assertFalse(contact_gap.is_retry_eligible("welcome"))

    def test_a_security_type_is_never_eligible(self):
        """Even named explicitly: a parked passcode is a credential store,
        and a passcode has expired by the time an address arrives."""
        with override_settings(STAPEL_NOTIFICATIONS={
            "COMPANY_NAME": "Acme",
            "RETRY_ON_CONTACT": ["otp_code", "recordings.ready"],
        }):
            self.assertFalse(contact_gap.is_retry_eligible("otp_code"))

    def test_the_boot_refuses_a_security_type_in_the_allowlist(self):
        from stapel_notifications.checks import (
            check_retry_allowlist_holds_no_security_type,
        )

        with override_settings(STAPEL_NOTIFICATIONS={
            "COMPANY_NAME": "Acme", "RETRY_ON_CONTACT": ["otp_code"],
        }):
            errors = check_retry_allowlist_holds_no_security_type(None)
        self.assertEqual([e.id for e in errors], ["stapel_notifications.E008"])

    def test_a_clean_allowlist_boots(self):
        from stapel_notifications.checks import (
            check_retry_allowlist_holds_no_security_type,
        )

        self.assertEqual(
            check_retry_allowlist_holds_no_security_type(None), []
        )


@override_settings(STAPEL_NOTIFICATIONS={
    "COMPANY_NAME": "Acme", "TYPES": _RETRY_TYPES,
    "EMAIL_PROVIDER": "mock",
    "RETRY_ON_CONTACT": ["recordings.ready", "billing.payment_"],
    "RETRY_ON_CONTACT_WINDOW_HOURS": 72,
})
class ParkAndReplayTests(TestCase):

    def setUp(self):
        cache.clear()
        self.uid = _uid()

    def _skip(self, ntype="recordings.ready"):
        """Dispatch with no address: the journal row + the parked request."""
        from stapel_notifications.services import process_notification

        process_notification(
            notification_type=ntype,
            user_id=str(self.uid),
            variables={"title": "Q3 review"},
            event_id=f"evt-{uuid.uuid4().hex[:8]}",
        )
        return NotificationLog.objects.get(
            user_id=self.uid, notification_type=ntype, status="skipped"
        )

    def test_a_skip_parks_the_request(self):
        log = self._skip()
        parked = ParkedDispatch.objects.get(log_id=log.id)
        self.assertEqual(parked.notification_type, "recordings.ready")
        self.assertEqual(parked.request["variables"], {"title": "Q3 review"})

    def test_an_ineligible_type_parks_nothing(self):
        with override_settings(STAPEL_NOTIFICATIONS={
            "COMPANY_NAME": "Acme", "TYPES": _RETRY_TYPES,
            "EMAIL_PROVIDER": "mock", "RETRY_ON_CONTACT": [],
        }):
            self._skip()
        self.assertEqual(ParkedDispatch.objects.count(), 0)

    def test_parking_is_idempotent_on_the_log_row(self):
        log = self._skip()
        contact_gap.park_dispatch(
            log=log, user_id=self.uid, notification_type="recordings.ready",
            channel="email", event_id="x", language="en", request={},
        )
        self.assertEqual(ParkedDispatch.objects.filter(log_id=log.id).count(), 1)

    def test_replay_sends_once_and_flips_the_journal_row(self):
        log = self._skip()
        UserContact.objects.create(user_id=self.uid, email="a@example.com")
        parked = ParkedDispatch.objects.get(log_id=log.id)

        self.assertEqual(contact_gap.replay(parked), "resent")
        log.refresh_from_db()
        self.assertEqual(log.status, "sent")
        self.assertTrue(log.data.get("resent_from_skipped"))
        self.assertEqual(ParkedDispatch.objects.count(), 0)

    def test_a_second_resend_pass_finds_nothing(self):
        log = self._skip()
        UserContact.objects.create(user_id=self.uid, email="a@example.com")
        contact_gap.replay(ParkedDispatch.objects.get(log_id=log.id))
        self.assertEqual(list(contact_gap.replayable()), [])

    def test_still_unreachable_stays_parked(self):
        log = self._skip()
        parked = ParkedDispatch.objects.get(log_id=log.id)
        self.assertEqual(contact_gap.replay(parked), "still_unreachable")
        self.assertEqual(ParkedDispatch.objects.count(), 1)

    def test_rows_past_the_window_expire_unsent(self):
        log = self._skip()
        ParkedDispatch.objects.filter(log_id=log.id).update(
            created_at=timezone.now() - timezone.timedelta(hours=73)
        )
        self.assertEqual(list(contact_gap.replayable()), [])
        self.assertEqual(contact_gap.expire_parked(), 1)
        self.assertEqual(ParkedDispatch.objects.count(), 0)

    def test_a_row_inside_the_window_does_not_expire(self):
        log = self._skip()
        ParkedDispatch.objects.filter(log_id=log.id).update(
            created_at=timezone.now() - timezone.timedelta(hours=71)
        )
        self.assertEqual(contact_gap.expire_parked(), 0)
        self.assertEqual(len(list(contact_gap.replayable())), 1)

    def test_an_erasure_takes_the_parked_row_with_it(self):
        from stapel_notifications.erasure import erase_account

        self._skip()
        receipt = erase_account(self.uid)
        self.assertEqual(receipt["parked_dispatches"], 1)
        self.assertEqual(ParkedDispatch.objects.count(), 0)


# ── the reconcile command ────────────────────────────────────────────────────


@override_settings(STAPEL_NOTIFICATIONS={
    "COMPANY_NAME": "Acme", "TYPES": _RETRY_TYPES,
    "EMAIL_PROVIDER": "mock",
    "RETRY_ON_CONTACT": ["recordings.ready", "billing.payment_"],
    "RETRY_ON_CONTACT_WINDOW_HOURS": 72,
})
class ReconcileCommandTests(TestCase):

    def setUp(self):
        cache.clear()
        self.uid = _uid()

    def _pages(self, *pages):
        """A fake ``auth.contacts_page`` that walks *pages* then stops.

        Every other Function name falls through to the real registry — a
        resend really dispatches, and that reaches ``profiles.language``.
        """
        import stapel_core.comm as comm

        seq = list(pages)
        real = comm.call

        def _call(name, payload=None, **kwargs):
            if name != "auth.contacts_page":
                return real(name, payload, **kwargs)
            return seq.pop(0) if seq else {"contacts": [], "next": None}

        return _call

    def _run(self, *pages, **opts):
        import io

        out = io.StringIO()
        with patch("stapel_core.comm.call", self._pages(*pages)):
            call_command(
                "notifications_reconcile_contacts", stdout=out, **opts
            )
        return out.getvalue()

    def test_dry_run_reports_and_writes_nothing(self):
        page = {"contacts": [
            {"user_id": str(self.uid), "email": "a@example.com", "phone": ""},
        ], "next": None}
        output = self._run(page, dry_run=True)
        self.assertIn("created                : 1", output)
        self.assertIn("dry run", output)
        self.assertEqual(UserContact.objects.count(), 0)

    def test_it_creates_updates_and_leaves_unchanged(self):
        other = _uid()
        stale = _uid()
        UserContact.objects.create(user_id=stale, email="old@example.com")
        UserContact.objects.create(user_id=other, email="same@example.com")
        page = {"contacts": [
            {"user_id": str(self.uid), "email": "new@example.com", "phone": ""},
            {"user_id": str(stale), "email": "fixed@example.com", "phone": ""},
            {"user_id": str(other), "email": "same@example.com", "phone": ""},
        ], "next": None}
        output = self._run(page)
        self.assertIn("created                : 1", output)
        self.assertIn("updated                : 1", output)
        self.assertIn("unchanged              : 1", output)
        self.assertEqual(
            UserContact.objects.get(user_id=stale).email, "fixed@example.com"
        )

    def test_it_follows_the_keyset_cursor(self):
        second = _uid()
        output = self._run(
            {"contacts": [{"user_id": str(self.uid), "email": "a@example.com",
                           "phone": ""}], "next": str(self.uid)},
            {"contacts": [{"user_id": str(second), "email": "b@example.com",
                           "phone": ""}], "next": None},
        )
        self.assertIn("upstream contacts read : 2", output)
        self.assertEqual(UserContact.objects.count(), 2)

    def test_it_never_deletes_a_row_the_source_did_not_mention(self):
        """A reconciler that deletes on 'I did not see it' is an outage."""
        orphan = _uid()
        UserContact.objects.create(user_id=orphan, email="o@example.com")
        output = self._run({"contacts": [], "next": None})
        self.assertIn("missing upstream       : 1", output)
        self.assertTrue(UserContact.objects.filter(user_id=orphan).exists())

    def test_it_prints_no_address(self):
        page = {"contacts": [
            {"user_id": str(self.uid), "email": "secret@example.com",
             "phone": "+70000000000"},
        ], "next": None}
        output = self._run(page)
        self.assertNotIn("secret@example.com", output)
        self.assertNotIn("+70000000000", output)

    def test_resend_dry_run_then_apply(self):
        from stapel_notifications.services import process_notification

        process_notification(
            notification_type="recordings.ready",
            user_id=str(self.uid),
            variables={"title": "Q3 review"},
            event_id="evt-resend",
        )
        log = NotificationLog.objects.get(user_id=self.uid, status="skipped")
        page = {"contacts": [
            {"user_id": str(self.uid), "email": "a@example.com", "phone": ""},
        ], "next": None}

        # The order an operator runs it in: repair the mirror, THEN preview
        # the resend. A dry-run reconcile writes no contact, so a resend
        # preview stacked on it can only report "still unreachable" — it
        # reports what it can see, not what the write would have enabled.
        self._run(page)
        dry = self._run(page, dry_run=True, resend_skipped=True)
        self.assertIn("resent                 : 1", dry)
        log.refresh_from_db()
        self.assertEqual(log.status, "skipped")

        real = self._run(page, resend_skipped=True)
        self.assertIn("resent                 : 1", real)
        log.refresh_from_db()
        self.assertEqual(log.status, "sent")

        again = self._run(page, resend_skipped=True)
        self.assertIn("resent                 : 0", again)

    def test_an_unreachable_function_fails_loudly(self):
        from django.core.management.base import CommandError
        from stapel_core.comm.exceptions import FunctionNotRegistered

        def _boom(name, payload=None, **kwargs):
            raise FunctionNotRegistered(name)

        with patch("stapel_core.comm.call", _boom):
            with self.assertRaises(CommandError):
                call_command("notifications_reconcile_contacts")
