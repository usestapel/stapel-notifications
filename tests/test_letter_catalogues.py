"""Every packaged letter reaches a reader in each shipped language.

The library's copy lives in ``translation_keys.NOTIFICATION_KEYS`` (English)
and in the packaged gettext catalogues under ``locale/<lang>/``. Without a
catalogue entry a slot falls back to English, which looks like success — the
mail is sent, only in the wrong language. These tests close that class for
every slot of every built-in type (email subject/body/footer, SMS, push):

* each shipped catalogue covers every slot that has words in it;
* each translation carries exactly the English ``{placeholders}``, line
  breaks and markup;
* the compiled ``.mo`` is the ``.po`` it was built from;
* a letter of every type, sent in every shipped language, resolves no slot
  to its English source;
* a host's ``TEXT`` pin still outranks the packaged catalogue, and a cache
  row that merely echoes the English source does not.
"""
import gettext
import html
import json
import re
import uuid
from pathlib import Path

import pytest
from django.test import override_settings

from stapel_notifications import services
from stapel_notifications.conf import notifications_settings
from stapel_notifications.routing import NOTIFICATION_ROUTING
from stapel_notifications.translation_keys import NOTIFICATION_KEYS

REPO = Path(__file__).resolve().parent.parent
LOCALE = REPO / "locale"

#: The languages this package ships a catalogue for. Adding one is adding a
#: ``locale/<lang>/LC_MESSAGES/django.po`` (+ compiled ``.mo``) and its code here.
SHIPPED = ("de", "es", "fr", "it", "nl", "pl", "pt", "ru")

PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
MARKUP = re.compile(r"<[^>]+>")


def _has_words(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]", PLACEHOLDER.sub("", text)))


