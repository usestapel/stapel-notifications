# Changelog

## Unreleased

## [0.3.13] — 2026-07-17

Fix-up #2: 0.3.12's regen still baked the old version into
`docs/capabilities.json` (`make contract` ran before the version bump
landed). Re-ran with 0.3.13 already in `pyproject.toml`; verified match,
suite green.

## [0.3.12] — 2026-07-17

Fix-up: 0.3.11's CI/publish failed on contract drift — `docs/capabilities.json`
embeds the package version and wasn't regenerated for the 0.3.11 bump.
Regenerated via `make contract`; no other diff.

## [0.3.11] — 2026-07-17

Fleet follow-up to stapel-core 0.12.0 (legacy shim sweep). No source
changes needed — the `consume_*` management commands already import
`stapel_core.bus.BaseBusConsumerCommand` (aliased locally as
`BaseKafkaConsumerCommand`), not the removed kafka transport module. Full
suite green against core 0.12.0.

### Changed
- `stapel-core` dependency ceiling `<0.12` → `<0.13`.

### Removed — legacy flat-setting compat scrub (0.3.10)

- `tests/test_extensibility.py::test_legacy_flat_setting_still_works` deleted —
  the module no longer pins or advertises the legacy flat Django setting
  fallback (`EMAIL_PROVIDER`, `PUSH_PROVIDER`, `TWILIO_*` as top-level
  settings). Configure via the `STAPEL_NOTIFICATIONS` namespace dict or
  environment variables.
- Docs scrubbed of the legacy flat-setting resolution step (`conf.py`
  docstring, `channels/push.py` docstring, `MODULE.md` §1): documented
  resolution is now `settings.STAPEL_NOTIFICATIONS[key]` → env → default.
  The flat fallback mechanism itself lives in `stapel_core.conf.AppSettings`
  (out of this repo's scope); this package no longer documents or tests it —
  removing the mechanism is a stapel-core change.

### Changed — core ceiling raised for the 0.11 fleet re-pin (0.3.9)

- `stapel-core` ceiling raised `>=0.10,<0.11` → `>=0.10,<0.12` (core 0.11 is
  additive for modules: default bus, nav, config-checks, error
  params/language).
- `docs/schema.json` regenerated against core 0.11.2 — error object gained
  `error_language` field and a reworded `error` description; no drift
  otherwise.

### Added — per-module contract emission (contract-pipeline.md §2-3, Wave 1)

This module now emits its **own** API contract triad — `docs/schema.json`
(drf-spectacular OpenAPI), `docs/flows.json` (generate_flow_docs machine
artifact) and `docs/errors.json` (generate_error_keys, already the etalon) —
from a single-module `{notifications + core}` Django instance mounted at the
canonical `/notifications/api/` prefix, copied byte-for-byte from
stapel-auth's reference implementation.

- `_codegen_settings.py` — extracted `settings.configure(...)` block, single
  source of truth shared by `conftest.py` (bare test mount) and the new
  `_codegen.py` harness (canonical-prefix mount, production `REST_FRAMEWORK`).
  Adds `drf_spectacular` to `INSTALLED_APPS`. No test-behavior change.
- `codegen_urls.py` — mounts `stapel_notifications.urls` at `notifications/api/`
  (the monolith's mount, `svc-app/core/urls.py:39` — no sibling co-mount, unlike
  auth+gdpr).
- `_codegen.py` — entrypoint; pins `spectacular_settings.SCHEMA_PATH_PREFIX = "/"`
  and explicitly registers the `JWTCookieAuth` drf-spectacular authentication
  extension (`stapel_core.django.openapi.swagger._register_jwt_auth_extension`)
  — in the monolith this registration happens as a process-global side effect
  of importing `stapel_gdpr.urls` (which calls `get_app_swagger_urls`) before
  notifications' endpoints are introspected; this single-module harness has no
  such sibling to piggyback on, so it reproduces the registration directly
  (idempotent) rather than diverge from the monolith slice (missing `security`
  on every operation, and a spectacular "could not resolve authenticator"
  warning).
- `Makefile` (`contract` / `contract-check`) + `tests/test_contract.py`
  (triad-committed, no-drift, deterministic, canonical-prefix, and — in the
  workspace only — byte-identity vs the monolith aggregate's notifications
  slice).
- `docs/schema.json` + `docs/flows.json` (new): **byte-identical** to the
  monolith aggregate's `/notifications/api/` slice (4 paths, 5-component
  closure: `DeviceTokenRequest`, `DeviceTokenResponse`, `FeedItemResponse`,
  `PaginatedFeedItemResponseList`, `StapelError` — fully self-contained, no
  sibling-only `$ref`s, so no additional module needed installed in the
  harness). This module has no `@flow_step` annotations (confirmed against the
  monolith aggregate, which also carries zero notifications flows), so
  `flows.json = []`. `docs/errors.json` unchanged (already committed, emission
  is a no-op).

