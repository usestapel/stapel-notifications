# Changelog

## Unreleased

### Added
- `STAPEL_NOTIFICATIONS` settings namespace (`stapel_notifications.conf`):
  every previously hardcoded knob — providers, credentials, company
  branding, FRONTEND_URL — is overridable without forking. Legacy flat
  settings (`EMAIL_PROVIDER`, `TWILIO_*`, …) keep working.
- Open notification-type registry: `STAPEL_NOTIFICATIONS["TYPES"]` adds or
  overrides types (channels, group, email template) on top of the built-in
  catalog.
- Channel providers accept dotted paths (`"myapp.email.SendgridProvider"`)
  besides built-in short names — same escape hatch as captcha backends.
- `workspace.invitation` notification type + email template (used by
  stapel-workspaces invitations).
- `user.deleted` comm Action subscriber erases contact data.

### Changed
- `routing.get_email_template()` replaces the module-level EMAIL_TEMPLATES
  lookup; per-type `"template"` keys win over built-ins.
- Iron branding leftovers removed (`Stapel` defaults).

### Packaging
- Email templates, static and event schemas ship in the wheel.
- Django floor raised to 5.1.
