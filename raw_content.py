"""Whether a caller may put its own body inside this brand's letterhead.

``process_notification(..., content_html=...)`` renders caller-supplied
markup with ``|safe`` inside the brand layout, and accepts an UNREGISTERED
notification type while it is at it. That is a phishing kit: anything that
can reach the notification bus — a compromised producer, a leaked shared
broker credential, an internal service with more reach than it needs — can
send mail that is, byte for byte, this platform writing to its own users,
with any content and any link it likes. No amount of HTML sanitising fixes
that: ``<a href="https://not-us.example/login">`` is valid, harmless-looking
markup and is the whole attack.

So the hatch is a declaration a deployment makes, not a default:

``STAPEL_NOTIFICATIONS["RAW_CONTENT"]``
    ``"off"`` (default)
        There is no hatch. Only registered types with registered templates
        are sent; ``content_html``/``content_text`` are ignored and an
        unregistered type is refused exactly as it would be without them.
    ``"text"``
        Ad-hoc bodies are allowed but no caller decides markup: HTML is
        reduced to its text, which the layout then renders as escaped
        paragraphs. Keeps the operational use of the hatch (a one-off
        notice from an internal tool) without keeping the brandable canvas.
    ``"html"``
        The pre-0.11 behaviour, for a deployment whose producers are all
        first-party and authenticated. Boot warns (``W004``) so this stays a
        decision somebody made rather than one they inherited.
"""
import logging

from django.utils.html import strip_tags
from django.utils.text import normalize_newlines

from .conf import notifications_settings

logger = logging.getLogger(__name__)

OFF = "off"
TEXT = "text"
HTML = "html"
MODES = (OFF, TEXT, HTML)


def mode() -> str:
    """The configured mode, or ``off`` for anything unrecognised.

    An unreadable value falls back to the closed setting, never the open one:
    a typo in a security switch must not be the thing that opens it.
    """
    configured = str(notifications_settings.RAW_CONTENT or OFF).strip().lower()
    if configured not in MODES:
        logger.warning(
            "STAPEL_NOTIFICATIONS['RAW_CONTENT']=%r is not one of %s — "
            "treating it as %r", configured, list(MODES), OFF,
        )
        return OFF
    return configured


def apply_policy(
    notification_type: str,
    content_html: str | None,
    content_text: str | None,
) -> tuple[str | None, str | None]:
    """The caller's raw body as this deployment permits it to be sent."""
    if not (content_html or content_text):
        return None, None

    current = mode()
    if current == HTML:
        return content_html, content_text
    if current == TEXT:
        if not content_html:
            return None, content_text
        return None, content_text or _to_text(content_html)

    logger.error(
        "Raw content refused for notification_type=%r: "
        "STAPEL_NOTIFICATIONS['RAW_CONTENT'] is %r. Register the type in "
        "STAPEL_NOTIFICATIONS['TYPES'] with its own template, or set "
        "RAW_CONTENT to 'text' (caller body, no caller markup) or 'html' "
        "(caller markup — only where every producer is trusted).",
        notification_type, OFF,
    )
    return None, None


def _to_text(html: str) -> str:
    """Markup reduced to what it says.

    ``strip_tags`` is not a sanitiser and is not used as one — the result
    goes through ``|linebreaksbr`` in the template, which escapes it, so
    nothing that survives here is ever rendered as markup.
    """
    return normalize_newlines(strip_tags(html)).strip()