Regenerate with `make contract`; gated by `tests/test_contract.py`.

### Added — admin-suite AS-5: `@access` category rollout + `StapelModelAdmin`

Applies the `stapel_core.access` category decorators (admin-suite §0/AS-5
sweep, docs/admin-suite.md) to this module's models and switches the
affected `ModelAdmin`s to `stapel_core.django.admin.base.StapelModelAdmin`.

- `@access.ops` (read-only journal, forbids add/change/delete for everyone
  including superuser; view requires HIGH clearance): `NotificationLog` (a
  passive delivery/audit journal — matches the doc's own worked example) and
  `TranslationCache` (a pure sync cache populated only by
  `translations.resolve_and_cache`/`sync_translations`/the
  `translations.changed` subscriber, never hand-authored through the admin).
- `@access.secret` (every operation superuser-only, sensitive fields masked):
  `DevicePushToken` — carries a bearer FCM push-token string (`token`),
  matches the doc's own worked example verbatim; the field name already
  matches `StapelModelAdmin`'s auto-detect pattern, no `secret_fields` pin
  needed.
- `UserNotificationSettings`, `UserContact` stay undecorated (implicit
  `@access.standard`) — per-user preference/contact business projections a
  support operator legitimately looks up, the doc's own `Profile` shape.
- Attribute-only change: no migrations (`makemigrations notifications
  --check --dry-run` reports no changes).

## 0.3.6 — 2026-07-06

### Added — ru error catalog + bilingual error reference (i18n-shipping волна 2)

Reference-pattern application of the `stapel_core.i18n` catalog contour to the
`errors` domain (i18n-shipping.md §5), copied 1:1 from the stapel-auth pilot.

- `translations/errors.ru.json` — flat `{code: text}` ru catalog covering all
  43 keys, with `translations/.state.json` provenance sidecar. 41 keys seeded
  from the curated `stapel-translate` builtin fixtures (`origin:
  seed:stapel-builtin`, no tokens spent), 2 machine-translated (`origin:
  llm`, unreviewed). `translations/.errors.ru.llm-cache.json` is the
  committed, content-hash translation cache.
- `docs/errors.en.md` · `docs/errors.ru.md` — generated human-readable
  references; README + MODULE.md link both languages.
- `tests/test_error_i18n.py` — `check_translation_catalogs` gate + env-gated
  regen (`STAPEL_REGEN_ERROR_I18N=1`).


## 0.3.5 — 2026-07-06

### Added
- **Declarative error registry + `docs/errors.json` codegen artifact.** The two
  service error keys (`error.400.invalid_platform`, `error.404.token_not_found`)
  now declare a machine-readable `remediation` hint (`fix_input` for both —
  backend is canon, overriding the heuristic that would resolve a 404
  `not_found` to `retry`) via `register_service_errors(..., remediation=...)`.
- `docs/errors.json` — the language-agnostic error-key registry (43 entries:
  core `COMMON_ERRORS` + cross-cutting verification/captcha keys + the two
  service keys), emitted by `generate_error_keys` and consumed by the frontend
  (`stapel-react` notifications pair) as the errors-bundle source.
- `tests/test_error_keys.py` — byte-stable drift gate (regenerate-and-diff, same
  discipline as schema.json/flow docs) plus artifact-shape and
  declared-remediation assertions. Regenerate with
  `STAPEL_REGEN_ERROR_KEYS=1 pytest tests/test_error_keys.py`.

### Changed
- Test settings (`conftest.py`) install `stapel_core.django.apps.CommonDjangoConfig`
  so the `generate_error_keys` management command is discoverable for the drift
  gate. No `@flow_step` flows exist in this module (0 flows is valid).


## 0.3.4 — 2026-07-06

### Changed
- Pinned `stapel-core` to the `>=0.8,<0.9` window (library-standard §7.1: one
  minor window; floor `0.8.0` is published on PyPI — no pin into the void).
- CI: added the release-track job (library-standard §7.4) — installs the package
  the way an end user does (`pip install .`, dependencies resolved from PyPI
  strictly by the declared pins, no git-main core, no editable siblings), asserts
  `stapel-core` resolves inside the `0.8` window, and runs an import smoke.
  Advisory (continue-on-error) until the whole stapel graph is on PyPI; becomes
  the blocking precondition for a `vX.Y.Z` tag once it is.


## 0.3.3 — 2026-07-06

### Packaging
- Tests excluded from the built wheel/sdist (the `stapel_notifications.tests`
  subpackage is no longer listed in `[tool.setuptools] packages`). Added
  `[project.urls]`, completed the trove classifiers (MIT/OSI, Python 3.13,
  `Typing :: Typed`, OS Independent, `3 :: Only`, Development Status) and a
  `[tool.ruff]` lint section (single source shared with the git hooks/CI).


## 0.3.2 — 2026-07-05

### Fixed
- `user_id` in comm schemas typed uuid, was integer — rejected valid
  `user.deleted` events. `schemas/consumes/user.deleted.json` now types
  `user_id` as `{"type": "string", "format": "uuid"}`, matching the
  UUID-pk canonical user and the auth/gdpr producers.


## 0.3.1 — 2026-07-04
### Added
- **translate→notifications loop fixed (comm seam).** New
  `@on_action("translations.changed")` subscriber: the event is a thin
  invalidation (`{language, keys_changed}`); values are pulled through the
  `translate.resolve` comm Function and merged into `TranslationCache`
  (declared in `schemas/consumes/translations.changed.json`).
- `manage.py sync_translations [--languages de,fr]` — initial population of
  the TranslationCache for all `NOTIFICATION_KEYS` across
  `STAPEL_NOTIFICATIONS['LANGUAGES']`.
- Lazy resolve-on-miss in the render path: a translation-cache miss calls
  `translate.resolve`, stores the value and proceeds; translate being down
  degrades to the built-in `en` fallback as before.
- Branding settings: `LOGO_URL` (set → `<img src=URL>` and no inline CID
  attachment; unset → packaged logo attached as `cid:logo` as before),
  `BRAND_PRIMARY`, `BRAND_PRIMARY_DARK`, `BRAND_BG`, `BRAND_TEXT`.
  All email templates now extend a single base layout
  (`templates/notifications/email/_base.html`) that renders header/logo/
  footer/colors from these settings — changing env vars restyles every
  email type without editing templates.
- `manage.py eject_notification_templates --out templates/ [--only a,b]
  [--dry-run] [--force]` — copies the packaged email templates (incl. the
  base layout) into the host project for on-site customization;
  skip-if-exists unless `--force`; prints loader-order next steps.
- `manage.py check_notifications [paths...]` — static AST lint over
  `request_notification(...)` call sites: literal types must be registered
  (built-ins + `STAPEL_NOTIFICATIONS['TYPES']`) unless the call passes
  `content_html`/`content_text`; exit 1 on error; dynamic types are
  warnings (cross-service literal call sites only — documented limitation).
- Raw-content escape hatch: `notification.requested` payloads may carry
  `content_html`/`content_text`; the body is rendered inside the base brand
  layout instead of a registered per-type template, and an unregistered
  type is then allowed (group defaults to `system`).
- `@on_action("user.deletion_initiated")` — account-closure grace period
  soft-deactivates the user's contact (`UserContact.is_active`, migration
  `0004`) and push tokens; full erasure stays on `user.deleted`. A contact
  sync or device re-registration reactivates them. **Known gap:** the gdpr
  module emits no "closure cancelled" event, so an explicit cancellation
  cannot proactively re-enable notifications — reactivation waits for the
  next sync.

