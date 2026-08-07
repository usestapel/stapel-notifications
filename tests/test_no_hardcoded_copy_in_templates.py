"""Ни одно видимое слово письма не живёт в шаблоне.

ДЕФЕКТ. В `suspicious_login.html` плашка над заголовком была написана прямо
в разметке: `&#9888; Security alert`. Всё остальное письмо шло через реестр
ключей и переводилось — поэтому русский человек получал русское тело под
английской шапкой. Разбор Олега, 08.08.2026: письмо про безопасность, где
шапка на чужом языке, читается как подделка ровно в тот момент, когда
доверие нужнее всего.

ПОЧЕМУ ГЕЙТ, А НЕ ПРАВКА. Копирайт в шаблоне не ломает ничего машинного:
письмо собирается, тесты зелены, отправка проходит. Увидеть это может только
человек, читающий письмо на своём языке, — то есть уже получатель. Один
разбор закрыл один литерал; закрывает класс — проверка, которая смотрит на
все шаблоны сразу и на каждом прогоне.

ЧТО ИМЕННО СЧИТАЕТСЯ. Из шаблона вычитаются служебные слои, где слова
законны и невидимы получателю: CSS в `<style>`, HTML-комментарии, комментарии
Django и сами теги с их атрибутами. Всё, что осталось, — это текст, который
человек УВИДИТ, и он обязан приходить переменной из реестра ключей.
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
#: слово = две и более буквы подряд. Одиночная буква — это скорее символ
#: разметки («×», «·»), и придираться к ней значило бы получить гейт,
#: который отключат.
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
def test_шаблон_не_несёт_собственного_копирайта(template):
    words = visible_words(template.read_text(encoding="utf-8"))
    assert not words, (
        f"{template.name}: видимый текст зашит в шаблон — {words}. "
        f"Заведите ключ в translation_keys.py и подставьте переменной: "
        f"шаблон не переводится, а письмо читают на своём языке."
    )


def test_гейт_действительно_ловит_возвращённый_дефект():
    """Сам детектор — тоже механизм, и он обязан быть проверяемым.

    Ровно та строка, что уходила Елене, в точной разметке своей плашки.
    """
    regression = (
        '<p style="margin: 0; font-size: 13px;">\n'
        "  &#9888; Security alert\n"
        "</p>"
    )
    assert visible_words(regression) == ["Security", "alert"]


def test_гейт_не_придирается_к_служебным_слоям():
    benign = (
        "{% comment %}Base email layout shared by every template{% endcomment %}\n"
        "<!-- Alert banner -->\n"
        "<style>body { margin: 0 !important; }</style>\n"
        '<p style="color: #1C1D20;">{{ badge }}</p>\n'
        "{% if revoke_url %}<a href=\"{{ revoke_url }}\">{{ cta }}</a>{% endif %}"
    )
    assert visible_words(benign) == []
