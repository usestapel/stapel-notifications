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
| Type → channel routing | `routing.NOTIFICATION_ROUTING` built-in catalog (23 types: `otp_code`, `auth_change_*`, `magic_link_login`, `new_device_login`, `suspicious_login`, `all_sessions_revoked`, `gdpr.*`, `new_message`, `report_reviewed`, `listing_expiring`, `listing_blocked`, `workspace.invitation` + `.new_user`/`.reminder`, `workspace.provisioned_account`, `workspace.mfa_*`, `workspace.member_password_reset`) in groups `auth` (mandatory) / `messages` / `system` (user-mutable) |
| User preferences | `UserNotificationSettings` (per channel×group booleans; **no language** — see §5), enforced in `services._should_send`; `auth` group always sends |
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
Resolution per key: `settings.STAPEL_NOTIFICATIONS[key]` → environment
variable → default. Values are read
lazily (never frozen at import) and reload on `setting_changed` in tests.

| Key | Default | Purpose |
|---|---|---|
| `TYPES` | `{}` | Notification-type registry, merged **over** built-ins (see §2) |
| `EMAIL_TEMPLATES` | `{}` | Per-type email template map, merged over `DEFAULT_EMAIL_TEMPLATES` |
| `TEXT` | `{}` | Per-key copy registry, merged **over** `NOTIFICATION_KEYS` — the string counterpart of `EMAIL_TEMPLATES` (see §4a) |
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
| `RAW_CONTENT` | `"off"` | May a caller supply the body of a branded letter? `off` / `text` (body yes, markup no) / `html` (trusted producers only, boot-warns `W004`) — see §2a |
| `TELEMETRY` | `{}` | Per-type journal allowlist, `{"<type>": [...], "*": [...]}` — deny-by-default, see §2b |
| `DELIVERY_CLAIM_TTL` | `900` | Seconds after which a delivery claim whose process died may be taken over by a redelivery (§2c) |

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
- Group semantics: the vocabulary is **closed** (`routing.VALID_GROUPS`), and
  an unknown or missing group is `notifications.E001` at boot — the group also
  names the recipient's preference field, so mail under a misspelled group is
  mail nobody can switch off. `auth` = mandatory security/authentication mail;
  `messages` / `system` = per-channel user preference checked.
- **Unsubscribe policy** (`routing.unsubscribe_allowed`, one decision behind
  both the footer and the `List-Unsubscribe` / `List-Unsubscribe-Post:
  One-Click` headers): an **allowlist** — the group must be in
  `UNSUBSCRIBABLE_GROUPS` (`messages`, `system`) and the type must be neither
  `"transactional": True` nor `"security": True`. Everything else, including a
  type whose group is missing or misspelled, gets nothing. Two orthogonal
  flags, both affordance-only (the group still decides the preference):
  `transactional` = one-to-one mail triggered by a named human;
  `security` = account-security mail that must nevertheless stay
  switch-off-able. Boot gates: `E001` unknown group, `E002` a settings
  override that drops a built-in security type's classification (an override
  REPLACES the built-in entry, it does not merge), `W003` a type named like
  security mail sitting in a bulk-mail group.
- Ad-hoc escape hatch: `request_notification(..., content_html=/content_text=)`
  renders the given body inside the brand layout (`_raw_content.html`) and
  permits an **unregistered** type (group defaults to `system`) — **only where
  the deployment opened it**, `RAW_CONTENT` is `"off"` by default (§2a).
- CI gate: `manage.py check_notifications` statically verifies literal
  `request_notification` call sites against the effective registry, and
  follows `RAW_CONTENT`: with the hatch shut a `content_html=` call site on an
  unregistered type is an error, because it sends nothing.

### 2a. `RAW_CONTENT` — who may compose branded mail (`raw_content.py`)

Caller-supplied markup rendered with `|safe` inside this brand's layout, for a
notification type that need not be registered, is a phishing kit for anything
that can reach the bus (security audit 2026-08-11, NOTIFY-02). No sanitiser
answers it — `<a href="https://not-us.example/login">` is valid markup and is
the attack — so the hatch is a declaration, not a default:

| `RAW_CONTENT` | Unregistered types | Caller markup |
|---|---|---|
| `"off"` (default) | refused, ERROR names the setting | ignored |
| `"text"` | allowed | reduced to its text, escaped by the layout |
| `"html"` | allowed | rendered as given; boot-warns `W004` |

