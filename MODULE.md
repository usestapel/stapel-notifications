# stapel-notifications — MODULE.md

Agent-facing map of this module: what it provides, its fork-free extension
points, and the anti-patterns those extension points make unnecessary. Use it
to classify a desired change as **app-layer override** (use an extension point
below, no fork) vs **upstream contribution** (change to this package via the
contribution pipeline — see `docs/stdlib-contribution-pipeline.md` and
system-design §8.6). Stapel modules never import each other; all cross-module
interaction goes through `stapel_core.comm` (events + functions) and the bus.

- pip package: `stapel-notifications` (import `stapel_notifications`), depends only on `stapel-core`
- Django app label: `notifications` (`stapel_notifications.apps.NotificationsConfig`)
- Optional extras: `[firebase]` (push via FCM), `[kafka]` (bus consumers)

## What this module provides

| Area | Details |
|---|---|
| Multi-channel dispatch | `services.process_notification` resolves language → contacts → translations → templates and dispatches to `email` / `sms` / `push` channels, with per-channel `NotificationLog` rows (`sent` / `failed` / `skipped`) and `event_id` idempotency |
| Type → channel routing | `routing.NOTIFICATION_ROUTING` built-in catalog (17 types: `otp_code`, `auth_change_*`, `magic_link_login`, `new_device_login`, `suspicious_login`, `all_sessions_revoked`, `gdpr.*`, `new_message`, `report_reviewed`, `listing_expiring`, `listing_blocked`, `workspace.invitation`) in groups `auth` (mandatory) / `messages` / `system` (user-mutable) |
| User preferences | `UserNotificationSettings` (per channel×group booleans + language), enforced in `services._should_send`; `auth` group always sends |
| Contact projection | `UserContact` (email/phone synced from auth via bus; `is_active` soft-off during account-closure grace period) |
| Push tokens + feed | `DevicePushToken` model; REST API: `POST/DELETE devices/`, `GET feed/` (push log as feed), `GET notification-keys/` (translation-key export for the translate collector) |
| Branded email layer | `templates/notifications/email/_base.html` shared shell + 16 per-type templates + `_raw_content.html` escape hatch; branding driven entirely by settings |
| i18n | `TranslationCache` model, lazy pull through the `translate.resolve` comm Function, English defaults in `translation_keys.NOTIFICATION_KEYS` |
| GDPR | `NotificationsGDPRProvider` (section `notifications`) registered in `apps.ready()` on `stapel_core.gdpr.gdpr_registry` — export + erase |
| Ops commands | `consume_notifications`, `consume_contacts`, `consume_profiles` (bus consumers), `sync_translations` (prefetch), `check_notifications` (CI gate: every literal `request_notification` call site must reference a registered type), `eject_notification_templates` (copy packaged templates into the host project) |

Public API (`stapel_notifications.__all__`, PEP 562 lazy):
`notifications_settings`, `request_notification` (re-export of
`stapel_core.notifications.request_notification` — the publish side lives in
core so any module can request without importing this one),
`process_notification`, `get_channels`, `get_group`, `get_email_template`,
`registered_types`.

## Extension points (fork-free)

### 1. Settings — the `STAPEL_NOTIFICATIONS` namespace (`conf.py`)

`notifications_settings = AppSettings("STAPEL_NOTIFICATIONS", ...)`.
Resolution per key: `settings.STAPEL_NOTIFICATIONS[key]` → flat Django setting
of the same name (legacy) → environment variable → default. Values are read
lazily (never frozen at import) and reload on `setting_changed` in tests.

