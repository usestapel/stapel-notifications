"""stapel-notifications ``docs/templates.json`` emitter — the SIXTH contract
artifact, thin shim over ``stapel_tools.template_contract``.

Templates are this module's largest extension surface and were the only one
with nothing declared about it. The other five artifacts describe HTTP paths,
error codes, flows, config axes and the usage surface; not one of them names
a template path or a context variable, so a host overriding
``notifications/email/otp_code.html`` had to read ``services.py`` to learn
what its own file would be handed — and nothing then stopped this library
renaming either the file or the variable in a patch release, silently.

Provenance — where each context variable comes from, all four derived
---------------------------------------------------------------------
``branding``
    Literal-key writes into ``all_vars`` in ``services.py`` that happen on
    every path (``company_name``, ``logo_url``, the ``brand_*`` colours…).
    Read out of the Python AST of the render call site, not retyped.
``conditional``
    The same, but written only under an ``if`` — today
    ``unsubscribe_url`` / ``manage_notifications_url``, guarded by
    "non-auth group, known user, not transactional". The guard's source text
    travels into the artifact.
``translation``
    The short names ``services.process_notification`` derives from
    ``translation_keys.NOTIFICATION_KEYS`` — ``notification.<type>.<slot>``
    becomes ``<slot>``, ``notification.footer.<slot>`` becomes
    ``footer_<slot>``. Derived through ``translation_keys.keys_for_type``,
    the same function the runtime calls.
``caller``
    What the SENDER must pass in ``variables``. Two independent static
    witnesses, unioned: a ``{placeholder}`` inside one of the type's own
    translation defaults (``"Your code is {code}"`` proves ``code``), and a
    variable the shipped template reads that no other provenance declares
    (``{{ accept_url }}`` proves ``accept_url``).

The honest edge, restated in the artifact's own ``limits``: a variable a
caller passes that neither a translation string interpolates nor the shipped
template renders is invisible to every static source there is — the library
never mentions it. ``caller`` is therefore a floor, not a census. Everything
else in the document is exact.
"""
from __future__ import annotations

import re
import string
import tomllib
from pathlib import Path

from stapel_tools.template_contract import (
    Route,
    build_document,
    resolve_chain,
    run_template_contract_cli,
    scan_call_site,
)

REPO = Path(__file__).resolve().parent
TEMPLATE_ROOT = "templates"
TEMPLATE_DIRS = [REPO / TEMPLATE_ROOT]

#: The raw-content escape hatch is a route with no routing key: any
#: unregistered type may take it by passing content_html/content_text.
RAW_CONTENT_KEY = "@raw-content"
RAW_CONTENT_TEMPLATE = "notifications/email/_raw_content.html"

LIMITS = [
    "`caller` is a floor, not a census: a variable the sender passes in "
    "`variables` that no translation string interpolates and no shipped "
    "template renders is invisible to static derivation — nothing in this "
    "library mentions it. Every other field here is exact.",
    "A host's own overrides are not modelled. This declares what the LIBRARY "
    "passes and what the LIBRARY's templates read; STAPEL_NOTIFICATIONS "
    "TYPES / EMAIL_TEMPLATES / TEXT can add types, repoint templates and "
    "replace copy at runtime.",
    "Chain variables are the union over {% extends %} and {% include %}: a "
    "parent block the child overrides is still counted, and `include ... only` "
    "context isolation is ignored. The union over-declares, which is the safe "
    "direction for a host gating `my template's variables ⊆ declared`.",
    "`consumed` reports `optional` for a variable whose every read carries a "
    "`default` filter or is an {% if %} condition, `required` otherwise. It "
    "describes the LIBRARY's template; a host's override may read more or "
    "fewer.",
]


def _module_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]


def _placeholders(text: str) -> set[str]:
    """Root field names of ``{...}`` in a translation default.

    ``services`` interpolates every translated string with ``_SafeFormatter``
    over the whole context, so ``"Your {company_name} code: {code}"`` is proof
    that ``code`` reaches the letter from somewhere. Uses the stdlib parser
    rather than a regex so ``{{`` escapes behave.
    """
    out: set[str] = set()
    try:
        for _lit, field_name, _spec, _conv in string.Formatter().parse(text):
            if field_name:
                out.add(re.split(r"[.\[]", field_name)[0])
    except ValueError:
        # A string with unbalanced braces cannot be a format string; the
        # runtime swallows the same ValueError and leaves it alone.
        pass
    return out


