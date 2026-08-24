"""The channel set, opened.

``routing.py`` was always a registry — a host adds a notification TYPE
through ``STAPEL_NOTIFICATIONS["TYPES"]``, no fork. The channel that type
was delivered ON was the opposite: an ``if/elif`` over four hardcoded names
in ``services._dispatch``, mirrored by two more chains for "who is the
recipient" and "which rendering is claimed". A host that wanted an in-app
feed, a webhook or a chat gateway had to patch upstream, or reimplement
``process_notification`` and lose the preference gate, the delivery claim
and the journal with it.

These pin the registry that replaced it — and, just as much, pin that
registering a channel opts out of NOTHING.
"""
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from stapel_notifications.channels.registry import (
    Channel,
    ChannelMessage,
    channels,
    get_channel,
    registered_channels,
)
from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import (
    NotificationLog,
    UserContact,
    UserNotificationSettings,
)
from stapel_notifications.services import (
    _should_send,
    process_notification,
    valid_pref_fields,
)


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


# ── A host channel, written the way a host would write one ──────

delivered = []


def _deliver_webhook(msg: ChannelMessage) -> bool:
    """Deliver to the account's registered endpoint — or nowhere."""
    if not msg.user_id:
        return False
    delivered.append((msg.notification_type, str(msg.user_id), msg.body))
    return True


webhook_channel = Channel(name="webhook", deliver=_deliver_webhook)

HERE = __name__
WEBHOOK = f"{HERE}.webhook_channel"
WEBHOOK_DELIVER = f"{HERE}._deliver_webhook"


@pytest.fixture(autouse=True)
def _clear_delivered():
    delivered.clear()
    yield
    delivered.clear()


def _conf(**extra):
    return {"EMAIL_PROVIDER": "mock", "PUSH_PROVIDER": "mock", **extra}


# ── The registry itself ─────────────────────────────────────────


class TestRegistry:
    def test_builtins_are_the_four_the_chain_carried(self):
        assert registered_channels() == ["email", "push", "sms", "telegram"]

    def test_settings_merge_over_builtins_rather_than_replace_them(self):
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": WEBHOOK}}):
            notifications_settings.reload()
            assert registered_channels() == [
                "email", "push", "sms", "telegram", "webhook",
            ]
            assert get_channel("email") is not None

    def test_a_bare_deliver_callable_is_enough(self):
        with override_settings(
            STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": WEBHOOK_DELIVER}}
        ):
            notifications_settings.reload()
            channel = get_channel("webhook")
            assert channel.deliver is _deliver_webhook
            # No address of its own: an account-addressed channel defaults to
            # the user id, which is what the delivery claim needs.
            assert channel.address(ChannelMessage(
                channel="webhook", notification_type="t", routing={},
                all_vars={}, lang="en", user_id="u-1",
            )) == "u-1"

    def test_a_channel_object_can_be_passed_directly(self):
        with override_settings(
            STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": webhook_channel}}
        ):
            notifications_settings.reload()
            assert get_channel("webhook") is webhook_channel

    def test_the_registry_key_wins_over_the_objects_own_name(self):
        """Registering one Channel under a second name must not shadow it."""
        with override_settings(
            STAPEL_NOTIFICATIONS={"CHANNELS": {"pager": webhook_channel}}
        ):
            notifications_settings.reload()
            assert get_channel("pager").name == "pager"
            assert get_channel("pager").deliver is _deliver_webhook

    def test_a_builtin_can_be_overridden(self):
        with override_settings(
            STAPEL_NOTIFICATIONS={"CHANNELS": {"sms": WEBHOOK_DELIVER}}
        ):
            notifications_settings.reload()
            assert get_channel("sms").deliver is _deliver_webhook

    def test_none_switches_a_builtin_off(self):
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"telegram": None}}):
            notifications_settings.reload()
            assert get_channel("telegram") is None
            assert "telegram" not in registered_channels()
            # Kept as a key: "registered then switched off" is a different
            # fact from "never a channel here".
            assert "telegram" in channels()

    def test_an_unresolvable_path_refuses_rather_than_dropping_the_mail(self):
        with override_settings(
            STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": "myproject.nope.missing"}}
        ):
            notifications_settings.reload()
            with pytest.raises(ImproperlyConfigured):
                get_channel("webhook")

    def test_a_nonsense_entry_refuses(self):
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": 42}}):
            notifications_settings.reload()
            with pytest.raises(ImproperlyConfigured):
                get_channel("webhook")


# ── The preference vocabulary follows the registry ──────────────


class TestPreferenceVocabulary:
    def test_builtin_pairs(self):
        assert valid_pref_fields() == {
            "email_messages", "email_system",
            "push_messages", "push_system",
            "sms_messages", "sms_system",
            "telegram_messages", "telegram_system",
        }

    def test_a_registered_channel_gains_its_pairs(self):
        """Otherwise a host channel is undeliverable by construction:
        _should_send refuses a preference pair it cannot read."""
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": WEBHOOK}}):
            notifications_settings.reload()
            assert "webhook_system" in valid_pref_fields()
            assert "webhook_messages" in valid_pref_fields()

    def test_an_unregistered_channel_has_none(self):
        assert "webhook_system" not in valid_pref_fields()


