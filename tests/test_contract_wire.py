"""Every response body the contract declares is a body the views actually send.

``docs/schema.json`` is emitted from the views' ``@extend_schema``
annotations, and an annotation is a CLAIM: it says what the view returns, and
the generator has no way to check it against the method body.
``tests/test_contract.py`` compares the committed document against a FRESH
EMISSION of the same annotations — it proves the file is not stale, and
nothing else, because both sides come from the claim. stapel-alerts 0.2.0
shipped ``GET /issues`` declared as ``Issue[]`` while the wire carried
``{count, offset, limit, results}``: the drift gate was green and the
frontend pair rendered ``undefined``.

This is the gate the generator cannot be: it performs every operation the
committed schema declares with a JSON response body, and validates the body
it gets against the schema it was promised.

Rules this file holds itself to:

* an operation with a declared JSON response and no entry in ``RECIPES``
  FAILS LOUDLY — a gate that quietly covers three of four rows is the family
  of green that proves nothing;
* a path parameter the gate cannot fill fails at the point of substitution,
  naming the operation;
* the operations that genuinely cannot be driven in-process are listed by
  name in ``UNDRIVABLE`` with a one-line reason each. That list is asserted
  to be exactly current: a stale entry, or a missing reason, fails;
* a collection that comes back empty fails in the populated pass — an empty
  array validates against any item schema, so an empty answer is a check
  that looked at nothing. That covers the nested ``items`` of the feed
  envelope, which a generic "is the body a list" check cannot see;
* every read is driven a SECOND time in its emptiest legal state
  (``EMPTY_STATE``): a feed with no rows AND a feed holding exactly one row
  that carries none of its optional values, a device registry with nothing
  in it, a mark-as-read that matches nothing. Every null finding in the
  first wave of this gate was there.

Runs on every interpreter: it reads the committed schema and never emits.

THE MOUNT — and this module is one of the broken ones.
``codegen_urls.py`` mounts ``notifications/api/`` →
``stapel_notifications.urls``, which contributes ``v1/``, so the committed
document is written against ``/notifications/api/v1/…``. The pytest harness
(``_codegen_settings.settings_kwargs``, whose ``root_urlconf`` defaults to
``stapel_notifications.urls_v1``) mounts the v1 URL set **bare**, skipping
both the host's ``notifications/api/`` prefix and the module's own ``v1/``
segment — so the whole existing suite drives ``/feed/``, ``/devices/``,
``/notification-keys/``, and **not one path in the committed contract
resolves under it**. Nothing in this repository had ever driven the document
it ships. That is the same defect five of the first eight libraries in this
wave carried, and ``test_every_declared_path_resolves_under_this_urlconf``
below is what catches it: this module declares the EMISSION mount and
asserts every declared path resolves under it. The library's own urlconf is
left exactly as it is.

What it found on its first run: 5 of 5 operations driven, 0 red. The claims
this module makes about its own wire are honest, in both the populated and
the empty state, including every ``nullable`` one — ``read_at`` on an unread
row, ``next_anchor``/``prev_anchor`` on a single page.
``test_the_gate_is_not_blind`` proves that is a finding rather than a gate
that never looked: it re-validates every driven body against ``{"type":
"string"}`` and requires all of them to fail.

Two things this pass deliberately does not call a lie:

* ``GET /notifications/api/v1/feed/`` annotates ``responses={200:
  FeedItemResponseSerializer(many=True)}`` while the wire sends the anchor
  envelope ``{items, next_anchor, prev_anchor, has_next, has_prev, count,
  unread_count}``. That is the stapel-alerts defect in shape — but NOT here:
  the view also declares ``pagination_class = FeedPagination``, which
  drf-spectacular reads, and the committed document therefore already says
  ``PaginatedFeedItemResponseList``. The annotation and the document
  disagree; the document and the wire do not, and the document is what
  clients are generated from.
* ``GET /notifications/api/v1/notification-keys/`` is annotated
  ``responses={200: dict}`` and emits as ``{"type": "object",
  "additionalProperties": {}}``. The wire does answer a JSON object, so this
  is not a lie — it is a declaration that promises nothing beyond "an
  object", and the strongest statement this gate can make about it is that
  the object arrived. Recorded here so the green on that row is not read as
  more than it is.
"""
import copy
import json
import re
import uuid
from pathlib import Path
from urllib.parse import quote

