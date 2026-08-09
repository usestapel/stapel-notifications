"""No visible email word may live in the template.

Defect (2026-08-08): `suspicious_login.html` hardcoded its header banner
as literal markup while the rest of the email went through the key
registry — a Russian recipient got a Russian body under an English
banner. Hardcoded copy passes every machine check (renders, tests green,
send succeeds); only a human reading in their own language notices. One
fix closes one literal — this gate closes the class, on every template,
every run.

What counts as visible: whatever survives stripping CSS, HTML/Django
comments, and tags with their attributes. Anything left over must come
from a translation_keys.py key, not the template.
"""
import html
import re
from pathlib import Path

import pytest

EMAIL_TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "notifications" / "email"

_STYLE = re.compile(r"<style\b.*?</style>", re.S | re.I)
_SCRIPT = re.compile(r"<script\b.*?</script>", re.S | re.I)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_DJANGO_COMMENT = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S | re.I)
_DJANGO_INLINE_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_DJANGO_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_HTML_TAG = re.compile(r"<[^>]*>", re.S)
#: word = two or more letters in a row. A single character is more likely
#: markup punctuation («×», «·»); flagging those would produce a gate
#: people just disable.
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'’-]+")


def visible_words(source: str) -> list[str]:
    text = _DJANGO_COMMENT.sub(" ", source)
    text = _DJANGO_INLINE_COMMENT.sub(" ", text)
    text = _HTML_COMMENT.sub(" ", text)
    text = _STYLE.sub(" ", text)
    text = _SCRIPT.sub(" ", text)
    text = _DJANGO_TAG.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    return _WORD.findall(html.unescape(text))


@pytest.mark.parametrize(
    "template",
    sorted(EMAIL_TEMPLATES.glob("*.html")),
    ids=lambda p: p.name,
)
def test_template_carries_no_hardcoded_copy(template):
    words = visible_words(template.read_text(encoding="utf-8"))
    assert not words, (
        f"{template.name}: hardcoded visible text in template — {words}. "
        f"Add a key in translation_keys.py and substitute the variable: "
        f"templates aren't translated, and recipients read in their own language."
    )


def test_gate_catches_the_regressed_defect():
    """The detector is a mechanism too, and needs its own test — this is
    the exact markup of the regression it was built to catch."""
    regression = (
        '<p style="margin: 0; font-size: 13px;">\n'
        "  &#9888; Security alert\n"
        "</p>"
    )
    assert visible_words(regression) == ["Security", "alert"]


def test_gate_ignores_service_layers():
    benign = (
        "{% comment %}Base email layout shared by every template{% endcomment %}\n"
        "<!-- Alert banner -->\n"
        "<style>body { margin: 0 !important; }</style>\n"
        '<p style="color: #1C1D20;">{{ badge }}</p>\n'
        "{% if revoke_url %}<a href=\"{{ revoke_url }}\">{{ cta }}</a>{% endif %}"
    )
    assert visible_words(benign) == []
