"""docs/templates.json — the sixth contract artifact, and what it guarantees.

The drift gate lives in ``tests/test_contract.py`` alongside the other five.
This file is the SEMANTIC half: it asserts the properties a host project
relies on when it overrides one of this library's email templates, and it
proves the emitter is loud rather than lenient by breaking it on purpose.

Unlike the triad, this artifact needs no Django settings and no
drf-spectacular, so these tests run on any interpreter — the contract a host
gates against must not be checkable only on the release pin.
"""
import json
from pathlib import Path

import pytest

from stapel_tools.template_contract import (
    EmitError,
    Route,
    build_document,
    declared_for,
    resolve_chain,
    scan_call_site,
    scan_source,
)

REPO = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((REPO / "docs" / "templates.json").read_text())
TEMPLATE_DIRS = [REPO / "templates"]


def test_every_routed_type_with_an_email_template_is_declared():
    """The routing registry and the artifact cannot disagree."""
    from stapel_notifications.routing import DEFAULT_EMAIL_TEMPLATES, NOTIFICATION_ROUTING

    declared = {r["key"]: r["template"] for r in CONTRACT["routes"]}
    for ntype in NOTIFICATION_ROUTING:
        template = DEFAULT_EMAIL_TEMPLATES.get(ntype)
        if template is None:
            continue
        assert declared.get(ntype) == template, (
            f"{ntype} routes to {template} but templates.json says "
            f"{declared.get(ntype)!r} — run `make contract`"
        )


def test_every_declared_template_is_shipped_in_the_package():
    """Failure mode #2, from the library's side: the artifact may not name a
    template that no longer exists, or the host gate it feeds would be
    validating against fiction."""
    for entry in CONTRACT["templates"]:
        assert (REPO / "templates" / entry["path"]).is_file(), entry["path"]


def test_every_shipped_template_is_declared():
    """And the converse: a letter nobody can discover from the contract is a
    letter a host will reimplement."""
    shipped = {
        str(p.relative_to(REPO / "templates"))
        for p in (REPO / "templates").rglob("*.html")
    }
    declared = {e["path"] for e in CONTRACT["templates"]}
    assert shipped == declared, f"undeclared: {sorted(shipped - declared)}"


def test_declared_context_covers_what_the_library_templates_read():
    """Failure mode #1, from the library's side: every variable a shipped
    template renders must be declared by some provenance."""
    for entry in CONTRACT["templates"]:
        declared = set(entry["declared"])
        required = {n for n, s in entry["consumed"].items() if s == "required"}
        assert required <= declared, (
            f"{entry['path']} reads {sorted(required - declared)} which no "
            "provenance declares"
        )


def test_caller_variables_are_the_ones_senders_actually_pass():
    """Spot-check the derivation against the letters people actually send.

    ``code``/``expiry_minutes`` come from ``{code}`` in the OTP translation
    strings; ``accept_url`` comes from the invitation template reading it.
    Two different static witnesses, both real."""
    routes = {r["key"]: r for r in CONTRACT["routes"]}
    assert set(routes["otp_code"]["context"]["caller"]) == {"code", "expiry_minutes"}
    assert "accept_url" in routes["workspace.invitation"]["context"]["caller"]
    assert "workspace_name" in routes["workspace.invitation"]["context"]["caller"]


def test_auth_letters_declare_no_unsubscribe_variables():
    """An auth-group letter never gets unsubscribe_url, so the contract must
    not tell a host it may render one."""
    for route in CONTRACT["routes"]:
        if route.get("group") == "auth":
            assert route["context"].get("conditional", []) == [], route["key"]


def test_transactional_letters_declare_no_unsubscribe_variables():
    for route in CONTRACT["routes"]:
        if route.get("transactional"):
            assert route["context"].get("conditional", []) == [], route["key"]
    assert any(r.get("transactional") for r in CONTRACT["routes"])


def test_call_site_records_the_guard_on_conditional_variables():
    site = CONTRACT["call_sites"][0]
    assert site["dynamic_keys"] is True, (
        "the translation-derived half of the context is written under a "
        "computed key; if that stopped being true the emitter's `translation` "
        "provenance is describing something else"
    )
    unsub = site["variables"]["unsubscribe_url"]
    assert unsub["presence"] == "conditional"
    assert "is_transactional" in unsub["when"]


def test_limits_are_stated():
    """A contract that admits its edges beats one that quietly claims more."""
    assert CONTRACT["limits"]
    assert any("floor, not a census" in limit for limit in CONTRACT["limits"])


# --- the emitter is loud, not lenient ---------------------------------------

def test_undeclared_required_variable_aborts_emission():
    """The emitter refuses to publish a contract that under-declares."""
    with pytest.raises(EmitError, match="no provenance declares"):
        build_document(
            module="x",
            version="0",
            routing_key="k",
            template_root="templates",
            template_dirs=TEMPLATE_DIRS,
            routes=[
                Route(
                    key="otp_code",
                    template="notifications/email/otp_code.html",
                    context={"translation": ["heading"]},
                )
            ],
            call_sites=[],
            limits=[],
        )


def test_missing_template_aborts_emission():
    with pytest.raises(EmitError, match="not found"):
        resolve_chain("notifications/email/nope.html", TEMPLATE_DIRS)


def test_call_site_wiring_is_verified():
    """Rename the context dict and emission stops — it will not silently
    derive the contract from the wrong object."""
    with pytest.raises(EmitError, match="no render_to_string"):
        scan_call_site(REPO / "services.py", context_var="not_the_context")


def test_unmodelled_template_construct_is_loud_under_strict():
    with pytest.raises(EmitError, match="unmodelled template construct"):
        scan_source("{% mytag %}{{ x }}", name="t.html", strict=True)


def test_scanner_reads_django_syntax_not_regex():
    scan = scan_source(
        '{% if flag %}{{ a.b|default:fallback }}{% endif %}'
        '{% for row in rows %}{{ row.x }}{{ outer }}{% endfor %}'
        '{% mytag "lit" as bound %}{{ bound }}',
        name="t.html",
    )
    assert scan.variables["a"] == "optional"      # carries a default filter
    assert scan.variables["fallback"] == "optional"  # a filter ARG is a read
    assert scan.variables["flag"] == "optional"   # an {% if %} guard
    assert scan.variables["rows"] == "required"
    assert scan.variables["outer"] == "required"
    assert "row" not in scan.variables            # loop variable, not context
    assert "bound" not in scan.variables          # `as` result, not context
    assert scan.unknown_tags == ("mytag",)


# --- the consumer half a host project uses ----------------------------------

def test_declared_for_names_the_dead_override():
    assert "code" in declared_for(CONTRACT, "notifications/email/otp_code.html")
    with pytest.raises(EmitError, match="shadows nothing and is dead code"):
        declared_for(CONTRACT, "notifications/email/otp.html")