def _parse_po(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    msgid = msgstr = None
    target = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            if msgid is not None:
                entries[msgid] = msgstr
            msgid, msgstr, target = json.loads(line[6:]), "", "id"
        elif line.startswith("msgstr "):
            msgstr, target = json.loads(line[7:]), "str"
        elif line.startswith('"'):
            if target == "id":
                msgid += json.loads(line)
            else:
                msgstr += json.loads(line)
    if msgid is not None:
        entries[msgid] = msgstr
    entries.pop("", None)
    return entries


def _catalogue(lang: str) -> dict[str, str]:
    return _parse_po(LOCALE / lang / "LC_MESSAGES" / "django.po")


WORDY_SOURCES = {key: text for key, text in NOTIFICATION_KEYS.items() if _has_words(text)}


def test_the_shipped_list_is_what_is_on_disk():
    on_disk = sorted(p.name for p in LOCALE.iterdir() if (p / "LC_MESSAGES" / "django.po").exists())
    assert on_disk == sorted(SHIPPED)


@pytest.mark.parametrize("lang", SHIPPED)
def test_every_slot_has_a_translation(lang):
    catalogue = _catalogue(lang)
    missing = sorted(key for key, text in WORDY_SOURCES.items() if not catalogue.get(text))
    assert missing == [], f"{lang}: {len(missing)} slot(s) without a translation: {missing[:10]}"


@pytest.mark.parametrize("lang", SHIPPED)
def test_no_entry_translates_a_string_the_library_no_longer_has(lang):
    sources = set(NOTIFICATION_KEYS.values())
    stale = sorted(msgid for msgid in _catalogue(lang) if msgid not in sources)
    assert stale == [], f"{lang}: stale msgid(s), the English changed: {stale[:5]}"


@pytest.mark.parametrize("lang", SHIPPED)
def test_every_translation_keeps_the_english_placeholders_and_markup(lang):
    wrong = []
    for msgid, msgstr in _catalogue(lang).items():
        if set(PLACEHOLDER.findall(msgstr)) != set(PLACEHOLDER.findall(msgid)):
            wrong.append(("placeholders", msgid))
        if msgstr.count("\n") != msgid.count("\n"):
            wrong.append(("line breaks", msgid))
        if MARKUP.findall(msgstr) != MARKUP.findall(msgid):
            wrong.append(("markup", msgid))
    assert wrong == [], f"{lang}: {wrong[:5]}"


@pytest.mark.parametrize("lang", SHIPPED)
def test_the_compiled_catalogue_is_its_source(lang):
    """A stale ``.mo`` ships last release's words. Rebuild with
    ``msgfmt --check-format -o django.mo django.po`` in ``locale/<lang>/LC_MESSAGES``."""
    with open(LOCALE / lang / "LC_MESSAGES" / "django.mo", "rb") as fh:
        compiled = dict(gettext.GNUTranslations(fh)._catalog)
    compiled.pop("", None)
    assert compiled == _catalogue(lang)


@pytest.mark.parametrize("lang", SHIPPED)
def test_the_runtime_reaches_the_packaged_catalogue(lang):
    """Read through the runtime's own gettext step, not the file: proves the
    catalogue is on Django's search path once the app is installed."""
    text = NOTIFICATION_KEYS["notification.otp_code.heading"]
    assert services._gettext_default(text, lang) == _catalogue(lang)[text]


# ── a letter of every type, in every language ───────────────────────


class _Capture:
    email: list = []
    sms: list = []
    push: list = []


class CaptureEmail:
    def send(self, recipient, subject, html_body, headers):
        _Capture.email.append({"subject": subject, "html": html_body})


class CaptureSMS:
    def send(self, phone, body):
        _Capture.sms.append(body)


class CapturePush:
    def send(self, user_id, title, body, data):
        _Capture.push.append({"title": title, "body": body})
        return 1


PROVIDERS = {
    "EMAIL_PROVIDER": f"{__name__}.CaptureEmail",
    "SMS_PROVIDER": f"{__name__}.CaptureSMS",
    "PUSH_PROVIDER": f"{__name__}.CapturePush",
    "FRONTEND_URL": "https://app.example",
}

CONTRACT = json.loads((REPO / "docs" / "templates.json").read_text())
CALLER_VARIABLES = {
    route["key"]: route["context"].get("caller", []) for route in CONTRACT["routes"]
}


def _variables(notification_type: str) -> dict:
    names = set(CALLER_VARIABLES.get(notification_type, ()))
    for key in WORDY_SOURCES:
        if key.startswith(f"notification.{notification_type}."):
            names.update(n[1:-1] for n in PLACEHOLDER.findall(NOTIFICATION_KEYS[key]))
    names -= {"company_name", "company_year", "company_address"}
    return {
        name: ("https://app.example/x" if name.endswith("_url") else "Sample7")
        for name in sorted(names)
    }


#: Slots whose correct translation is the English word itself.
SAME_AS_ENGLISH: set[tuple[str, str]] = set()

TYPES = sorted(NOTIFICATION_ROUTING)


@pytest.fixture
def capture():
    _Capture.email, _Capture.sms, _Capture.push = [], [], []
    with override_settings(STAPEL_NOTIFICATIONS=PROVIDERS):
        notifications_settings.reload()
        yield _Capture
    notifications_settings.reload()


@pytest.mark.django_db
@pytest.mark.parametrize("lang", SHIPPED)
@pytest.mark.parametrize("notification_type", TYPES)
def test_no_english_slot_is_left(notification_type, lang, capture, monkeypatch):
    resolved: dict[str, str] = {}
    real = services._resolve_translations

    def spy(keys, language):
        out = real(keys, language)
        resolved.update(out)
        return out

    monkeypatch.setattr(services, "_resolve_translations", spy)
    services.process_notification(
        notification_type=notification_type,
        user_id=str(uuid.uuid4()),
        variables=_variables(notification_type),
        email="reader@example.com",
        phone="+15550000000",
        language=lang,
    )

    assert resolved, "the letter resolved no copy at all"
    left = sorted(
        key for key, value in resolved.items()
        if key in WORDY_SOURCES and value == NOTIFICATION_KEYS[key]
        and (lang, key) not in SAME_AS_ENGLISH
    )
    assert left == [], f"{notification_type} in {lang} kept English: {left}"

    channels = NOTIFICATION_ROUTING[notification_type]["channels"]
    if "email" in channels:
        (mail,) = capture.email
        rendered = mail["subject"] + "\n" + html.unescape(mail["html"])
        english = sorted(
            key for key in resolved
            if key in WORDY_SOURCES and (lang, key) not in SAME_AS_ENGLISH
            and _english_shows(NOTIFICATION_KEYS[key], rendered)
        )
        assert english == [], f"{notification_type} in {lang} rendered English: {english}"
    if "sms" in channels:
        (body,) = capture.sms
        assert not _english_shows(NOTIFICATION_KEYS[f"notification.{notification_type}.sms"], body)
    if "push" in channels:
        (push,) = capture.push
        for slot in ("push_title", "push_body"):
            source = NOTIFICATION_KEYS.get(f"notification.{notification_type}.{slot}", "")
            assert not _english_shows(source, push["title"] + "\n" + push["body"]), slot


def _fragment(source: str) -> str:
    """The longest run of words in *source* between two placeholders."""
    return max(PLACEHOLDER.split(source), key=len).strip()


def _english_shows(source: str, rendered: str) -> bool:
    """Does *rendered* carry *source*'s words? Fragments under two words
    ("at", ", ") are too short to tell English from anything else."""
    fragment = _fragment(source)
    return len(fragment.split()) >= 2 and fragment in rendered


def test_the_fragment_check_can_see_english():
    """Guard on the guard: a rendered English line contains its fragment."""
    source = NOTIFICATION_KEYS["notification.auth_change_reminder.body"]
    line = source.format(change_type="email", masked_new_value="a***", days_remaining=3)
    assert _english_shows(source, line)


# ── precedence: the host first, then the cache, then the catalogue ──

HEADING = "notification.otp_code.heading"


@pytest.mark.django_db
def test_a_host_pin_outranks_the_packaged_catalogue():
    pins = {HEADING: {"pl": "Host heading", "nl": "Host kop"}}
    with override_settings(STAPEL_NOTIFICATIONS={"TEXT": pins}):
        notifications_settings.reload()
        try:
            for lang, expected in (("pl", "Host heading"), ("nl", "Host kop")):
                assert services._resolve_translations([HEADING], lang)[HEADING] == expected
            german = services._resolve_translations([HEADING], "de")[HEADING]
        finally:
            notifications_settings.reload()
    assert german == _catalogue("de")[NOTIFICATION_KEYS[HEADING]]


@pytest.mark.django_db
def test_a_cached_translation_outranks_the_packaged_catalogue():
    from stapel_notifications.models import TranslationCache

    TranslationCache.objects.create(key=HEADING, values={"de": "Ihr Code"})
    assert services._resolve_translations([HEADING], "de")[HEADING] == "Ihr Code"


@pytest.mark.django_db
def test_a_cached_echo_of_the_english_does_not_outrank_the_catalogue():
    """translate.resolve answers a key it lacks in the asked language with
    the default language's text, and that answer is cached under the asked
    language. It used to win over the packaged catalogue."""
    from stapel_notifications.models import TranslationCache

    english = NOTIFICATION_KEYS[HEADING]
    TranslationCache.objects.create(key=HEADING, values={"en": english, "de": english})
    assert services._resolve_translations([HEADING], "de")[HEADING] == _catalogue("de")[english]


def test_an_earlier_app_echoing_a_shared_msgid_does_not_shadow_the_catalogue():
    """A REST framework catalogue maps "Username" to "Username" in Italian;
    Django's merged catalogue lets that echo shadow the translation here."""
    assert services._gettext_default("Username", "it") == _catalogue("it")["Username"]


def test_a_regional_variant_reads_the_base_catalogue():
    text = NOTIFICATION_KEYS[HEADING]
    assert services._gettext_default(text, "pt-br") == _catalogue("pt")[text]
