# CONFIG.MD — stapel-notifications

Config registry for **stapel-notifications** (`static-scaffold-and-config.md`
§2). One row per configuration key the module reads, its **source** (`env` =
the process environment / the `STAPEL_NOTIFICATIONS` settings dict; `vault` = a
secret that belongs in the `stapel_core.secrets` provider seam), what it is
for, whether it is required, and its default. The **Type** column is this
module's addition to the canonical five: half these keys are registries and
merge-maps rather than scalars, and "what shape does it take" is the first
question anyone setting one asks.

Every key is read through `notifications_settings`
(`stapel_notifications.conf.NotificationsAppSettings`, namespace
`STAPEL_NOTIFICATIONS`). Resolution order per key:
`settings.STAPEL_NOTIFICATIONS` dict → a flat Django setting of the same name →
environment variable → the default below.

**Exception — the four `*_PROVIDER` keys are never read from the
environment.** They are declared `import_strings`, which stapel-core (≥0.24.0)
treats as implicitly `no_env`: such a key names the class this process imports
and runs to put a passcode, a magic link or an account-closure notice on the
wire, so anything able to set an env var in the pod (a leaked value, a sibling
container's config, a stray `export` in an entrypoint) must not be able to
choose it. For those four, resolution stops at `settings.STAPEL_NOTIFICATIONS`
→ flat Django setting → default; a same-named environment variable is ignored
and reported by `manage.py check` as `stapel_core.conf.W001`. A deployment that
genuinely must select a provider per environment adds that key to
`env_overridable=` in `conf.py` — deliberately not preconfigured, because
forgetting a flag must leave the process closed, never open. They keep source
`env` in the table below because that is the column's registry-wide meaning
(the settings dict); the closure is stated per row.

**Closed by default.** `EMAIL_PROVIDER`, `SMS_PROVIDER` and `TELEGRAM_PROVIDER`
ship as `unconfigured` — a provider that *raises* on every send — rather than
as `mock`, which returns and is therefore counted as a delivery: a zero-config
deployment used to journal `status="sent"` for every OTP it had only logged.
`RAW_CONTENT` ships `off`, `TELEMETRY` is deny-by-default, and nothing in the
built-in catalog routes to `telegram`. Log-only delivery is still one explicit
setting away (`"mock"`), and `checks.W005` warns when a non-delivering provider
is in force with `DEBUG=False` on a channel the effective registry routes to.

## stapel-notifications

### Channel providers — the implementation seam (never env-selected)

| Key | Type | Source | Purpose | Required | Default |
|-----|------|--------|---------|----------|---------|
| EMAIL_PROVIDER | str | env | axis: which class sends email — `resend` \| `smtp` \| `mailgun` \| `mock` \| `unconfigured`, or a dotted path to a class with `.send(recipient, subject, html_body, headers)`. Env-closed (see above). | no | unconfigured |
| SMS_PROVIDER | str | env | axis: `gatewayapi` \| `twilio` \| `mock` \| `unconfigured`, or a dotted path to a class with `.send(phone, body)`. Env-closed. | no | unconfigured |
| PUSH_PROVIDER | str | env | axis: `fcm` \| `mock` \| `unconfigured`, or a dotted path to a class with `.send(user_id, title, body, data) -> int`. Env-closed. Keeps `fcm`: it already refuses loudly without credentials. | no | fcm |
| TELEGRAM_PROVIDER | str | env | axis: `mock` \| `unconfigured`, or a dotted path to a class with `.send(chat_id, text)`. No delivering built-in ships — a bot token is the deployment's own sending identity, so the client is app-layer. Env-closed. | no | unconfigured |

### Provider credentials

Read lazily at send time, never frozen at import. Telegram has no credential
key here on purpose: the token lives in the host's own provider class, which is
the only thing that ever touches it.

| Key | Type | Source | Purpose | Required | Default |
|-----|------|--------|---------|----------|---------|
| RESEND_API_KEY | str | vault | Resend API key; the send raises without it when `EMAIL_PROVIDER=resend`. | no | (empty) |
| MAILGUN_API_KEY | str | vault | Mailgun API key; required when `EMAIL_PROVIDER=mailgun`. | no | (empty) |
| MAILGUN_DOMAIN | str | env | Mailgun sending domain; required alongside the key when `EMAIL_PROVIDER=mailgun`. | no | (empty) |
| GATEWAYAPI_TOKEN | str | vault | GatewayAPI token; required when `SMS_PROVIDER=gatewayapi`. | no | (empty) |
| GATEWAYAPI_SENDER | str | env | sender name shown on GatewayAPI messages. | no | Stapel |
| TWILIO_ACCOUNT_SID | str | vault | Twilio account SID — the username half of the Twilio credential pair; rotate it with the token, not separately. | no | (empty) |
| TWILIO_AUTH_TOKEN | str | vault | Twilio auth token; with the SID, required when `SMS_PROVIDER=twilio`. | no | (empty) |
| TWILIO_PHONE_NUMBER | str | env | the `from` number Twilio sends with. | no | (empty) |
| GOOGLE_APPLICATION_CREDENTIALS | str | env | filesystem path to the FCM service-account JSON — the path is config, the file it names is the secret. Unset disables push with a logged warning. | no | (empty) |

### Notification registry and policy

| Key | Type | Source | Purpose | Required | Default |
|-----|------|--------|---------|----------|---------|
| TYPES | dict | env | notification-type registry merged **over** `routing.NOTIFICATION_ROUTING`: `{"<type>": {"channels": [...], "group": "auth\|messages\|system", "template": ...}}`. An entry REPLACES a built-in wholesale — it does not merge, which is what `checks.E002` guards. | no | {} |
| EMAIL_TEMPLATES | dict | env | per-type email-template overrides merged over `routing.DEFAULT_EMAIL_TEMPLATES`; loses to a `"template"` key on the routing entry. | no | {} |
| TEXT | dict | env | per-key copy registry merged **over** `translation_keys.NOTIFICATION_KEYS`. A bare string replaces the English default and becomes the gettext msgid; a `{lang: str}` dict pins languages outright. The only copy source for a host-registered type. | no | {} |
| RAW_CONTENT | str | env | axis: may a caller supply the body of a branded letter — `off` \| `text` (body yes, markup reduced to text) \| `html` (trusted producers only; boot-warns `W004`, because caller markup inside this brand's layout is first-party phishing with valid SPF/DKIM). | no | off |
| TELEMETRY | dict | env | per-type allowlist of caller variables the delivery journal may store: `{"<type>": [...], "*": [...]}`. Deny-by-default, and a declared key is still dropped when its value is credential-shaped. | no | {} |
| DELIVERY_CLAIM_TTL | int | env | seconds after which a delivery claim whose process died may be taken over by a redelivery, so a crash between claim and send cannot silence a notification forever. | no | 900 |

### Branding and template variables

Threaded into every email render by `services.process_notification` as the
`company_*` / `brand_*` / `logo_url` context variables, so a host rebrands
without touching a template.

| Key | Type | Source | Purpose | Required | Default |
|-----|------|--------|---------|----------|---------|
| COMPANY_NAME | str | env | template variable `company_name`; also the text wordmark when no logo is set. | no | Stapel |
| COMPANY_URL | str | env | template variable `company_url`; its host becomes `company_host` in the footer. | no | (empty) |
| COMPANY_ADDRESS | str | env | postal address for the legal footer (`company_address`). | no | (empty) |
| COMPANY_YEAR | str | env | copyright year (`company_year`); empty resolves to the current year at send time. | no | (empty) |
| FRONTEND_URL | str | env | base URL for the generated `unsubscribe_url` / `manage_notifications_url`. | no | (empty) |
| LOGO_URL | str | env | set → emails embed `<img src="LOGO_URL">`; empty → no image at all and the header renders the company name as text. This package ships no logo of its own. A `data:` URI does not work — mail clients block it. | no | (empty) |
| BRAND_PRIMARY | str | env | logo/accent colour (`brand_primary`). | no | #00AEEF |
| BRAND_PRIMARY_DARK | str | env | buttons and links (`brand_primary_dark`). | no | #2A90D9 |
| BRAND_BG | str | env | page background (`brand_bg`). | no | #F5F5F6 |
| BRAND_TEXT | str | env | headings and body copy (`brand_text`). | no | #1C1D20 |

### Delivery and i18n tuning

| Key | Type | Source | Purpose | Required | Default |
|-----|------|--------|---------|----------|---------|
| SMTP_TIMEOUT | int | env | connect/read timeout in seconds used by the SMTP provider when the host has not set Django's `EMAIL_TIMEOUT`; without one a slow mail server hangs the request until the proxy gives up. | no | 15 |
| LANGUAGES | list[str] | env | languages `manage.py sync_translations` prefetches; the lazy resolve-on-miss path covers anything not listed. | no | [en] |

### Bus consumer groups

Read with `os.getenv` inside the consumer commands rather than through the
namespace (each carries a deliberate `# noqa: CFG001`): a consumer group is a
property of the *process* — the same image runs three of them — and must be
settable before Django settings exist. Listed here because they are real
operational knobs, and a registry that omits them hides them from ops.

| Key | Type | Source | Purpose | Required | Default |
|-----|------|--------|---------|----------|---------|
| NOTIFICATIONS_CONSUMER_GROUP | str | env | consumer group for `consume_notifications` (the `notification.requested` dispatcher). | no | stapel.notifications.processor |
| NOTIFICATIONS_CONSUMER_GROUP_CONTACTS | str | env | consumer group for `consume_contacts` (the `UserContact` email/phone/telegram_chat_id projection). | no | stapel.notifications.contacts |
| NOTIFICATIONS_CONSUMER_GROUP_PROFILES | str | env | consumer group for `consume_profiles` (the `UserNotificationSettings` preference projection). | no | stapel.notifications.profiles |

## Not configuration — the per-recipient preference plane

`UserNotificationSettings` carries eight booleans, one per channel×group pair —
`{email,push,sms,telegram}_{messages,system}` — enforced by
`services._should_send`. They are **user data**, projected from profiles over
the bus (`consume_profiles`), not deployment settings: nothing in
`STAPEL_NOTIFICATIONS` sets or overrides them, and the `auth` group ignores
them entirely because security mail is mandatory.

The pair a channel×group needs must exist, or the type is undeliverable on that
channel: `checks.E004` refuses at boot a non-`auth` type routed to a channel
with no matching field. The channels that have one are `email`, `sms`, `push`,
`telegram`.
