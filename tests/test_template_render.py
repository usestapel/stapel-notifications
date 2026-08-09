"""Every packaged letter renders with nothing missing from its declared context.

This is the loop the two artifacts close between them. ``docs/templates.json``
says what this library passes for each notification type; the test harness
renders a missing variable as a visible marker rather than Django's default
empty string (``stapel_core.templates``); and this file
renders every packaged template against exactly its declared context and
asserts no marker survives.

What it catches that neither half catches alone:

* rename ``{{ code }}`` in a template and forget the contract → the variable is
  not in the declared context, the marker appears, this fails naming ``code``;
* rename the variable in ``services.py`` and regenerate the contract without
  touching the template → same failure, from the other side.

The contract's own emission gate (``build_document``) already refuses to emit
when a template reads something no provenance declares. This is the runtime
confirmation of the same claim: the declaration is not merely internally
consistent, it is sufficient to render the letter.
"""
import json
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from stapel_core.templates import (
    assert_no_missing_variables,
    missing_variables,
)

REPO = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((REPO / "docs" / "templates.json").read_text())

#: A declared variable's value is irrelevant here — presence is the claim
#: under test. A distinctive filler also makes an accidental empty string
#: visible in a failure message.
FILLER = "<declared>"


def _context(route: dict) -> dict:
    names = {n for names in route["context"].values() for n in names}
    return {name: FILLER for name in sorted(names)}


ROUTES = [pytest.param(r, id=r["key"]) for r in CONTRACT["routes"]]


@pytest.mark.parametrize("route", ROUTES)
def test_declared_context_is_enough_to_render_the_letter(route):
    html = render_to_string(route["template"], _context(route))
    assert_no_missing_variables(html, context=f"{route['key']} → {route['template']}")


def test_the_marker_is_actually_armed():
    """A guard on the guard: if the harness ever stopped substituting the
    marker, every test above would pass vacuously — an empty string is not
    something an assertion can see."""
    route = next(r for r in CONTRACT["routes"] if r["key"] == "otp_code")
    context = _context(route)
    context.pop("code")
    html = render_to_string(route["template"], context)
    assert missing_variables(html) == ["code"]


def test_an_undeclared_variable_would_be_caught():
    """The failure mode, demonstrated: a template asking for something the
    contract does not declare renders a marker, not a blank space."""
    from django.template import Context, Engine

    engine = Engine(dirs=[], app_dirs=False, string_if_invalid="!!MISSING-TEMPLATE-VAR:%s!!")
    html = engine.from_string("code: {{ otp }}").render(Context({"code": "1234"}))
    with pytest.raises(AssertionError, match="otp"):
        assert_no_missing_variables(html)
