# Changelog

## Unreleased

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
- Iron branding leftovers removed: `COMPANY_NAME` default is `Stapel`,
  GatewayAPI SMS sender default `IronMemo` → `Stapel`, bus consumer groups
  `iron.notifications.*` → `stapel.notifications.*` (overridable via the
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
