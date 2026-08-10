"""The email is RENDERED in the recipient's language, not the process's.

Two different mechanisms are involved, and telling them apart is the whole
point of this file — because a host asking "are my templates translated?"
gets a different answer depending on which one its template relies on.

1. **Resolved strings.** Every string this library owns is looked up per
   recipient (``services._resolve_translations``) and handed to the template
   as a context variable. The packaged templates contain no prose of their
   own — ``test_no_hardcoded_copy_in_templates`` enforces that — so they were
   already per-recipient before anything here.

2. **The render itself.** ``{% trans %}``, ``{% blocktrans %}``, ``|date``
   and every other locale-sensitive tag ask Django's ACTIVE language, which
   is the SENDER's (a web process that just handled a request) or whatever a
   consumer process was last left in. Those tags only appear in HOST
   templates. ``_dispatch`` now wraps the render in
   ``translation.override(lang)``, which is what makes a host's own gettext
   catalogue reach the person being written to.

And the limit, stated as a test rather than a promise:
``get_email_template(notification_type)`` takes NO language argument. There
is one template per type. Prose typed literally into a template is therefore
frozen in the language it was typed in, and no amount of correctness in (1)
or (2) moves it.
"""
import ast
import pathlib
import re

import pytest
from django.template.loader import render_to_string
from django.test import override_settings
from django.utils import translation

from stapel_core.templates import strict_template_variables

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import UserContact
from stapel_notifications.services import process_notification

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LATIN = re.compile(r"[A-Za-z]")

#: "Your verification code" is a real msgid in the packaged ru catalogue
#: (locale/ru/LC_MESSAGES/django.po → "Ваш код подтверждения"), so a host
#: template that uses gettext the standard Django way has something to hit.
MSGID = "Your verification code"
MSGSTR_RU = "Ваш код подтверждения"

HOST_TEMPLATES = {
    # what language is active AT RENDER TIME
    "host/active_language.html": (
        "{% load i18n %}{% get_current_language as LANG %}<p>lang={{ LANG }}</p>"
    ),
    # the standard Django way to translate a template
    "host/gettext.html": '{% load i18n %}<p>{% trans "' + MSGID + '" %}</p>',
    # prose typed straight into the markup
    "host/literal.html": "<p>Здравствуйте, это письмо на русском языке.</p>",
}


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _Capture:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append(
            {"subject": subject, "html": html_body, "headers": headers or {}}
        )


CAPTURE = f"{_Capture.__module__}._Capture"

HOST_TEMPLATE_SETTINGS = strict_template_variables([
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [],
            "loaders": [
                ("django.template.loaders.locmem.Loader", HOST_TEMPLATES),
                "django.template.loaders.app_directories.Loader",
            ],
        },
    }
])


def _send(notification_type, user_id, *, template=None, process_language,
          variables=None, **extra):
    """Send one notification with a known ACTIVE process language."""
    _Capture.sent = []
    settings = {"EMAIL_PROVIDER": CAPTURE, "FRONTEND_URL": "https://app.example"}
    if template:
        settings["TYPES"] = {
            notification_type: {
                "channels": ["email"], "group": "system", "template": template,
            }
        }
    with override_settings(
        STAPEL_NOTIFICATIONS=settings, TEMPLATES=HOST_TEMPLATE_SETTINGS
    ):
        notifications_settings.reload()
        with translation.override(process_language):
            process_notification(
                notification_type=notification_type,
                user_id=user_id,
                variables=variables or {},
                **extra,
            )
    (mail,) = _Capture.sent
    return mail


# ── (2) the render runs inside the recipient's language ──────────────


@pytest.mark.django_db
def test_the_render_is_wrapped_in_the_recipients_language(user, profiles_language):
    """The process is Russian; the recipient chose English; the TEMPLATE
    sees English."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    profiles_language[str(user.id)] = ("en", None)

    mail = _send(
        "myapp.probe", str(user.id),
        template="host/active_language.html", process_language="ru",
    )
    assert "lang=en" in mail["html"]


@pytest.mark.django_db
def test_a_host_gettext_template_renders_in_the_recipients_language(
    user, profiles_language
):
    """The process is English; the recipient chose Russian; ``{% trans %}``
    resolves against the ru catalogue.

    This is the case that was broken: the string is not one of this
    library's keys, so nothing resolved it into all_vars — it was translated
    by the template engine, under whatever language the process happened to
    have active.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    profiles_language[str(user.id)] = ("ru", None)

    mail = _send(
        "myapp.probe", str(user.id),
        template="host/gettext.html", process_language="en",
    )
    assert MSGSTR_RU in mail["html"]
    assert MSGID not in mail["html"]


@pytest.mark.django_db
def test_the_process_language_is_restored_after_the_send(user, profiles_language):
    """An override that leaks would mistranslate whatever runs next."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    profiles_language[str(user.id)] = ("ru", None)

    with translation.override("en"):
        _send(
            "myapp.probe", str(user.id),
            template="host/gettext.html", process_language="en",
        )
        assert translation.get_language() == "en"


def test_the_wrap_is_what_does_it():
    """Control: the same template, rendered WITHOUT the override, follows
    the process. This is the behaviour every host template had."""
    with override_settings(TEMPLATES=HOST_TEMPLATE_SETTINGS):
        with translation.override("en"):
            assert MSGID in render_to_string("host/gettext.html", {})
            with translation.override("ru"):
                assert MSGSTR_RU in render_to_string("host/gettext.html", {})


# ── (1) resolved strings: already per-recipient, re-measured here ────


@pytest.mark.django_db
def test_a_packaged_letter_is_english_end_to_end_while_the_process_is_russian(
    user, profiles_language
):
    """Oleg's sandbox measurement, as a test.

    Russian active in the process, ``"ru"`` passed as the request language,
    and a recipient who CHOSE English: subject and body both come out
    English. The recipient's choice outranks both — and the body proves the
    language reached past the subject line.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    profiles_language[str(user.id)] = ("en", None)

    mail = _send(
        "otp_code", str(user.id), process_language="ru",
        variables={"code": "1234", "expiry_minutes": 5}, language="ru",
    )
    body = re.sub(r"<[^>]*>", " ", mail["html"])
    assert CYRILLIC.search(mail["subject"]) is None
    assert CYRILLIC.search(body) is None, "the body followed the process, not the recipient"
    assert LATIN.search(body)
    assert "verification code" in mail["subject"].lower()