An unrecognised value falls back to `"off"`. Authenticating and scoping the
producers on the bus is the deployment's job — this setting removes the value
of reaching the bus, it does not replace producer authentication.

### 2b. `TELEMETRY` — what the delivery journal may remember (`telemetry.py`)

`NotificationLog.data` used to hold every scalar the caller passed, which for
the built-in types means passcodes, sign-in links, invitation URLs and
provisioned passwords (NOTIFY-01). It is now deny-by-default in two layers:
a **key** is journalled only where declared — the routing entry's
`"telemetry": [...]`, `STAPEL_NOTIFICATIONS["TELEMETRY"]`, or the deep links
the push feed needs (`chat_url`, `listing_url`, `notifications_chat_url`) —
and a declared key is still replaced by `[redacted]` when its **value** is
credential-shaped (token links, JWTs, opaque runs, 4–10 digit runs). UUIDs,
numbers and prose survive; `title`/`body` are stripped of credential carriers
rather than filtered by key, because a human reads them back in the feed.

Both layers run in `NotificationLog.save()`, so this is a property of the
table: host code and future channels get it without opting in. Rows written
before the change are rewritten by `manage.py scrub_notification_logs`
(dry run by default; `--commit`, `--older-than-days`, `--delete-older-than-days`).

### 2c. Delivery claims (`delivery.py`, `models.NotificationDelivery`)

Idempotency is a row, not a `.exists()` check: a claim unique on
`(event_id, channel, recipient, template_version)` taken before the dispatch,
confirmed when the provider accepts the message, released when nothing was
delivered. Per channel and recipient, so one channel's success no longer
suppresses another channel's retry; atomic, so two consumers handed the same
event cannot both send. A claim whose process died is taken over after
`DELIVERY_CLAIM_TTL` seconds.

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
  unsubscribe variant whenever `unsubscribe_url` is present — which only a
  type `routing.unsubscribe_allowed` grants one to ever gets.
- Branding without touching any template: `LOGO_URL` + `BRAND_PRIMARY` /
  `BRAND_PRIMARY_DARK` / `BRAND_BG` / `BRAND_TEXT` + `COMPANY_*` are threaded
  into every render as `logo_url` / `brand_*` / `company_*` variables by
  `services.process_notification`; templates use `|default:` fallbacks for the
  colors.
- Pointing a type at a fully custom template needs no ejection at all:
  `EMAIL_TEMPLATES = {"new_message": "myapp/email/new_message.html"}` or the
  `"template"` key of a `TYPES` entry.
- **Gate your override against `docs/templates.json`.** It declares, per type,
  the template path and every context variable this module passes, with its
  provenance. Without it an override rests on honour twice over: rename a
  variable here and Django's `string_if_invalid = ''` renders your letter with
  a blank space; rename a template FILE here and your override shadows nothing
  — while a "does my template resolve from my folder?" test stays green,
  because it asserts a name you chose yourself and that file still exists.

  ```python
  from pathlib import Path
  import stapel_notifications
  from stapel_tools.template_contract import declared_for, load_contract, scan_template

  CONTRACT = load_contract(Path(stapel_notifications.__file__).parent)

  def test_my_override_is_still_an_override():
      path = "notifications/email/otp_code.html"
      declared = declared_for(CONTRACT, path)          # raises if the path is gone
      scan = scan_template(MY_TEMPLATES / path, name=path)
      assert set(scan.variables) <= declared           # raises if a variable is gone
  ```

  Pair it with `stapel_core.templates.strict_template_variables(TEMPLATES)` in
  your TEST settings: an unresolved variable then renders as a visible marker
  and `assert_no_missing_variables(html)` fails the test that rendered it. The
  marker is the net (it catches what a test exercised); the contract is the
  closure.

### 4a. Copy overrides — `TEXT`, the string counterpart of `EMAIL_TEMPLATES`

A host can replace a letter's layout; `TEXT` replaces its words. The subject is
the case that forces it: it lives in no template at all, and
`process_notification` refuses a caller `variables` key that collides with a
translation key, so it cannot be passed per-send either.

```python
STAPEL_NOTIFICATIONS = {
    "TEXT": {
        # a bare string replaces the English default AND becomes the gettext
        # msgid, so your own locale/*/django.po keeps translating it
        "notification.workspace.invitation.subject": "Join {workspace_name}",
        # a dict pins languages outright; the rest fall through the normal
        # cache -> translate service -> gettext -> default chain
        "notification.footer.legal": {"en": "...", "ru": "..."},
    },
}
```