import jsonschema
import pytest
from django.test import override_settings
from django.urls import include, path as url_path
from rest_framework.test import APIClient

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())

#: The mount the contract is emitted at, reproduced for the test client
#: (``codegen_urls.py``: ``notifications/api/`` → ``stapel_notifications.urls``,
#: which contributes ``v1/``). The suite's own harness mounts
#: ``stapel_notifications.urls_v1`` bare, under which NONE of these paths
#: resolve — see the module docstring.
urlpatterns = [
    url_path("notifications/api/", include("stapel_notifications.urls")),
]

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]

V1 = "/notifications/api/v1"


@pytest.fixture(autouse=True)
def _media_root(tmp_path):
    """Nothing here writes files today; pin the root so nothing ever does.

    ``MEDIA_ROOT`` is unset in this module's harness settings
    (``_codegen_settings.settings_kwargs``), so it defaults to the working
    directory — in stapel-auth that put a data export into the checkout,
    where under a flat package layout a stray directory also shadowed a real
    submodule.
    """
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# The contract side: what the document declares
# ─────────────────────────────────────────────────────────────────────────────


def _json_schema(node):
    """OpenAPI 3.0 → JSON Schema, for the divergence that matters here.

    OAS 3.0 spells "may be null" as ``nullable: true`` beside a ``type``;
    JSON Schema has no such keyword and would refuse the null — which is
    exactly what ``read_at`` answers on every unread row and what both
    anchors answer on a feed that fits in one page. Everything else
    drf-spectacular emits here (``$ref``, ``required``,
    ``additionalProperties``, ``format``) is JSON Schema as written.
    """
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(response_schema):
    root = copy.deepcopy(response_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    """Every ``(method, path, 2xx code, JSON body schema)`` the contract declares.

    The two ``DELETE`` routes are absent on purpose rather than by accident:
    both declare ``204`` with no content at all, so there is no body for this
    gate to check. Every operation that DOES declare one is here.
    """
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for code, response in op.get("responses", {}).items():
                body = (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


# ─────────────────────────────────────────────────────────────────────────────
# The wire side: harness
# ─────────────────────────────────────────────────────────────────────────────


def _unique(prefix):
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def make_user(**kwargs):
    from django.contrib.auth import get_user_model

    defaults = dict(
        username=_unique("wire_"),
        email=f"{_unique('wire-')}@example.com",
        password="wire-contract-password-7",
    )
    defaults.update(kwargs)
    return get_user_model().objects.create_user(**defaults)


def make_staff():
    """``IsStaffUser | IsServiceRequest`` — the human half of the door.

    The key path needs ``ServiceAPIKeyMiddleware`` swapped into MIDDLEWARE,
    and the permission is an OR, so the same view code answers either way.
    """
    return make_user(is_staff=True)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_device(user, platform="ios", is_active=True):
    from stapel_notifications.models import DevicePushToken

    return DevicePushToken.objects.create(
        user_id=user.id,
        token=_unique("wire-device-token-"),
        platform=platform,
        is_active=is_active,
    )


def make_feed_row(user, **kwargs):
    """One delivered push notification — which is what the feed IS.

    ``feed_queryset`` is ``status="sent", channel="push"``; a row written any
    other way is not in the feed and would make a "populated" read look at
    nothing.
    """
    from stapel_notifications.models import NotificationLog

    defaults = dict(
        notification_type="new_message",
        channel="push",
        status="sent",
        language="en",
        recipient=str(user.id),
        title="New message",
        body="You have mail",
        data={"notification_type": "new_message"},
    )
    defaults.update(kwargs)
    return NotificationLog.objects.create(user_id=user.id, **defaults)


def make_bare_feed_row(user):
    """A feed row carrying none of its optional values.

    Blank copy, an empty ``data`` object and ``read_at`` null — the state
    every row is born in, and the one where a ``required`` claim about an
    optional field shows up.
    """
    return make_feed_row(user, title="", body="", data={})


# ─────────────────────────────────────────────────────────────────────────────
# The recipe table
# ─────────────────────────────────────────────────────────────────────────────


class Call:
    """Performs one declared operation, and refuses to guess a path parameter."""

    def __init__(self, method, path):
        self.method = method
        self.path = path

    def __call__(self, client, params=None, data=None, query="", **extra):
        url = self.path
        for name, value in (params or {}).items():
            url = url.replace("{%s}" % name, str(value))
        assert "{" not in url, (
            f"{self.method} {self.path}: a path parameter this gate does not "
            "know how to fill — teach its recipe, or the operation goes unchecked"
        )
        send = getattr(client, self.method.lower())
        if self.method in ("GET", "DELETE"):
            return send(url + query, **extra)
        return send(url + query, data if data is not None else {}, format="json", **extra)


#: How to perform each operation the contract declares with a JSON response
#: body, keyed by ``(METHOD, path template)``. A recipe returns the response it
#: produced, or a list of ``(label, response)`` pairs when one operation has
#: more than one answering state worth asking.
RECIPES = {}

#: The same operations again, in the emptiest state the contract still has to
#: describe: no rows, or the one row the operation addresses carrying none of
#: its optional values. A populated answer cannot say what a field holds when
#: there is nothing to hold, and that is where every null finding in the first
#: wave of this gate was.
EMPTY_STATE = {}


def recipe(method, path, table=None):
    def register(fn):
        target = RECIPES if table is None else table
        key = (method, V1 + path)
        assert key not in target, f"duplicate recipe for {method} {path}"
        target[key] = fn
        return fn

    return register


def empty_state(method, path):
    return recipe(method, path, table=EMPTY_STATE)


#: Operations that cannot be driven in-process, by name and with the reason.
#: A short, visible list is acceptable here; a silent skip is not.
#:
#: EMPTY. Every operation this module declares with a JSON body is reachable
#: from a test client: nothing here dials a push provider on the response
#: path (delivery is a separate pipeline), and the one write that broadcasts
#: a realtime frame does so through a seam that is a no-op when no realtime
#: substrate is installed.
UNDRIVABLE: dict = {}

#: Collections nested inside an object body that must actually carry a row in
#: the populated pass. An empty array validates against any item schema, so a
#: populated run that leaves one empty looked at nothing — and the generic
#: "is the body a list" check cannot see a list one level down.
POPULATED_COLLECTIONS = {
    ("GET", V1 + "/feed/"): ("items",),
}

#: Reads with no emptiest state to drive, by name and with the reason. The
#: default is that every GET is driven twice; an exemption has to say why.
EMPTY_STATE_EXEMPT = {
    ("GET", V1 + "/notification-keys/"):
        "Answers the module-level NOTIFICATION_KEYS constant "
        "(translation_keys.py) — a literal dict compiled into the package. "
        "There is no per-caller or per-row state for it to be empty of, so an "
        "'empty' run would be byte-for-byte the populated one.",
}


# ── devices ──────────────────────────────────────────────────────────────────


@recipe("GET", "/devices/")
def _device_list(call):
    """Both kinds of row a device list holds: one the provider still accepts
    and one it has rejected. ``is_active: false`` rows are returned on
    purpose — hiding them would render a toggle ON for a device that
    receives nothing."""
    owner = make_user()
    make_device(owner, platform="ios")
    make_device(owner, platform="android", is_active=False)
    make_device(make_user())  # somebody else's — the list is scoped by owner

    response = call(client_for(owner))
    # The recipe's own claim, checked: a "populated" state that quietly came
    # back one-sided is a check that looked at half of what it says it did.
    rows = response.json()
    assert {row["is_active"] for row in rows} == {True, False}, rows
    return response


@empty_state("GET", "/devices/")
def _device_list_empty(call):
    """An account that has never registered a handset — the state every
    account is in until the app's first launch."""
    make_device(make_user())
    return call(client_for(make_user()))


@recipe("POST", "/devices/")
def _device_register(call):
    """Three states of the same write, because they take three different
    branches: a first registration, a re-registration of a token the caller
    already holds (``update_or_create``), and a rebinding that first DELETES
    another account's binding for the same physical device."""
    first = make_user()
    returning = make_user()
    taker = make_user()

    held = make_device(returning, platform="ios")
    handed_over = make_device(make_user(), platform="android")

    return [
        (
            "a first registration",
            call(
                client_for(first),
                data={"token": _unique("wire-token-"), "platform": "ios"},
            ),
        ),
        (
            "a re-registration of a token the caller already holds",
            call(client_for(returning), data={"token": held.token, "platform": "ios"}),
        ),
        (
            "a rebinding that removes another account's binding",
            call(
                client_for(taker),
                data={"token": handed_over.token, "platform": "web"},
            ),
        ),
    ]


@empty_state("POST", "/devices/")
def _device_register_empty(call):
    """The very first device of a brand-new account, with an empty registry
    behind it: nothing to rebind, nothing to update."""
    return call(
        client_for(make_user()),
        data={"token": _unique("wire-token-"), "platform": "web"},
    )


# ── the feed ─────────────────────────────────────────────────────────────────


@recipe("GET", "/feed/")
def _feed(call):
    """A page with more behind it, so both anchors are real strings, and a
    mix of read and unread rows so ``read_at`` is exercised as a timestamp
    and as a null in the same answer."""
    from django.utils import timezone

    owner = make_user()
    make_feed_row(owner, data={"notification_type": "new_message"})
    make_feed_row(owner, title="Listing blocked", notification_type="listing_blocked")
    read_row = make_feed_row(owner)
    read_row.read_at = timezone.now()
    read_row.save(update_fields=["read_at"])
    make_feed_row(make_user())  # somebody else's — the feed is scoped by owner

    first_page = call(client_for(owner), query="?limit=2")
    next_anchor = first_page.json()["next_anchor"]
    assert next_anchor, "the populated feed must have a second page to point at"
    # The recipe's own claims, checked. A state this file says it drove and
    # did not is the same family of green as an operation nobody asked.
    seen = call(client_for(owner), query="?limit=50").json()["items"]
    assert any(row["read_at"] for row in seen), seen
    assert any(row["read_at"] is None for row in seen), seen
    assert any(row["data"] for row in seen), (
        "no row survived telemetry scrubbing with a non-empty `data`, so the "
        "populated pass never looked at a filled object"
    )
    # The anchor is an ISO timestamp, so it ends in ``+00:00`` — and ``+`` in a
    # query string decodes to a SPACE, which Django's DateTimeField refuses.
    # Percent-encode it the way a generated client would.
    return [
        ("the first page of two (next_anchor set)", first_page),
        (
            "the second page (prev_anchor set)",
            call(
                client_for(owner),
                query=f"?limit=2&anchor={quote(next_anchor, safe='')}",
            ),
        ),
    ]


@empty_state("GET", "/feed/")
def _feed_empty(call):
    """Two kinds of empty: a feed with no rows at all, and a feed whose only
    row is itself empty — blank copy, ``data: {}``, ``read_at: null``. The
    second is the one that matters: an empty ARRAY validates against any item
    schema, so a collection's emptiest interesting state is one row carrying
    none of its optional values."""
    nobody = make_user()
    owner = make_user()
    make_bare_feed_row(owner)

    nothing = call(client_for(nobody))
    bare = call(client_for(owner))

    # The recipe's own claims, checked.
    assert nothing.json()["items"] == [], nothing.json()
    assert nothing.json()["next_anchor"] is None, nothing.json()
    row = bare.json()["items"][0]
    assert row["read_at"] is None and row["data"] == {} and row["title"] == "", row
    return [
        ("no rows at all", nothing),
        ("one row carrying none of its optional values", bare),
    ]


@recipe("POST", "/feed/read/")
def _feed_read(call):
    """Both targets the write accepts — named ids and ``all: true`` — each
    against a feed that actually has something to mark."""
    by_ids_owner = make_user()
    rows = [make_feed_row(by_ids_owner) for _ in range(3)]
    all_owner = make_user()
    make_feed_row(all_owner)
    make_feed_row(all_owner)

    by_ids = call(
        client_for(by_ids_owner), data={"ids": [str(rows[0].id), str(rows[1].id)]}
    )
    mark_all = call(client_for(all_owner), data={"all": True})

    # The recipe's own claims, checked: a "populated" write that marked
    # nothing is the empty state wearing the populated state's label.
    assert by_ids.json() == {"marked": 2, "unread_count": 1}, by_ids.json()
    assert mark_all.json() == {"marked": 2, "unread_count": 0}, mark_all.json()
    return [
        ("marking named ids", by_ids),
        ("marking everything", mark_all),
    ]


@empty_state("POST", "/feed/read/")
def _feed_read_empty(call):
    """Two writes that change nothing: ``all: true`` on a feed with no rows,
    and a repeat of a call that already marked everything. ``marked`` is the
    number that CHANGED, so both must answer 0 — and both still have to
    carry the declared shape."""
    nobody = make_user()
    repeater = make_user()
    make_feed_row(repeater)
    first = call(client_for(repeater), data={"all": True})
    assert first.status_code == 200, first.content

    nothing = call(client_for(nobody), data={"all": True})
    repeat = call(client_for(repeater), data={"all": True})

    # The recipe's own claim, checked.
    assert nothing.json() == {"marked": 0, "unread_count": 0}, nothing.json()
    assert repeat.json() == {"marked": 0, "unread_count": 0}, repeat.json()
    return [
        ("nothing in the feed at all", nothing),
        ("a repeat of a call that already marked everything", repeat),
    ]


# ── the translation-key listing ──────────────────────────────────────────────


@recipe("GET", "/notification-keys/")
def _notification_keys(call):
    return call(client_for(make_staff()))


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────


#: Operations whose declared body the wire does not send, with the defect and
#: its owner. ``strict=True``: a fixed entry fails until it is deleted, so a
#: finding can be neither forgotten nor quietly kept.
#:
#: EMPTY, and that is the finding rather than the absence of one: 5 of 5
#: declared operations were driven against a real test client in both their
#: populated and their emptiest state, and every declared body arrived as
#: declared. ``test_the_gate_is_not_blind`` is what makes that green mean
#: something. The mechanism stays because the next wave will need it: an entry
#: must name the defect and its owner, and ``strict=True`` turns a fixed one
#: into a failure until the entry is deleted, so a finding can be neither
#: forgotten nor quietly kept.
KNOWN_MISMATCHES: dict = {}


def test_the_contract_declares_something_to_check():
    assert OPERATIONS, "docs/schema.json declares no JSON responses at all"


def test_every_declared_path_resolves_under_this_urlconf():
    """The suite must be looking where the document describes.

    Five of the first eight libraries this gate was written for had a
    committed contract that nothing had ever driven, because the test urlconf
    mounted somewhere the document does not describe: one mounted a different
    prefix AND one segment short, one mounted the paths bare, one mounted a
    doubled segment, one mounted less than the emission did. THIS module is
    another: the harness settings mount ``stapel_notifications.urls_v1``
    bare, skipping both the host's ``notifications/api/`` prefix and the
    module's own ``v1/`` segment, so the whole existing suite drives
    ``/feed/`` and ``/devices/`` and not one path of the committed document
    resolves under it.

    That is the same family as a gate nobody asks: the recipes can all be
    written, the run can be green, and not one request went where the
    contract says it goes. A missing recipe already fails loudly; this fails
    when the MOUNT is wrong, which no per-operation check can see, because
    when the mount is wrong every operation is equally and silently
    unreachable.

    Asserted against the urlconf this module declares, so it fails at the one
    moment it is cheap to fix: when somebody changes a mount.
    """
    from django.urls import Resolver404, resolve

    # Resolution cares about the SHAPE of a segment, and a urlconf may use
    # several converters — uuid, int, slug. A path counts as reachable if any
    # one shape resolves: the question here is whether the mount exists, not
    # whether a particular id does.
    candidates = (
        "00000000-0000-4000-8000-000000000000",
        "1",
        "a-slug",
    )

    unreachable = []
    for _method, path, _code, _schema in OPERATIONS:
        for value in candidates:
            try:
                resolve(re.sub(r"\{[^}]+\}", value, path))
                break
            except Resolver404:
                continue
        else:
            unreachable.append(path)

    assert not unreachable, (
        "these declared paths do not resolve under this module's urlconf, so "
        "nothing here can be driving them — the mount is wrong, not the "
        "recipes:\n  " + "\n  ".join(sorted(set(unreachable)))
    )


def test_every_declared_operation_is_driven_or_named_undrivable():
    """No operation is covered by silence, and no entry outlives its operation."""
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    covered = set(RECIPES) | set(UNDRIVABLE)

    missing = sorted(declared - covered)
    assert not missing, (
        "operations with a declared JSON response body and no recipe:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    stale = sorted(covered - declared)
    assert not stale, (
        "recipes/exclusions for operations the contract no longer declares:\n"
        + "\n".join(f"  {m} {p}" for m, p in stale)
    )
    both = sorted(set(RECIPES) & set(UNDRIVABLE))
    assert not both, f"driven AND excluded: {both}"
    for key, reason in UNDRIVABLE.items():
        assert reason and reason.strip(), f"{key} is excluded with no reason"

    stale_collections = sorted(set(POPULATED_COLLECTIONS) - declared)
    assert not stale_collections, (
        f"nested-collection expectations for undeclared operations: {stale_collections}"
    )


def test_every_read_is_also_driven_in_its_emptiest_state():
    """A populated answer cannot say what a field holds when there is nothing.

    Every null finding in the first wave of this gate was on the empty state.
    A gate that only ever seeds three rows and asks never sees any of them.

    So every GET is required to have an ``EMPTY_STATE`` recipe as well. The
    exemptions are named in ``EMPTY_STATE_EXEMPT``, each with its reason.
    """
    reads = {
        (method, path)
        for method, path, _code, _schema in OPERATIONS
        if method == "GET"
    }
    missing = sorted(reads - set(EMPTY_STATE) - set(EMPTY_STATE_EXEMPT))
    assert not missing, (
        "reads driven only against a populated database — the state where "
        "every null claim in this gate's history was found is unchecked:\n"
        + "\n".join(f"  {m} {p}" for m, p in missing)
    )
    declared = {(m, p) for m, p, _c, _s in OPERATIONS}
    stale = sorted(set(EMPTY_STATE) - declared)
    assert not stale, f"empty-state recipes for undeclared operations: {stale}"
    stale_exempt = sorted(set(EMPTY_STATE_EXEMPT) - declared)
    assert not stale_exempt, (
        f"empty-state exemptions for undeclared operations: {stale_exempt}"
    )
    for key, reason in EMPTY_STATE_EXEMPT.items():
        assert reason and reason.strip(), f"{key} is exempt with no reason"
    overlap = sorted(set(EMPTY_STATE) & set(EMPTY_STATE_EXEMPT))
    assert not overlap, f"driven in its empty state AND exempt from it: {overlap}"


def test_every_known_mismatch_is_still_declared_and_explained():
    """A recorded defect must name a live operation and carry its reason.

    Without this, an operation that is renamed or removed leaves an entry that
    silences nothing and reads like a known problem forever.
    """
    declared = {(method, path) for method, path, _code, _schema in OPERATIONS}
    for key, reason in KNOWN_MISMATCHES.items():
        assert key in declared, (
            f"{key} is recorded as a known mismatch but the contract no longer "
            "declares it — delete the entry"
        )
        assert reason and reason.strip(), f"{key} is recorded with no reason"


def _labelled(result):
    """A recipe answers with one response, or with labelled branches."""
    if isinstance(result, list):
        return result
    return [("", result)]


def _drive(table, method, path, code, body_schema, *, expect_rows):
    perform = table.get((method, path))
    assert perform is not None, (
        f"{method} {path} declares a response body and has no recipe — an "
        "unchecked operation is a schema nobody proves. Teach RECIPES, or "
        "name it in UNDRIVABLE with a reason."
    )

    for label, response in _labelled(perform(Call(method, path))):
        where = f"{method} {path}" + (f" [{label}]" if label else "")
        assert response.status_code == code, (
            f"{where}: expected the declared {code}, got "
            f"{response.status_code}: {response.content[:400]}"
        )

        body = response.json()
        errors = sorted(
            _validator(body_schema).iter_errors(body), key=lambda e: list(e.path)
        )
        assert not errors, (
            f"{where} answers a body the contract does not describe:\n"
            + "\n".join(f"  at {list(e.path) or '<root>'}: {e.message}" for e in errors[:10])
            + f"\n  body: {json.dumps(body)[:600]}"
        )

        # An empty list validates against any item schema, so a collection
        # must actually carry a row for the check to have looked at anything.
        if expect_rows:
            if isinstance(body, list):
                assert body, f"{where}: the declared list came back empty"
            for name in POPULATED_COLLECTIONS.get((method, path), ()):
                assert isinstance(body, dict) and body.get(name), (
                    f"{where}: the declared collection {name!r} came back "
                    "empty, so nothing in it was checked"
                )


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    OPERATIONS,
    ids=[f"{m} {p}" for m, p, _c, _s in OPERATIONS],
)
def test_the_wire_matches_the_declared_response(method, path, code, body_schema, request):
    if (method, path) in UNDRIVABLE:
        pytest.skip(f"excluded by name: {UNDRIVABLE[(method, path)]}")

    if (method, path) in KNOWN_MISMATCHES:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{method} {path}: {KNOWN_MISMATCHES[(method, path)]}",
            )
        )

    _drive(RECIPES, method, path, code, body_schema, expect_rows=True)


