"""Read state: the column, the envelope count, and the mark-as-read write.

The feed used to record only what was SENT, so a bell could not tell an
arrived notification from one the person had already looked at — every client
either invented its own local "seen" set (wrong on a second device) or showed
a badge that never cleared. These tests pin the three halves of the fix that a
client actually renders: `read_at` on the row, `unread_count` on the page
envelope, and `POST feed/read/` as an idempotent write scoped to the caller.
"""

import uuid

import pytest

from stapel_notifications.models import NotificationLog

pytestmark = pytest.mark.django_db


def _log(user_id, **kwargs):
    defaults = {
        "notification_type": "new_message",
        "channel": "push",
        "status": "sent",
        "language": "en",
        "recipient": str(user_id),
        "title": "New message",
        "body": "You have mail",
    }
    defaults.update(kwargs)
    return NotificationLog.objects.create(user_id=user_id, **defaults)


# ── the row and the envelope ────────────────────────────────────────────


def test_a_fresh_row_is_unread(authed_client, user):
    _log(user.id)

    body = authed_client.get("/feed/").json()

    assert body["items"][0]["read_at"] is None, "unread is the state a row is born in"
    assert body["unread_count"] == 1


def test_unread_count_is_the_whole_feed_not_the_page(authed_client, user):
    for _ in range(3):
        _log(user.id)

    body = authed_client.get("/feed/?limit=1").json()

    assert len(body["items"]) == 1
    assert body["unread_count"] == 3, (
        "a badge counts the feed, not the page it happens to have fetched"
    )


def test_unread_count_ignores_rows_the_feed_does_not_show(authed_client, user, other_user):
    _log(user.id)
    _log(user.id, status="failed")
    _log(user.id, status="skipped")
    _log(user.id, channel="email")
    _log(other_user.id)

    body = authed_client.get("/feed/").json()

    assert body["unread_count"] == 1, "the count and the list are the same query"


def test_read_at_travels_back_on_the_row(authed_client, user):
    row = _log(user.id)

    authed_client.post("/feed/read/", {"ids": [str(row.id)]}, format="json")
    item = authed_client.get("/feed/").json()["items"][0]

    row.refresh_from_db()
    assert item["read_at"] == row.read_at.isoformat()


# ── the write ───────────────────────────────────────────────────────────


def test_requires_auth(api_client):
    assert api_client.post("/feed/read/", {"all": True}, format="json").status_code in (
        401,
        403,
    )


def test_marking_ids_marks_exactly_those(authed_client, user):
    first, second = _log(user.id), _log(user.id)

    resp = authed_client.post(
        "/feed/read/", {"ids": [str(first.id)]}, format="json"
    )

    assert resp.status_code == 200
    assert resp.json() == {"marked": 1, "unread_count": 1}
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.read_at is not None
    assert second.read_at is None


def test_marking_is_idempotent(authed_client, user):
    row = _log(user.id)
    payload = {"ids": [str(row.id)]}

    first = authed_client.post("/feed/read/", payload, format="json").json()
    second = authed_client.post("/feed/read/", payload, format="json").json()

    assert first == {"marked": 1, "unread_count": 0}
    assert second == {"marked": 0, "unread_count": 0}, (
        "`marked` reports what CHANGED — a repeat changes nothing"
    )


def test_marking_read_does_not_move_the_timestamp(authed_client, user):
    row = _log(user.id)
    payload = {"ids": [str(row.id)]}

    authed_client.post("/feed/read/", payload, format="json")
    row.refresh_from_db()
    first_read_at = row.read_at

    authed_client.post("/feed/read/", payload, format="json")
    row.refresh_from_db()
    assert row.read_at == first_read_at, "already read is not read again"


def test_all_marks_the_whole_feed(authed_client, user):
    rows = [_log(user.id) for _ in range(3)]

    resp = authed_client.post("/feed/read/", {"all": True}, format="json")

    assert resp.json() == {"marked": 3, "unread_count": 0}
    for row in rows:
        row.refresh_from_db()
        assert row.read_at is not None


def test_all_leaves_non_feed_rows_alone(authed_client, user):
    email_row = _log(user.id, channel="email")
    failed_row = _log(user.id, status="failed")

    authed_client.post("/feed/read/", {"all": True}, format="json")

    email_row.refresh_from_db()
    failed_row.refresh_from_db()
    assert email_row.read_at is None
    assert failed_row.read_at is None, (
        "read state is about the feed; a failed delivery was never shown"
    )


def test_another_accounts_row_is_not_touched(authed_client, user, other_user):
    theirs = _log(other_user.id)

    resp = authed_client.post(
        "/feed/read/", {"ids": [str(theirs.id)]}, format="json"
    )

    assert resp.status_code == 200, "not an oracle: a foreign id is simply not matched"
    assert resp.json()["marked"] == 0
    theirs.refresh_from_db()
    assert theirs.read_at is None


def test_all_only_reaches_the_callers_own_rows(authed_client, user, other_user):
    mine, theirs = _log(user.id), _log(other_user.id)

    authed_client.post("/feed/read/", {"all": True}, format="json")

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.read_at is not None
    assert theirs.read_at is None


def test_an_unknown_id_is_ignored(authed_client, user):
    resp = authed_client.post(
        "/feed/read/", {"ids": [str(uuid.uuid4())]}, format="json"
    )

    assert resp.status_code == 200
    assert resp.json() == {"marked": 0, "unread_count": 0}


# ── the request has to say what it means ────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"ids": []},
        {"all": False},
        {"ids": [], "all": False},
    ],
    ids=["empty", "empty-ids", "all-false", "both-empty"],
)
def test_no_target_is_a_400(authed_client, user, payload):
    _log(user.id)

    resp = authed_client.post("/feed/read/", payload, format="json")

    assert resp.status_code == 400
    assert resp.json()["localizable_error"] == "error.400.read_target_required"


def test_both_targets_is_a_400(authed_client, user):
    row = _log(user.id)

    resp = authed_client.post(
        "/feed/read/", {"ids": [str(row.id)], "all": True}, format="json"
    )

    assert resp.status_code == 400
    assert resp.json()["localizable_error"] == "error.400.read_target_required"
    row.refresh_from_db()
    assert row.read_at is None, "an undecided request writes nothing"


def test_too_many_ids_is_a_400(authed_client, user):
    from stapel_notifications.views import MAX_READ_IDS

    ids = [str(uuid.uuid4()) for _ in range(MAX_READ_IDS + 1)]

    resp = authed_client.post("/feed/read/", {"ids": ids}, format="json")

    assert resp.status_code == 400
    assert resp.json()["localizable_error"] == "error.400.too_many_ids"


def test_a_full_page_of_ids_is_accepted(authed_client, user):
    from stapel_notifications.views import FeedPagination, MAX_READ_IDS

    assert MAX_READ_IDS > FeedPagination.max_page_size, (
        "the cap must not make a single page of the feed unmarkable"
    )
    rows = [_log(user.id) for _ in range(3)]

    resp = authed_client.post(
        "/feed/read/", {"ids": [str(r.id) for r in rows]}, format="json"
    )

    assert resp.json() == {"marked": 3, "unread_count": 0}