| Key | Default | Purpose |
|---|---|---|
| `TYPES` | `{}` | Notification-type registry, merged **over** built-ins (see §2) |
| `EMAIL_TEMPLATES` | `{}` | Per-type email template map, merged over `DEFAULT_EMAIL_TEMPLATES` |
| `EMAIL_PROVIDER` | `"mock"` | `resend` / `smtp` / `mailgun` / `mock` or dotted path (see §3) |
| `SMS_PROVIDER` | `"mock"` | `gatewayapi` / `twilio` / `mock` or dotted path |
| `PUSH_PROVIDER` | `"fcm"` | `fcm` / `mock` or dotted path |
| `RESEND_API_KEY` | `""` | Resend credentials |
| `MAILGUN_API_KEY`, `MAILGUN_DOMAIN` | `""` | Mailgun credentials |
| `GATEWAYAPI_TOKEN`, `GATEWAYAPI_SENDER` | `""`, `"Stapel"` | GatewayAPI credentials + sender name |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | `""` | Twilio credentials |
| `GOOGLE_APPLICATION_CREDENTIALS` | `""` | FCM service-account file path |
| `COMPANY_NAME` | `"Stapel"` | Template variable `company_name` |
| `COMPANY_URL`, `COMPANY_ADDRESS`, `COMPANY_YEAR` | `""` | Footer/legal template variables (`COMPANY_YEAR` empty → current year) |
| `FRONTEND_URL` | `""` | Base for `unsubscribe_url` / `manage_notifications_url` |
| `LOGO_URL` | `""` | Set → emails embed `<img src="LOGO_URL">`, no attachment. Empty → packaged static logo attached inline, referenced as `cid:logo` |
| `BRAND_PRIMARY` | `"#00AEEF"` | Logo/accent color (template var `brand_primary`) |
| `BRAND_PRIMARY_DARK` | `"#2A90D9"` | Buttons + links (`brand_primary_dark`) |
| `BRAND_BG` | `"#F5F5F6"` | Page background (`brand_bg`) |
| `BRAND_TEXT` | `"#1C1D20"` | Headings + body copy (`brand_text`) |
| `LANGUAGES` | `["en"]` | Languages prefetched by `manage.py sync_translations` (lazy resolve-on-miss covers the rest) |

Note: this namespace declares no `import_strings` — the `*_PROVIDER` dotted
paths are resolved at send time by `channels.sms._resolve_provider` (shared by
all three channels), so they behave as dotted-path seams anyway.

### 2. Notification types registry (`routing.py`) — the key extension point

Open registry, read through `get_routing(type)` / `get_channels(type)` /
`get_group(type)` / `get_email_template(type)` / `registered_types()`.
`STAPEL_NOTIFICATIONS["TYPES"]` is merged **over** `NOTIFICATION_ROUTING`; an
entry with the same key replaces the built-in wholesale.

Add a new type (no fork, no code in this package):

```python
STAPEL_NOTIFICATIONS = {
    "TYPES": {
        "invoice_ready": {
            "channels": ["email", "push"],          # any of email|sms|push
            "group": "system",                       # auth|messages|system
            "template": "myapp/email/invoice_ready.html",
        },
        # override a built-in:
        "new_message": {"channels": ["push"], "group": "messages"},
    },
}
```

- Email template precedence per type: routing entry `"template"` key →
  `EMAIL_TEMPLATES[type]` → `DEFAULT_EMAIL_TEMPLATES[type]` (built-in).
- Group semantics: `auth` = mandatory, no unsubscribe headers/links;
  `messages` / `system` = per-channel user preference checked, one-click
  `List-Unsubscribe` headers added.
- Ad-hoc escape hatch: `request_notification(..., content_html=/content_text=)`
  renders the given body inside the brand layout (`_raw_content.html`) and
  permits an **unregistered** type (group defaults to `system`).
- CI gate: `manage.py check_notifications` statically verifies literal
  `request_notification` call sites against the effective registry.

### 3. Channel providers — dotted paths (`channels/{email,sms,push}.py`)

Each channel resolves its provider per send via `_resolve_provider(name_or_path,
registry, fallback, kind)`: built-in short name, else any dotted path imported
with `django.utils.module_loading.import_string`, else fall back to `mock`
with a warning (never crash on misconfig).

| Channel | Setting | Built-ins | Provider duck type |
|---|---|---|---|
| Email | `EMAIL_PROVIDER` | `resend`, `smtp`, `mailgun`, `mock` | `.send(recipient, subject, html_body, headers: dict \| None) -> None` |
| SMS | `SMS_PROVIDER` | `gatewayapi`, `twilio`, `mock` | `.send(phone, body) -> None` |
| Push | `PUSH_PROVIDER` | `fcm`, `mock` | `.send(user_id, title, body, data: dict \| None) -> int` (count sent) |

A new provider (SendGrid, Postmark, APNs direct, …) is a class in the **host
project** with the matching `send` signature plus
`STAPEL_NOTIFICATIONS["EMAIL_PROVIDER"] = "myproject.email.SendgridProvider"`.
Facades are `send_email` / `send_sms` / `send_push` — same pattern as
`stapel_core` captcha backends.

### 4. Template overrides — Django loader mechanics + branding