@pytest.mark.django_db
class TestHostChannelPreference:
    def test_absent_preference_means_opted_in(self, user):
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": WEBHOOK}}):
            notifications_settings.reload()
            settings_obj = UserNotificationSettings.objects.create(user_id=user.id)
            assert _should_send("system", "webhook", settings_obj) is True

    def test_the_recipient_can_switch_it_off(self, user):
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": WEBHOOK}}):
            notifications_settings.reload()
            settings_obj = UserNotificationSettings.objects.create(
                user_id=user.id, channel_preferences={"webhook_system": False},
            )
            assert _should_send("system", "webhook", settings_obj) is False
            # And only that pair — the same channel in another group is
            # a separate switch, exactly like the built-in columns.
            assert _should_send("messages", "webhook", settings_obj) is True

    def test_auth_stays_mandatory_on_a_host_channel(self, user):
        with override_settings(STAPEL_NOTIFICATIONS={"CHANNELS": {"webhook": WEBHOOK}}):
            notifications_settings.reload()
            settings_obj = UserNotificationSettings.objects.create(
                user_id=user.id, channel_preferences={"webhook_auth": False},
            )
            assert _should_send("auth", "webhook", settings_obj) is True

    def test_an_unregistered_channel_is_refused_not_sent(self, user):
        """The unknown case has to be the quiet one: mail nobody can switch
        off is the harm, and 'send' is the half that cannot be taken back."""
        settings_obj = UserNotificationSettings.objects.create(user_id=user.id)
        assert _should_send("system", "webhook", settings_obj) is False


# ── End to end: the seam is only real if the pipeline uses it ───


@pytest.mark.django_db
class TestRegistryDrivesThePipeline:
    def _register(self, **extra):
        return override_settings(STAPEL_NOTIFICATIONS=_conf(
            CHANNELS={"webhook": WEBHOOK},
            TYPES={"invoice_ready": {"channels": ["webhook"], "group": "system"}},
            **extra,
        ))

    def test_a_host_channel_delivers_and_is_journalled(self, user):
        with self._register():
            notifications_settings.reload()
            process_notification(
                notification_type="invoice_ready",
                user_id=str(user.id),
                variables={"body": "Your invoice is ready"},
            )

        assert delivered == [
            ("invoice_ready", str(user.id), "Your invoice is ready"),
        ]
        log = NotificationLog.objects.get(user_id=user.id)
        assert (log.channel, log.status) == ("webhook", "sent")
        assert log.recipient == str(user.id)

    def test_the_preference_gate_still_wraps_it(self, user):
        UserNotificationSettings.objects.create(
            user_id=user.id, channel_preferences={"webhook_system": False},
        )
        with self._register():
            notifications_settings.reload()
            process_notification(
                notification_type="invoice_ready",
                user_id=str(user.id),
                variables={"body": "x"},
            )

        assert delivered == []
        assert NotificationLog.objects.get(user_id=user.id).status == "skipped"

    def test_the_delivery_claim_still_wraps_it(self, user):
        with self._register():
            notifications_settings.reload()
            for _ in range(2):
                process_notification(
                    notification_type="invoice_ready",
                    user_id=str(user.id),
                    variables={"body": "x"},
                    event_id="evt-invoice-1",
                )

        assert len(delivered) == 1
        assert NotificationLog.objects.filter(user_id=user.id).count() == 1

    def test_no_address_is_journalled_as_a_gap_not_a_delivery(self, user, caplog):
        """A channel returning False means "nowhere to send it" — that is a
        reachability gap, and recording it as sent is the lie this pins."""
        with self._register():
            notifications_settings.reload()
            process_notification(
                notification_type="invoice_ready",
                user_id=None,
                email="nobody@example.com",
                variables={"body": "x"},
            )

        assert delivered == []
        log = NotificationLog.objects.get()
        assert log.status == "skipped"

    def test_a_builtin_override_replaces_the_delivery(self, user):
        """Same routing, same templates, different wire."""
        UserContact.objects.create(user_id=user.id, email="u@example.com")
        with override_settings(STAPEL_NOTIFICATIONS=_conf(
            CHANNELS={"email": WEBHOOK_DELIVER},
        )):
            notifications_settings.reload()
            process_notification(
                notification_type="report_reviewed",  # system: push + email
                user_id=str(user.id),
                variables={"body": "reviewed"},
            )

        assert [d[0] for d in delivered] == ["report_reviewed"]
        statuses = {
            log.channel: log.status
            for log in NotificationLog.objects.filter(user_id=user.id)
        }
        assert statuses == {"email": "sent", "push": "sent"}