@pytest.mark.django_db
def test_the_same_letter_in_russian_for_a_russian_recipient(user, profiles_language):
    """The mirror image, so the test above cannot pass by sending English
    to everybody."""
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    profiles_language[str(user.id)] = ("ru", None)

    mail = _send(
        "otp_code", str(user.id), process_language="en",
        variables={"code": "1234", "expiry_minutes": 5},
    )
    body = re.sub(r"<[^>]*>", " ", mail["html"])
    assert CYRILLIC.search(mail["subject"])
    assert MSGSTR_RU in body


# ── the limit: one template per type ─────────────────────────────────


@pytest.mark.django_db
def test_literal_prose_in_a_host_template_is_not_translated_by_any_of_this(
    user, profiles_language
):
    """The honest answer for a host whose letters are hardcoded markup.

    ``get_email_template`` takes no language argument — there is one
    template per type — so a template whose text is typed into the HTML
    sends that text to an English-speaking recipient too. The fix is not in
    this library: move the words into ``{% trans %}`` (now correctly scoped
    to the recipient) or into ``STAPEL_NOTIFICATIONS["TEXT"]`` / the
    translation-key registry, which is what the packaged templates do.
    """
    UserContact.objects.create(user_id=user.id, email="u@example.com")
    profiles_language[str(user.id)] = ("en", None)

    mail = _send(
        "myapp.probe", str(user.id),
        template="host/literal.html", process_language="en",
    )
    assert CYRILLIC.search(mail["html"]), (
        "a literal-prose template is language-frozen by construction; if this "
        "ever fails, the mechanism grew a capability and the docs owe the "
        "reader an explanation"
    )


def test_one_template_per_type_is_the_structural_reason():
    """No language reaches template SELECTION, only template RENDERING."""
    import inspect

    from stapel_notifications.routing import get_email_template

    params = list(inspect.signature(get_email_template).parameters)
    assert params == ["notification_type"]


# ── the gate: a render cannot escape the override ────────────────────
#
# The point-fix version of this defect is one `with` at one call site, and
# the next call site forgets it — which is exactly how it got here: the
# module already wrapped its gettext lookup in an override (services.py, the
# `_gettext_default` helper) and the render three hundred lines below simply
# never did. So the rule is asserted over the SOURCE: every render in this
# package must be lexically inside `translation.override(...)`.

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Anything that turns a Django template into text. Matched on the callee's
#: final name, so `render_to_string(...)` and `loader.render_to_string(...)`
#: are both caught.
_RENDERERS = {"render_to_string"}

_SKIP_DIRS = {"tests", "build", "docs", "__pycache__", ".git", ".venv", "locale"}


def _package_sources() -> list[pathlib.Path]:
    return sorted(
        p for p in PACKAGE_ROOT.rglob("*.py")
        if not _SKIP_DIRS & set(p.relative_to(PACKAGE_ROOT).parts)
    )


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_language_override(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _callee_name(node.func) == "override"


def renders_outside_a_language_override(source: str) -> list[int]:
    """Line numbers of template renders not lexically inside an override."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee_name(node.func) in _RENDERERS):
            continue
        cursor: ast.AST | None = node
        wrapped = False
        while cursor is not None and not wrapped:
            if isinstance(cursor, (ast.With, ast.AsyncWith)):
                wrapped = any(
                    _is_language_override(item.context_expr) for item in cursor.items
                )
            cursor = parents.get(cursor)
        if not wrapped:
            offenders.append(node.lineno)
    return offenders


def test_no_template_is_rendered_outside_the_recipients_language():
    for path in _package_sources():
        offenders = renders_outside_a_language_override(path.read_text(encoding="utf-8"))
        assert not offenders, (
            f"{path.relative_to(PACKAGE_ROOT)}: render_to_string at line(s) "
            f"{offenders} runs under whatever language the PROCESS has active "
            "— the sender's in a web process, a leftover in a consumer. Wrap "
            "it in `with translation.override(lang):` using the language "
            "resolved for the recipient."
        )


def test_the_gate_sees_the_defect_it_was_built_for():
    """The detector is a mechanism too, so it gets its own test — this is
    0.9.0's shape, verbatim in structure."""
    defect = (
        "def _dispatch(lang):\n"
        "    html = render_to_string(template, all_vars)\n"
    )
    fixed = (
        "def _dispatch(lang):\n"
        "    with translation.override(lang):\n"
        "        html = render_to_string(template, all_vars)\n"
    )
    assert renders_outside_a_language_override(defect) == [2]
    assert renders_outside_a_language_override(fixed) == []


def test_the_gate_is_not_fooled_by_a_neighbouring_with():
    """An override that ENDS before the render is not a wrap."""
    near_miss = (
        "def _dispatch(lang):\n"
        "    with translation.override(lang):\n"
        "        subject = gettext(s)\n"
        "    html = render_to_string(template, all_vars)\n"
    )
    assert renders_outside_a_language_override(near_miss) == [4]