### Removed
- Legacy Kafka consumer `manage.py consume_translations`
  (`TOPIC_TRANSLATIONS_CHANGED` / `EventType.TRANSLATIONS_CHANGED`,
  fat `{key, values}` payload). The topic never matched what translate now
  emits — replaced by the comm Action + Function pull above. The constants
  remain in stapel-core (deprecated) for deployments pinning the old
  contract.


## 0.3.0 — 2026-07-03

No functional changes — version alignment with the Stapel 0.3
release train; stapel-core dependency now `>=0.3.0,<0.4`.


## 0.2.0 — 2026-07-02

### Added
- `STAPEL_NOTIFICATIONS` settings namespace (`stapel_notifications.conf`):
  every previously hardcoded knob — providers, credentials, company
  branding, FRONTEND_URL — is overridable without forking. Legacy flat
  settings (`EMAIL_PROVIDER`, `TWILIO_*`, …) keep working.
- Open notification-type registry: `STAPEL_NOTIFICATIONS["TYPES"]` adds or
  overrides types (channels, group, email template) on top of the built-in
  catalog. `STAPEL_NOTIFICATIONS["EMAIL_TEMPLATES"]` maps/overrides email
  templates per type, merged over the built-in defaults.
- Channel providers accept dotted paths (`"myapp.email.SendgridProvider"`)
  besides built-in short names — same escape hatch as captcha backends.
  This now covers all three channels: `EMAIL_PROVIDER`, `SMS_PROVIDER`
  and the new `PUSH_PROVIDER` (built-ins: `fcm` — default, `mock`;
  FCM logic extracted into a provider class).