Keys for a type YOU registered through `TYPES` work too — such a type has no
entry in `NOTIFICATION_KEYS`, so `TEXT` is its only copy source.

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
- Language resolution order per notification (`language.py`, ordered by
  *whose statement each step is*): the recipient's **chosen** language,
  asked of profiles at send time via the `profiles.language` comm Function
  → the caller's `language` argument (an anonymous OTP answers a request
  the recipient just made) → the recipient's last **observed** language
  (also from `profiles.language`) → the **sender's** active language, a
  stated decision for the unregistered invitee who has no profile and never
  will until they accept → the project default. Every delivery row records
  which step decided it (`NotificationLog.data["language_source"]`), and a
  deployment that cannot ask at all is flagged at boot
  (`notifications.W001/W002`) and per send (`RECIPIENT LANGUAGE UNASKABLE`).
  This module keeps **no copy** of the language: the mirror it used to keep
  in `UserNotificationSettings` was empty for 100% of users for its entire
  lifetime, and a mirror cannot distinguish "chose nothing" from "the sync
  never ran". Translation strings are `{var}`-formatted with a
  `_SafeFormatter` that blocks attribute/index access.
- **Where the language applies.** Two mechanisms, and a host needs to know
  which one its template uses. (a) Every string this library owns is resolved
  per recipient into the template context before the render — the packaged
  templates carry no prose of their own (gated by
  `tests/test_no_hardcoded_copy_in_templates.py`), so they are per-recipient
  by construction. (b) The render itself runs inside
  `translation.override(lang)` (`services._dispatch`), so `{% trans %}`,
  `{% blocktrans %}`, `|date` and every other locale-sensitive tag in a
  **host** template resolves against the recipient's language rather than the
  language the process happens to have active (a consumer's leftover, or the
  sender's). **The limit:** `get_email_template(notification_type)` takes no
  language argument — there is one template per type — so prose typed
  literally into a template is frozen in the language it was typed in. A host
  whose letters are hardcoded markup gets nothing from either mechanism until
  the words move into `{% trans %}`, `STAPEL_NOTIFICATIONS["TEXT"]` or the
  key registry.

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
| profile-changed | `consume_profiles` | Upsert `UserNotificationSettings` (channel preferences only — the language is asked over comm, not mirrored) |

Functions: this module **calls** `translate.resolve` and `profiles.language`
(the recipient's own language, asked at send time — stapel-profiles >= 0.12.1,
or any provider registered under that name); it registers no comm Functions of
its own. It publishes no events either — the publish side
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

### Contract emission — the `schema` + `flows` + `errors` triad

This module emits its **own** machine-readable API contract, per-module, so
the frontend codegen reads a committed, version-pinned artifact instead of
checking out the monolith aggregate at floating `main`
(contract-pipeline.md §2, verdict **A**: contract = a reviewable commit). Copy
of the `stapel-auth` reference implementation (its `MODULE.md`, "Contract
emission" section, has the full rationale). The triad lives in `docs/`:

```
docs/schema.json   drf-spectacular OpenAPI, this module only, canonical /notifications/api/ prefix
docs/flows.json    generate_flow_docs machine artifact — [] (no @flow_step here)
docs/errors.json   generate_error_keys registry (the original per-module etalon)
```

**Harness** (`_codegen_settings.py` / `codegen_urls.py` / `_codegen.py`,
`Makefile` `contract`/`contract-check`, `tests/test_contract.py`) — same
three-file shape as auth's, wired to `stapel_notifications`'s own settings.
`codegen_urls.py` mounts `stapel_notifications.urls` alone at
`notifications/api/` (the monolith's mount, `svc-app/core/urls.py:39` — no
sibling co-mount; the module's schema is fully self-contained: 4 paths,
5-component closure `DeviceTokenRequest`/`DeviceTokenResponse`/
`FeedItemResponse`/`PaginatedFeedItemResponseList`/`StapelError`).

One non-obvious fact beyond auth's two (`SCHEMA_PATH_PREFIX` pinning,
`SPECTACULAR_SETTINGS` being ignored — see auth's `MODULE.md`): **the
`JWTCookieAuth` security scheme needs its drf-spectacular extension registered
explicitly.** In the monolith, `stapel_gdpr.urls` (co-mounted alongside auth)
calls `get_app_swagger_urls`, which registers the extension as a
process-global side effect — by the time notifications' endpoints are
introspected in that shared process, the registration has already leaked in.
This single-module harness has no such sibling to piggyback on, so
`_codegen.py` calls `stapel_core.django.openapi.swagger._register_jwt_auth_extension()`
directly (idempotent per its own docstring) rather than diverge from the
monolith slice — without it every operation is missing `security` and
drf-spectacular warns "could not resolve authenticator".

**Gate:** `make contract` re-emits; `make contract-check` regenerates into a
temp dir and diffs. Regenerate after any serializer/view/url/error change:

    make contract        # or: python -m stapel_notifications._codegen --out docs

then commit `docs/{schema,flows,errors}.json`.

## Admin categories (`stapel_core.access`, admin-suite AS-5)

Six models, reviewed against the doc's business/ops/secret cut:

- `UserNotificationSettings`, `UserContact` — business (undecorated, implicit
  `@access.standard`). Per-user preference/contact projections a support
  operator legitimately looks up, same shape as the doc's own `Profile`
  example — even though both are event-synced (§7), the sync mechanism is not
  what the category tracks, staff-facing relevance is.
- `TranslationCache` — `@access.ops`. Every row is written only by
  `translations.resolve_and_cache` (initial `sync_translations` command, the
  `translations.changed` subscriber, or the lazy resolve-on-miss path in
  `services._resolve_translations`); no code path or staff workflow ever
  hand-authors or edits a cache row through the admin — a pure sync cache,
  the same "dedup/TTL-shaped machinery" family as the doc's own examples even
  without a literal TTL field.
- `NotificationLog` — `@access.ops`. A passive delivery/audit journal:
  `services.process_notification` is the only writer (`sent`/`failed`/
  `skipped` rows per channel attempt), plus a GDPR-erasure `update()` in
  `gdpr.py`. Matches the doc's own worked example verbatim. Its payload
  columns filter themselves in `save()` (§2b) — the admin renders this table.
- `NotificationDelivery` — `@access.ops`. The delivery-claim ledger (§2c);
  answers "why was this redelivery suppressed", written only by
  `delivery.claim`/`confirm`/`release`.
- `DevicePushToken` — `@access.secret`. Carries a bearer FCM push-token
  string (`token`, unique) that authorizes sending to a device; deactivated
  automatically on delivery failure (`channels/push.py`), never staff-edited.
  Matches the doc's own worked example verbatim. The `token` field name
  already matches `StapelModelAdmin`'s auto-detect substring list — no
  `secret_fields` pin needed.

`admin.py`'s `TranslationCacheAdmin`, `NotificationLogAdmin`, and
`DevicePushTokenAdmin` now subclass
`stapel_core.django.admin.base.StapelModelAdmin` (was plain
`admin.ModelAdmin`) so the category is enforced/rendered (read-only fields,
masked `token`, HIGH/superuser view gating). `UserNotificationSettingsAdmin`
and `UserContactAdmin` are unchanged.

Attribute-only change: no migrations (`makemigrations notifications --check
--dry-run` reports no changes).

## Anti-patterns (never fork for these)

| Don't | Do instead |
|---|---|
| Fork to add or re-route a notification type | `STAPEL_NOTIFICATIONS["TYPES"]` entry (§2); verify with `manage.py check_notifications` |
| Fork or edit site-packages to rebrand emails | `LOGO_URL` + `BRAND_*` + `COMPANY_*` settings; per-type `EMAIL_TEMPLATES`; `eject_notification_templates` for structural edits (§4) |
| Fork to add an email/SMS/push provider (SendGrid, Postmark, …) | Provider class in your project + dotted path in `*_PROVIDER` (§3) |
| One-off email for an unregistered type by hacking templates | `request_notification(..., content_html=/content_text=)` after opening `RAW_CONTENT` (§2a) — off by default |
| Read your own variables back out of `NotificationLog.data` | Declare them: `"telemetry"` in the routing entry or `TELEMETRY` in settings (§2b) |
| Import `stapel_translate` (or any stapel module) from here, or vice versa | Comm surface only: `translate.resolve` Function + `translations.changed` event (§5) |
| Mirror another module's fact into a local table and read the copy | Ask the owner by name over comm at the moment you need it (`profiles.language` is the worked example: the mirror it replaced was empty for every user, silently) |
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