All packaged templates live under the namespaced path
`templates/notifications/email/` (so host `email/*` templates cannot collide).
Standard Django app-directories loading applies: a template with the **same
relative path** in a project-level `DIRS` template directory (searched before
app directories) overrides the packaged one — no setting in this module needed.

- `manage.py eject_notification_templates --out templates/ [--only otp_code,new_message] [--dry-run] [--force]`
  copies packaged templates (always including `_base.html`,
  `_footer_unsubscribe.html`, `_raw_content.html`) to the same namespaced path
  in the host project for on-site editing.
- `_base.html` is the shared shell (header/logo, body slot, footer). Blocks to
  override in per-type templates: `content` (required), `preheader` (defaults
  to `{{ subject }}`), `footer`, `head_extra`. The footer auto-switches to the
  unsubscribe variant whenever `unsubscribe_url` is present (non-auth groups).
- Branding without touching any template: `LOGO_URL` + `BRAND_PRIMARY` /
  `BRAND_PRIMARY_DARK` / `BRAND_BG` / `BRAND_TEXT` + `COMPANY_*` are threaded
  into every render as `logo_url` / `brand_*` / `company_*` variables by
  `services.process_notification`; templates use `|default:` fallbacks for the
  colors.
- Pointing a type at a fully custom template needs no ejection at all:
  `EMAIL_TEMPLATES = {"new_message": "myapp/email/new_message.html"}` or the
  `"template"` key of a `TYPES` entry.

### 5. i18n — integration with the translate module (no import)

- Values are pulled through the comm Function **`translate.resolve`**
  (`translations.resolve_and_cache`): input `{"keys": [...], "language": "de"}`,
  output `{"values": {key: text}}`; results merged per-key into
  `TranslationCache.values` (`{"en": ..., "de": ...}`).
- The translate module emits a thin **`translations.changed`** invalidation
  event (`{language, keys_changed}`); the `actions.py` subscriber re-resolves
  the affected `notification.*` keys.
- Render path degrades gracefully: cache → lazy `translate.resolve` on miss →
  cached `en` value → built-in English default from
  `translation_keys.NOTIFICATION_KEYS` (also served at
  `GET notification-keys/` for the translate collector,
  `source='backend:notifications'`).
- Language resolution order per notification: profile override
  (`UserNotificationSettings.language`) → event `language` → auto-detected
  language → `"en"`. Translation strings are `{var}`-formatted with a
  `_SafeFormatter` that blocks attribute/index access.

### 6. Events & functions (comm surface)

Action subscriptions (`actions.py`, via `stapel_core.comm.on_action`;
in-process in a monolith, bus consumer in microservices — transport chosen by
`STAPEL_COMM`). Handlers are idempotent (at-least-once delivery):

| Event consumed | Handler behavior |
|---|---|
| `user.deleted` | Erase this module's PII via `NotificationsGDPRProvider.delete` |
| `user.deletion_initiated` | Soft-deactivate `UserContact` + `DevicePushToken` rows (reversible; reactivated by normal sync paths) |
| `translations.changed` | Re-resolve changed `notification.*` keys through `translate.resolve` |

Bus consumers (Kafka topics, `management/commands/consume_*.py`):

| Topic / event | Command | Effect |
|---|---|---|
| `notification.requested` | `consume_notifications` | `process_notification(...)` — the module's main input |
| user-contact-changed | `consume_contacts` | Upsert `UserContact` (email/phone from auth) |
| profile-changed | `consume_profiles` | Upsert `UserNotificationSettings` (preferences + language from profiles) |

Functions: this module **calls** `translate.resolve`; it registers no comm
Functions of its own. It publishes no events either — the publish side
(`request_notification`) lives in `stapel_core.notifications.publish` so any
module can request a notification without importing this package. JSON schemas
for consumed events: `schemas/consumes/*.json`.

### 7. Swappable models

None. No model here is swappable, and none needs to be: all models key on a
plain `user_id` UUID (no FK to `AUTH_USER_MODEL`), populated via bus sync —
the module works with any user model. If you believe a model must be swappable,
that is an upstream contribution, not an app-layer workaround.

### 8. Serializer seams (`views.py`)

Every APIView mixes in `SerializerSeamMixin`: class attributes
`request_serializer_class` / `response_serializer_class` plus overridable
getters `get_request_serializer_class()` / `get_response_serializer_class()`.
To change a payload shape: subclass the serializer (they are
`StapelDataclassSerializer`s over `dto.py` dataclasses —
`DeviceTokenRequest/Response`, `FeedItemResponse`), subclass the view setting
the class attribute, and route your URL to the subclass — the HTTP method
bodies are reused untouched (see `tests/test_serializer_seams.py`).

