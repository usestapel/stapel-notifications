"""stapel-notifications contract-emission harness (contract-pipeline.md §2-3).

Emits the module's own contract triad into ``docs/`` from a single-module
``{notifications + core}`` Django instance mounted at the canonical
``notifications/api/`` prefix:

  docs/schema.json   drf-spectacular OpenAPI, this module only, canonical prefix
  docs/flows.json    generate_flow_docs machine artifact, canonical-prefix paths
  docs/errors.json   generate_error_keys registry (already the etalon)

Copied from stapel-auth's reference implementation
(contract-pipeline.md §7 Wave 1 recipe). The *mechanism* is
``stapel_tools.codegen`` (unchanged, shared); this file is the thin
per-module *config* that wires the module's settings + canonical mount into
it. Unlike auth, this module has no Celery/social_django/flat-openid-shadow
concerns, so the harness omits those steps.

Usage:
    python -m stapel_notifications._codegen --out docs        # `make contract`
"""
from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path

#: The packages whose error keys belong in THIS module's contract.
#:
#: The error registry is a process-global map, so it holds every key that any
#: INSTALLED_APP registered on import — and `_codegen_settings` deliberately
#: joins `stapel_translate` (plus the core taskstore) when it is importable,
#: so the cross-library i18n loop stays testable. That makes the emitted
#: errors.json a function of what else is installed on the emitting machine:
#: six `stapel_translate` keys in the workspace venv, none in a
#: `pip install stapel-notifications` CI run. Both v0.17.0 and v0.17.1 died in
#: the publish job on exactly that drift.
#:
#: Those keys are real and they are documented — in stapel-translate's own
#: errors.json, beside the catalogs that translate them. Here they are noise.
OWNED_ERROR_PACKAGES = frozenset({"stapel_core", "stapel_notifications"})


@contextmanager
def scoped_error_registry():
    """Restrict the global error registry to :data:`OWNED_ERROR_PACKAGES`.

    Wrap any emission that reads the registry — errors.json below,
    docs/errors.<lang>.md in tests/test_error_i18n.py — so the artifact
    describes this library rather than this machine. The registry is restored
    on exit: the runtime tests still see the live deployment's full key set,
    which is what `/error-keys/` serves.

    It reaches for core's private maps because core exposes no owner filter
    yet; a `--owner` option on `generate_error_keys` is the durable home for
    this, and until it exists the scoping lives with the contract it protects.
    """
    from stapel_core.django.api import errors as core_errors

    saved_keys = dict(core_errors._GLOBAL_REGISTRY)
    saved_owners = dict(core_errors._OWNER_REGISTRY)
    for code, owner in saved_owners.items():
        if owner is not None and owner not in OWNED_ERROR_PACKAGES:
            core_errors._GLOBAL_REGISTRY.pop(code, None)
            core_errors._OWNER_REGISTRY.pop(code, None)
    try:
        yield
    finally:
        core_errors._GLOBAL_REGISTRY.clear()
        core_errors._GLOBAL_REGISTRY.update(saved_keys)
        core_errors._OWNER_REGISTRY.clear()
        core_errors._OWNER_REGISTRY.update(saved_owners)


def _configure() -> None:
    """Configure + boot the single-module Django instance for emission."""
    from django.conf import settings

    if not settings.configured:
        from stapel_notifications._codegen_settings import settings_kwargs

        settings.configure(
            **settings_kwargs(
                root_urlconf="stapel_notifications.codegen_urls", contract=True
            )
        )

    import django

    django.setup()

    # drf-spectacular froze its settings singleton at import time (before this
    # harness ran configure()), so it is on drf defaults — the same state the
    # monolith emits under. The one knob to force is SCHEMA_PATH_PREFIX: left
    # None, drf derives the operationId prefix from the common path of all
    # endpoints — "/" across the multi-module monolith (operationIds keep the
    # mount segment, notifications_api_*), but "/notifications/api" in a
    # single-module harness (which would strip it to bare
    # register_device_token). Pin it to the monolith's common prefix so the
    # operationIds are byte-identical; SCHEMA_PATH_PREFIX_TRIM stays False
    # (default) so the path *keys* keep /notifications/api/ on both sides.
    from drf_spectacular.settings import spectacular_settings

    from stapel_notifications._codegen_settings import CODEGEN_SCHEMA_PATH_PREFIX

    spectacular_settings.SCHEMA_PATH_PREFIX = CODEGEN_SCHEMA_PATH_PREFIX

    # In the monolith, stapel_gdpr.urls (mounted at auth/api/, alongside auth)
    # calls stapel_core.django.openapi.swagger.get_app_swagger_urls('gdpr', ...)
    # at import time, whose side effect is process-global: it registers a
    # drf-spectacular OpenApiAuthenticationExtension for
    # JWTCookieAuthentication (name "JWTCookieAuth"). By the time the monolith
    # introspects notifications' endpoints, that registration has already
    # happened, so every notifications operation gets a resolved
    # "security": [{"JWTCookieAuth": []}] entry. This single-module harness
    # has no such sibling import to piggyback on, so without this the
    # emitted schema is missing "security" on every operation and
    # drf-spectacular warns "could not resolve authenticator" — not a real
    # behavior difference, just registration-order leakage across modules in
    # the monolith's shared process. Reproduce it directly (idempotent, safe
    # to call multiple times per its own docstring) rather than diverge from
    # the monolith slice.
    from stapel_core.django.openapi.swagger import _register_jwt_auth_extension

    _register_jwt_auth_extension()


def _require_python_312() -> None:
    """Abort emission if not running the pinned 3.12 interpreter.

    drf-spectacular's rendering of component descriptions (``Optional[X]`` vs
    ``X | None``) depends on the Python **minor** version — contracts emitted
    on anything other than 3.12 (the CI/monolith pin) produce false diffs
    against the committed docs/*.json. Emission must never proceed on the
    wrong minor.
    """
    if sys.version_info[:2] != (3, 12):
        got = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise SystemExit(
            f"stapel-notifications contract emission ABORTED: running Python "
            f"{got}, but contracts must be emitted on Python 3.12 (the "
            "CI/monolith pin). drf-spectacular renders component "
            "descriptions (Optional[X] vs X | None) differently across "
            "Python minor versions, so emitting on any other minor produces "
            "false diffs against the committed docs/*.json. Re-run under a "
            "3.12 interpreter."
        )


def main(argv: list[str] | None = None) -> int:
    _require_python_312()

    parser = argparse.ArgumentParser(
        prog="stapel-notifications-contract",
        description="Emit this module's contract triad (schema.json + flows.json "
        "+ errors.json) into --out, canonical /notifications/api/ prefix.",
    )
    parser.add_argument(
        "--out",
        default="docs",
        help="Output directory for the triad (default: docs).",
    )
    args = parser.parse_args(argv)

    _configure()

    # Reuse the shared mechanism's byte-stable emitters (contract-pipeline.md
    # §2: "the single-module harness already exists"). We call the three
    # triad emitters directly rather than generate(), which would also emit
    # the features/ Gherkin bundle — a separate concern.
    from stapel_tools.codegen import emit_errors, emit_flows, emit_schema

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    paths = emit_schema(out / "schema.json")
    flows = emit_flows(out / "flows.json")
    with scoped_error_registry():
        errors = emit_errors(out / "errors.json")

    print(
        f"stapel-notifications contract: {paths} paths, {flows} flows, "
        f"{errors} error keys → {out}/",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