def build() -> dict:
    from stapel_notifications.routing import (
        DEFAULT_EMAIL_TEMPLATES,
        NOTIFICATION_ROUTING,
    )
    from stapel_notifications.translation_keys import (
        FOOTER_PREFIX,
        NOTIFICATION_KEYS,
        keys_for_type,
    )

    services = scan_call_site(REPO / "services.py", context_var="all_vars")
    if not services.dynamic_keys:
        raise AssertionError(
            "services.py no longer writes all_vars under a computed key — the "
            "translation-derived half of the context has moved and this "
            "emitter's `translation` provenance is describing nothing."
        )

    branding = sorted(k for k, v in services.variables.items() if v["presence"] == "always")
    conditional = sorted(
        k for k, v in services.variables.items() if v["presence"] == "conditional"
    )
    framework = set(branding) | set(conditional)
    known_types = sorted(NOTIFICATION_ROUTING)

    routes: list[Route] = []
    for ntype in known_types:
        template = DEFAULT_EMAIL_TEMPLATES.get(ntype)
        if template is None:
            # A routed type with no email template: routed to sms/push only,
            # or a gap. Either way there is no template contract to declare.
            continue
        entry = NOTIFICATION_ROUTING[ntype]

        translated: set[str] = set()
        placeholders: set[str] = set()
        prefix = f"notification.{ntype}."
        for key in keys_for_type(ntype, known_types=known_types):
            if key.startswith(prefix):
                translated.add(key[len(prefix):])
            elif key.startswith(FOOTER_PREFIX):
                translated.add("footer_" + key.split(".")[-1])
            placeholders |= _placeholders(NOTIFICATION_KEYS[key])

        _chain, consumed, _scans = resolve_chain(template, TEMPLATE_DIRS, strict=True)
        caller = (placeholders | set(consumed)) - translated - framework

        routes.append(
            Route(
                key=ntype,
                template=template,
                context={
                    "translation": sorted(translated),
                    "branding": branding,
                    "conditional": [] if entry.get("transactional") or entry.get("group") == "auth"
                    else conditional,
                    "caller": sorted(caller),
                },
                meta={
                    "group": entry.get("group", ""),
                    "channels": list(entry.get("channels", [])),
                    "transactional": bool(entry.get("transactional")),
                },
            )
        )

    # The escape hatch: process_notification(content_html=/content_text=)
    # renders the base layout around caller-supplied markup, for a type that
    # need not be registered at all.
    raw_extra: set[str] = set()
    for render in services.renders:
        if RAW_CONTENT_TEMPLATE in render["template"]:
            raw_extra |= set(render["extra_keys"])
    _chain, raw_consumed, _scans = resolve_chain(RAW_CONTENT_TEMPLATE, TEMPLATE_DIRS, strict=True)
    raw_translated = {
        "footer_" + k.split(".")[-1] for k in NOTIFICATION_KEYS if k.startswith(FOOTER_PREFIX)
    } | {"subject"}
    routes.append(
        Route(
            key=RAW_CONTENT_KEY,
            template=RAW_CONTENT_TEMPLATE,
            context={
                "translation": sorted(raw_translated),
                "branding": branding,
                "conditional": conditional,
                "caller": sorted(
                    (raw_extra | set(raw_consumed)) - raw_translated - framework
                ),
            },
            meta={"group": "system", "channels": ["email"], "transactional": False},
            note="Not a routing key: process_notification(content_html=...) "
            "wraps caller-supplied markup in the base layout, and accepts an "
            "unregistered notification type.",
        )
    )

    return build_document(
        module="stapel-notifications",
        version=_module_version(),
        routing_key="notification_type",
        template_root=TEMPLATE_ROOT,
        template_dirs=TEMPLATE_DIRS,
        routes=routes,
        call_sites=[services],
        limits=LIMITS,
        strict=True,
    )


def main(argv=None) -> int:
    return run_template_contract_cli(argv, repo=REPO, build=build)


if __name__ == "__main__":
    raise SystemExit(main())
