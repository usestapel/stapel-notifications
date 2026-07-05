# Changelog

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
  GatewayAPI SMS sender default `legacy` → `Stapel`, bus consumer groups
  `stapel.notifications.*` → `stapel.notifications.*` (overridable via the
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
