"""``dedup_key`` on ``notification.requested``: one delivery per window.

``request_notification(..., dedup_key=, dedup_ttl=)`` (stapel-core 0.95.3)
carries the key on the payload; the consumer is the choke point every
transport calls, so the claim is made there and callers stop hand-rolling
``cache.add`` debounces and sent-at columns.
"""
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import override_settings

from stapel_core.bus import Event
from stapel_core.kafka.events import EventType

from stapel_notifications.intake import dedup_cache_key, handle_notification_requested


def _event(**payload):
    return Event(
        event_type=EventType.NOTIFICATION_REQUESTED,
        service="test",
        payload={"notification_type": "feed.reaction_received", "user_id": "u-1", **payload},
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def delivered():
    with mock.patch("stapel_notifications.services.process_notification") as process:
        yield process


def test_same_key_within_the_window_is_delivered_once(delivered):
    for _ in range(3):
        handle_notification_requested(_event(dedup_key="post:7", dedup_ttl=300))
    assert delivered.call_count == 1


def test_different_keys_are_independent(delivered):
    handle_notification_requested(_event(dedup_key="post:7", dedup_ttl=300))
    handle_notification_requested(_event(dedup_key="post:8", dedup_ttl=300))
    assert delivered.call_count == 2


def test_the_key_is_scoped_by_notification_type(delivered):
    handle_notification_requested(_event(dedup_key="r:1"))
    handle_notification_requested(
        _event(notification_type="reservations.reminder", dedup_key="r:1")
    )
    assert delivered.call_count == 2


def test_no_key_is_never_deduplicated(delivered):
    handle_notification_requested(_event())
    handle_notification_requested(_event())
    assert delivered.call_count == 2


def test_the_window_is_the_callers_ttl():
    with mock.patch("django.core.cache.cache.add", return_value=True) as add, \
            mock.patch("stapel_notifications.services.process_notification"):
        handle_notification_requested(_event(dedup_key="post:7", dedup_ttl=42))
    add.assert_called_once_with(dedup_cache_key("feed.reaction_received", "post:7"), 1, 42)


@override_settings(STAPEL_NOTIFICATIONS={"DEDUP_DEFAULT_TTL": 77})
def test_without_ttl_the_default_window_applies():
    with mock.patch("django.core.cache.cache.add", return_value=True) as add, \
            mock.patch("stapel_notifications.services.process_notification"):
        handle_notification_requested(_event(dedup_key="post:7"))
    assert add.call_args.args[2] == 77


def test_a_failed_delivery_releases_the_claim(delivered):
    delivered.side_effect = [RuntimeError("provider down"), None]
    with pytest.raises(RuntimeError):
        handle_notification_requested(_event(dedup_key="post:7", dedup_ttl=300))
    handle_notification_requested(_event(dedup_key="post:7", dedup_ttl=300))
    assert delivered.call_count == 2


def test_a_malformed_ttl_falls_back_to_the_default(delivered):
    handle_notification_requested(_event(dedup_key="post:7", dedup_ttl="soon"))
    handle_notification_requested(_event(dedup_key="post:7", dedup_ttl=-1))
    assert delivered.call_count == 1


def test_dummy_cache_is_a_boot_warning():
    from stapel_notifications.checks import check_dedup_cache

    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    ):
        ids = [m.id for m in check_dedup_cache(None)]
    assert ids == ["stapel_notifications.W007"]
    assert check_dedup_cache(None) == []


@pytest.mark.django_db
def test_request_notification_dedup_key_end_to_end(django_capture_on_commit_callbacks):
    """The seam: core's publisher carries the key, this consumer honours it."""
    from stapel_core.notifications import request_notification

    from stapel_notifications.models import NotificationLog

    def send():
        return request_notification(
            "gdpr.export_ready",
            email="dest@example.com",
            variables={"download_url": "https://x/dl"},
            source_service="test",
            dedup_key="export:1",
            dedup_ttl=600,
        )

    with override_settings(STAPEL_NOTIFICATIONS={"EMAIL_PROVIDER": "mock"}):
        with django_capture_on_commit_callbacks(execute=True):
            assert send() is True
            assert send() is True
    assert NotificationLog.objects.filter(notification_type="gdpr.export_ready").count() == 1
