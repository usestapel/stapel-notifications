"""Pull-side of the translate→notifications sync.

The translate module emits a thin ``translations.changed`` invalidation
event ({language, keys_changed}); the values themselves are pulled through
the comm Function ``translate.resolve``:

    input:  {"keys": ["notification.otp_code.subject", ...], "language": "de"}
    output: {"values": {key: text}}   # keys missing in both the requested
                                      # and the default language are omitted

This module owns that call and the merge into the local TranslationCache.
It is used by three entry points:

- the ``translations.changed`` action subscriber (actions.py),
- the ``sync_translations`` management command (initial population),
- the lazy resolve-on-miss path in ``services._resolve_translations``.
"""

import logging

from stapel_core.comm import call

logger = logging.getLogger(__name__)

TRANSLATE_RESOLVE = "translate.resolve"


def resolve_and_cache(keys: list[str], language: str) -> dict[str, str]:
    """Resolve *keys* in *language* via ``translate.resolve`` and merge the
    result into TranslationCache (per-key, language merged into the existing
    ``values`` dict).

    Returns the resolved ``{key: text}`` mapping (possibly empty). Raises
    whatever ``call`` raises — callers decide whether translate being down
    is fatal (the event handler lets the bus retry; the render path
    degrades to the ``en`` fallback).
    """
    from .models import TranslationCache

    keys = [k for k in keys if k]
    if not keys or not language:
        return {}

    result = call(TRANSLATE_RESOLVE, {"keys": keys, "language": language})
    values = (result or {}).get("values") or {}
    if not isinstance(values, dict):
        logger.warning(
            "translate.resolve returned a non-dict 'values' for language %s: %r",
            language, type(values),
        )
        return {}

    for key, text in values.items():
        if not isinstance(text, str):
            continue
        cached = TranslationCache.objects.filter(key=key).first()
        merged = dict(cached.values) if cached and isinstance(cached.values, dict) else {}
        merged[language] = text
        TranslationCache.objects.update_or_create(key=key, defaults={"values": merged})

    logger.info(
        "Synced %d translation value(s) for language %s", len(values), language
    )
    return {k: v for k, v in values.items() if isinstance(v, str)}