_EMPTY_OPERATIONS = [
    (method, path, code, schema)
    for method, path, code, schema in OPERATIONS
    if (method, path) in EMPTY_STATE
]


@pytest.mark.parametrize(
    "method,path,code,body_schema",
    _EMPTY_OPERATIONS,
    ids=[f"{m} {p}" for m, p, _c, _s in _EMPTY_OPERATIONS],
)
def test_the_wire_matches_the_declared_response_when_there_is_nothing_there(
    method, path, code, body_schema, request
):
    """The same claim, asked in the state where the nulls live."""
    if (method, path) in KNOWN_MISMATCHES:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{method} {path}: {KNOWN_MISMATCHES[(method, path)]}",
            )
        )

    _drive(EMPTY_STATE, method, path, code, body_schema, expect_rows=False)


def test_the_gate_is_not_blind():
    """A canary: swap a declared schema for one the wire cannot satisfy.

    Everything above can be green for two reasons — the claims are honest, or
    the check never looks at the body. This tells them apart by validating a
    real response against ``{"type": "string"}``: every operation here answers
    an object or an array, so every one of them must fail. If any passes, the
    validation in ``_drive`` is not reaching the received body and this whole
    file proves nothing. With ``KNOWN_MISMATCHES`` empty this covers the
    entire declared surface.
    """
    honest = [
        (method, path, code)
        for method, path, code, _schema in OPERATIONS
        if (method, path) not in KNOWN_MISMATCHES and (method, path) not in UNDRIVABLE
    ]
    assert honest, "nothing left to canary"

    survivors = []
    for method, path, code in honest:
        try:
            _drive(RECIPES, method, path, code, {"type": "string"}, expect_rows=False)
        except AssertionError:
            continue
        survivors.append(f"{method} {path}")
    assert not survivors, (
        "these operations passed validation against {'type': 'string'} — the "
        "gate is not looking at the body it received:\n  " + "\n  ".join(survivors)
    )