- GDPR / account-lifecycle notification types + email templates:
  `gdpr.export_ready` (vars: `download_url`), `gdpr.inactivity_warning`
  (vars: `days_remaining`), `gdpr.inactivity_closed` — all in the
  mandatory `auth` group (no unsubscribe).
- `workspace.invitation` notification type + email template (vars:
  `workspace_name`, `inviter_name`, `accept_url`; used by
  stapel-workspaces invitations).
- SMS opt-out preferences: `sms_messages` / `sms_system` fields on
  `UserNotificationSettings` (migration `0003_add_sms_preferences`),
  honored by `_should_send`, synced by the profiles consumer and included
  in the GDPR export — mirroring the email/push preferences.
- `user.deleted` comm Action subscriber erases contact data.
- `py.typed` marker (PEP 561) shipped in package data.

### Changed
- `routing.get_email_template()` replaces the module-level EMAIL_TEMPLATES
  lookup; precedence: per-type `"template"` key →
  `STAPEL_NOTIFICATIONS["EMAIL_TEMPLATES"]` → built-in default.
- Email templates namespaced from `templates/email/*` to
  `templates/notifications/email/*` so host projects' own `email/*`
  templates cannot collide with the app's (all render paths and includes
  updated). Hosts that referenced the old `email/...` paths directly must
  update to `notifications/email/...`.
- Legacy branding leftovers removed: `COMPANY_NAME` default is `Stapel`,
  GatewayAPI SMS sender default is now `Stapel`, bus consumer groups
  renamed to `stapel.notifications.*` (overridable via the
  `NOTIFICATIONS_CONSUMER_GROUP[_CONTACTS|_PROFILES|_TRANSLATIONS]` env
  vars). Marketplace-specific types (`new_message`, `report_reviewed`,
  `listing_expiring`, `listing_blocked`) are kept in the defaults: other
  modules and translation keys still reference them, so removal is not
  trivially safe.

### Fixed
- `POST /devices/` no longer silently re-binds a push token that belongs
  to another user: the previous binding is removed explicitly inside a
  transaction with an audit warning log line before the token is
  registered for the requesting user.

### Packaging
- Email templates, static and event schemas ship in the wheel.
- Django floor raised to 5.1.