### 9. Signals

This module defines no custom Django signals. The only signal usage is
`stapel_core.conf.AppSettings` reloading its cache on `setting_changed`
(tests). Cross-module reactions belong on comm events (§6), not signals.

**Error localization** (i18n-shipping.md §5): `docs/errors.json` is the
existing en canon (the language-agnostic error-key registry codegen
artifact — `tests/test_error_keys.py`); ru ships as a flat
`translations/errors.ru.json` catalog with a `translations/.state.json`
provenance sidecar, and human-readable references
[Errors (EN)](docs/errors.en.md) · [Ошибки (RU)](docs/errors.ru.md). Semantics
of the i18n seams (library-standard §3.3 — MODULE.md states the merge
semantics of each key): the **error registry** is `dict.update`/**last-wins**
(a host `errors.py` autodiscovered after ours overrides an en text — and its
raise-time render — without a fork); the **locale catalogs** are discovered
over INSTALLED_APPS and merged **later-wins** (a host app's
`translations/errors.<lang>.json` overrides our texts, and an override MUST
keep the canon's `{param}` slots — gated). ru provenance is honest: 41 keys
seeded from the curated `stapel-translate` builtin fixtures (`origin:
seed:stapel-builtin`, no tokens spent), 2 keys machine-translated (`origin:
llm`, unreviewed — the gate's W-counter, cleared by `translate_catalogs
--approve`). Gate + regenerate: `tests/test_error_i18n.py`
(`check_translation_catalogs` — E on missing/stale/params/byte-instability);
regenerate with `STAPEL_REGEN_ERROR_I18N=1 pytest
tests/test_error_i18n.py::test_regen` and commit `translations/errors.ru.json`,
`translations/.state.json`, `docs/errors.{en,ru}.md`.

## Anti-patterns (never fork for these)

| Don't | Do instead |
|---|---|
| Fork to add or re-route a notification type | `STAPEL_NOTIFICATIONS["TYPES"]` entry (§2); verify with `manage.py check_notifications` |
| Fork or edit site-packages to rebrand emails | `LOGO_URL` + `BRAND_*` + `COMPANY_*` settings; per-type `EMAIL_TEMPLATES`; `eject_notification_templates` for structural edits (§4) |
| Fork to add an email/SMS/push provider (SendGrid, Postmark, …) | Provider class in your project + dotted path in `*_PROVIDER` (§3) |
| One-off email for an unregistered type by hacking templates | `request_notification(..., content_html=/content_text=)` — rendered inside the brand layout (§2) |
| Import `stapel_translate` (or any stapel module) from here, or vice versa | Comm surface only: `translate.resolve` Function + `translations.changed` event (§5) |
| Write to `UserContact` / `UserNotificationSettings` directly from app code | They are event-synced projections — emit the auth/profile events; direct writes are overwritten by the next sync |
| Call `process_notification` from another service/module | `request_notification` (re-exported here, defined in `stapel_core`) → bus → this module's consumer |
| Hardcode user-facing strings in an overridden template | Keep `notification.<type>.*` translation keys so i18n keeps working (§5) |
| Rewrite a view to change its response shape | Serializer seam: subclass + `response_serializer_class` (§8) |

## App-layer override vs upstream contribution — rule of thumb

**App-layer** (host project, zero fork): anything expressible as a
`STAPEL_NOTIFICATIONS` key — new/overridden types, template remaps, branding,
provider dotted paths, languages; project-level template files; view/serializer
subclasses on your own URLs; new event subscribers in your own app.

**Upstream contribution** (PR to this package): a new **channel** (the
`email|sms|push` set and `_dispatch` are closed — a provider is app-layer, a
channel is not); new preference groups or `UserNotificationSettings` fields;
new built-in types/templates useful to every host; changes to
`process_notification` orchestration (idempotency, language resolution,
`_should_send`); new consumed/emitted events or comm Functions; model/schema
changes.

Heuristic: if the change needs an edit to any file in this package, it is
upstream; if it fits in your `settings.py`, your templates directory, or your
own app's modules, it is app-layer. When an override feels impossible without
copying package code, that gap is itself an upstream contribution (a missing
extension point), not a reason to fork.
