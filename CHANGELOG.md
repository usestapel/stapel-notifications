# Changelog

## Unreleased

### Security — an environment variable could choose who sends your one-time passcodes (BREAKING for a deployment that set `EMAIL_PROVIDER`/`SMS_PROVIDER`/`PUSH_PROVIDER` as a bare env var)

`STAPEL_NOTIFICATIONS` declared no `import_strings`, so `AppSettings` resolved
every key — including the three provider keys — from `os.environ` when the
settings dict did not carry it. Those keys are not data: each names the **class
this process imports and runs** to put an OTP, a magic link, a password reset
and an account-closure notice on the wire. Anything able to set an env var in
the pod (a leaked value, a sibling container's config, a stray `export` in an
entrypoint) could therefore redirect every passcode this service sends to a
class of its choosing, with no trace in the project's settings module.

stapel-core 0.24.0 closed this class fleet-wide by making `import_strings` keys
implicitly `no_env` — but only for keys a module actually declares. This module
declared none, so the mechanism did not reach it.

Now `EMAIL_PROVIDER`, `SMS_PROVIDER` and `PUSH_PROVIDER` are declared
`import_strings` (`conf.PROVIDER_SETTINGS`), so the shared rule covers them:
the `STAPEL_NOTIFICATIONS` dict and a flat Django setting still choose the
provider, and the environment does not. Values stay strings resolved by the
registry-aware `channels` resolver, so built-in short names (`resend`, `twilio`,
`fcm`, `mock`, `unconfigured`) keep working and an unresolvable value keeps
raising `ImproperlyConfigured` / `notifications.E003` rather than a bare
`ImportError`.

**Upgrade note — this change is silent by nature, so read this one.** A
deployment that selected a provider with a bare `EMAIL_PROVIDER=…`,
`SMS_PROVIDER=…` or `PUSH_PROVIDER=…` environment variable **silently stops
doing so**: no error, no log line, the service simply runs the value from your
settings module or the shipped default (`unconfigured` for email/SMS, which
raises on send and journals `status="failed"`). Nothing about the running
process announces that your variable is being ignored — which is why the rule
carries its own alarm:

```
$ python manage.py check
WARNINGS:
?: (stapel_core.conf.W001) Environment variable EMAIL_PROVIDER is set but
   ignored: STAPEL_NOTIFICATIONS['EMAIL_PROVIDER'] is an import_strings key …
```

`manage.py check` names every such variable (names only — never its value).
Run it against your deployment's environment before rolling this out.

Two remedies, in order of preference:

1. **Move the value into your settings module** (recommended — the project's
   own settings are trusted, the environment is not):

   ```python
   STAPEL_NOTIFICATIONS = {
       "EMAIL_PROVIDER": "myproject.email.SendgridProvider",
       "SMS_PROVIDER": "twilio",
   }
   ```

   Reading the env var yourself in `settings.py` (`os.environ["…"]`) is the
   same thing said explicitly, and is fine: the decision is then in code you
   own and can review.

2. **If this deployment genuinely must select the implementation from the
   environment**, say so once, by name, with core's `env_overridable` allowlist
   in `conf.py`'s `AppSettings` declaration. It is deliberately **empty** in
   this release and is not added preemptively for any key — forgetting a flag
   must leave the process closed, never open.

Unsetting the stale variable also clears the warning.

## [0.11.0] — 2026-08-14

### Fixed — a zero-config deployment could believe it had delivered an OTP (BREAKING: `EMAIL_PROVIDER`/`SMS_PROVIDER` no longer default to `mock`, and an unresolvable provider raises)

Security audit 2026-08-11, NOTIFY-02 (P1). Two halves of the same defect.

**The shipped default sent nothing and said it had.** `EMAIL_PROVIDER` and
`SMS_PROVIDER` defaulted to `"mock"`, whose `send` logs a line and returns —
and `services._dispatch` counts a provider that returned as a delivery. So a
production service that had never been pointed at a mailer wrote
`NotificationLog(status="sent")` for every passcode, magic link, password
reset and account-closure notice it had only logged. Nothing in the system
disagreed: the deliberate `skipped` path exists for "no address on this
channel", and this was not that.

**Any unresolvable provider name silently became that mock.** The shared
resolver answered an unknown short name (`"resedn"`) — or a dotted path whose
import raised — with the channel's mock class and a `WARNING`. That is a typo
downgrading a live mailer to a log line, and, worse, a provider module that
lost a dependency in a deploy doing the same to a mailer that worked
yesterday.

Now:

* `EMAIL_PROVIDER` and `SMS_PROVIDER` default to **`"unconfigured"`**, a
  provider that raises `ImproperlyConfigured` on every send. The same
  zero-config deployment now journals `status="failed"` and escalates through
  the existing `NOTIFICATION UNDELIVERABLE` error. `PUSH_PROVIDER` keeps
  `"fcm"`, which already refused loudly without credentials.
* An unknown short name or an unimportable dotted path **raises** instead of
  falling back. There is deliberately no fallback left: the fallback was a
  mock that reported success.
* New system check **`notifications.E003`** — a provider setting that names
  nothing this process can load, asked at boot instead of at the first
  passcode.
* New system check **`notifications.W005`** — a routed channel whose provider
  delivers nothing (`mock` or `unconfigured`) while `DEBUG=False`. Silent
  under `DEBUG`; silence it in production with `SILENCED_SYSTEM_CHECKS` if the
  channel is deliberately dark.

**Upgrade note.** A deployment that relied on the implicit `mock` default —
CI, a local checkout, a staging box that must not mail real people — now
raises on send. The opt-out restoring the old behaviour is one explicit
setting, and it is explicit on purpose:

```python
STAPEL_NOTIFICATIONS = {
    "EMAIL_PROVIDER": "mock",   # log only, no delivery — was the silent default
    "SMS_PROVIDER": "mock",
}
```

A deployment that already names its providers is unaffected, except that a
name which never resolved — and had therefore been sending nothing for as long
as it had been wrong — now fails the boot check rather than the recipient.

### Fixed — `_should_send` treated an unreadable preference as consent (BREAKING: an unrecognised channel+group pair is now refused, not sent)

Security audit 2026-08-11, NOTIFY-03 (P2). `services._should_send` failed open
on a preference it could not read: an unrecognised `f"{channel}_{group}"` field
logged "defaulting to send" and returned `True`, and the trailing
`getattr(settings_obj, pref_field, True)` did the same on a settings object
that no longer carried the field. Either way the result was mail the recipient
had no switch for anywhere in the API — the harm `notifications.E001` refuses
at boot for the group half of the pair.

The channel half had no check at all. `{"channels": ["webhook"], "group":
"system"}` names a real group, so E001 stayed silent, while
`UserNotificationSettings` has no `webhook_system` field.

Now:

* An unrecognised preference field is **refused**, at `ERROR` level. Sending is
  the half of this decision that cannot be taken back once it is wrong.
* New system check **`notifications.E004`** — a type routed to a channel with
  no preference field for its group. The `auth` group stays exempt: it is
  mandatory by design and deliberately has no preference field.

Unchanged on purpose: a recipient with **no** `UserNotificationSettings` row
still receives non-`auth` mail. Every preference field on that model defaults
to `True`, so an absent row and a default row mean the same thing; refusing
there would silence all mail for every user who never opened their settings,
which is a product decision this library does not get to make unilaterally.

**Upgrade note.** A host that registered a type on a channel this library
carries no preference for was sending mail nobody could switch off; that type
now sends nothing on that channel and `manage.py check` refuses the boot with
`E004`. There is no runtime opt-out, because the honest fix is bounded and
mechanical: route the type to `email`, `sms` or `push`, or drop the channel
from the entry.

### Fixed — the delivery journal kept the credentials it delivered (BREAKING: `NotificationLog.data` is now deny-by-default)

Security audit 2026-08-11, NOTIFY-01 (P1). Every scalar the caller passed in
`variables` was copied into `NotificationLog.data`. For this library's own
built-in types that is, literally: the one-time passcode (`otp_code.code`),
the sign-in link with its token in the query string (`magic_link_login`), the
invitation URL that both creates an account and joins it to a workspace, and
the initial password of an org-provisioned account — persisted for the life of
the deployment in a table the Django admin renders.

The replacement is deny-by-default in two independent layers (`telemetry.py`):

* **Keys.** A caller variable is journalled only where somebody DECLARED it —
  the routing entry's `"telemetry": [...]`, `STAPEL_NOTIFICATIONS["TELEMETRY"]`
  (`{"<type>": [...], "*": [...]}`), or the deep links the push feed reads back
  out of `data` to open the thing a notification is about. A denylist of
  known-bad names would have to be right about a name nobody has invented yet;
  an allowlist has to be right about names somebody wrote down.
* **Shapes.** A declared key is still dropped — replaced by `[redacted]` —
  when its VALUE is credential-shaped: a link carrying a query/fragment token
  or an opaque last segment, a JWT, a long high-entropy run, a 4–10 digit run.
  So declaring `reset_url` as telemetry does not get the reset token into the
  table. UUIDs, numbers and prose are identifiers and stay.

Both layers run inside `NotificationLog.save()`, so the guarantee belongs to
the **table**, not to one call site: host code, a future channel and a data
migration are covered by the same mechanism. `title`/`body` are copy a human
reads back in the feed, so they are stripped of credential carriers (links
with parameters, JWTs, long opaque runs) rather than filtered by key.

The GDPR erasure path clears `title`/`body` along with `recipient`/`user_id`:
an anonymised row that still quotes what was written to that person was not
anonymised, only harder to query.

**Historical rows are not rewritten by the migration** — a migration that
silently edits an audit table is the wrong place for it. `manage.py
scrub_notification_logs` does it on the operator's word (dry run by default,
`--commit` to write, `--delete-older-than-days N` to shred). Database backups
holding the same values are outside anything this library can reach and belong
in the incident plan.

*Upgrade note.* A host that reads `NotificationLog.data[...]` for its own
analytics gets `{}` where it used to get the caller's variables. Declare the
keys — `"telemetry"` in the routing entry, or `TELEMETRY` in settings — and
they come back, minus anything credential-shaped.

### Fixed — anything on the bus could send branded mail of its own composition (BREAKING: `RAW_CONTENT` defaults to "off")

Security audit 2026-08-11, NOTIFY-02 (P1). `process_notification(...,
content_html=...)` accepted an **unregistered** notification type with a
caller-supplied body and an explicit recipient, and `_raw_content.html`
rendered that body with `|safe` inside the brand layout. A compromised
producer, a leaked broker credential or an internal service with more reach
than it needs could therefore send mail that is, byte for byte, this platform
writing to its own users — with any link it liked, and valid SPF/DKIM. No
sanitiser fixes that: `<a href="https://not-us.example/login">` is
harmless-looking markup and is the whole attack.

The hatch is now a declaration a deployment makes,
`STAPEL_NOTIFICATIONS["RAW_CONTENT"]`:

| value | meaning |
|---|---|
| `"off"` (default) | no hatch: `content_html`/`content_text` are ignored and an unregistered type is refused, with an ERROR naming the setting |
| `"text"` | ad-hoc bodies allowed, no caller markup — HTML is reduced to its text, which the layout escapes |
| `"html"` | the pre-Unreleased behaviour, for a deployment whose producers are all first-party and authenticated; boot warns (`stapel_notifications.W004`) |

An unrecognised value falls back to `"off"`: a typo in a security switch must
not be the thing that opens it. `manage.py check_notifications` follows the
setting — with the hatch shut, a `content_html=` call site on an unregistered
type is an ERROR, because a gate that certifies the one call site guaranteed
to be silent is worse than no gate.

*Upgrade note.* A deployment that legitimately sends ad-hoc mail sets
`RAW_CONTENT` to `"text"` (recommended) or `"html"` before upgrading;
otherwise those sends stop and say so in the log.

### Fixed — delivery idempotency was check-then-act, and answered for the wrong thing

Same audit finding. `process_notification` opened with
`NotificationLog.objects.filter(data__event_id=..., status="sent").exists()`,
which is wrong twice. **Not atomic**: two consumers handed the same event by
an at-least-once broker both read "no row yet" and both send, and the window
is the whole render + SMTP round trip — exactly when a redelivery arrives.
**Too coarse**: one `sent` row suppressed the WHOLE event, so a passcode that
reached the recipient's email but had no phone number to reach on SMS could
never be retried on SMS; the email row answered for both.

New table `NotificationDelivery` (migration `0006_delivery_claim`) with a
unique constraint on `(event_id, channel, recipient, template_version)`. The
claim is taken in the database before the dispatch, confirmed when the
provider takes the message, and released when nothing was delivered so a retry
can take it again. A claim whose process died is taken over after
`DELIVERY_CLAIM_TTL` seconds (default 900) — a crash between claim and send
must not silence a notification forever. `template_version` is this library's
version of a letter, the effective template path (or `"raw"`), so re-pointing
a type at a new template is a new delivery rather than a suppressed duplicate.

*Upgrade note.* Nothing is backfilled from the old `data->>'event_id'` rows,
so the first redelivery of an event already delivered before the migration can
send once more. The window is the broker's retention; paying it once is the
price of moving idempotency off a check-then-act on a journal.

### Not fixed here — the producer side

The audit also asks to authenticate and scope producers and to allowlist
recipients. A library that is handed an event cannot do either: whether the
bus requires per-producer credentials, and which recipients a producer may
name, are deployment facts. `RAW_CONTENT="off"` removes the value of reaching
the bus without them; it does not replace them.

## [0.10.0] — 2026-08-10

### Fixed — this module translates only the keys it owns

`translations/errors.{ru,es}.json` each carried 41 verbatim copies of the
cross-cutting keys stapel-core owns. None was an intentional reword: before
stapel-core 0.22.0 the coverage gate took its canon from the whole in-process
registry, so going green *required* copying them. Core ships those catalogs
itself now and the loader merges them, so the copies were a second, drifting
shadow of texts this module does not answer for — and the gate
(`test_catalog_gate_green`) went red on them, which is what held this release
untagged.

ru and es go 43 → 2 keys: `error.400.notification_type_unknown` and
`error.404.unsubscribe_token_invalid`, the two this module actually owns.

The reference does not move. `docs/errors.{en,ru,es}.md` regenerated after the
deletion are **byte-identical** to the ones regenerated before it, because
stapel-core 0.23.1 resolves a key this module does not own from its owner's
catalog (`module_catalog`). Pruning without that fix would have silently
downgraded 41 Russian rows to `_(en)_` English fallbacks — a documentation
regression traded for a duplication one. Verified as bytes, and
`test_error_reference_matches_a_fresh_regeneration` keeps it verified, so a
committed reference can no longer be green while being unreproducible.

The `stapel-core` pin moves to `>=0.23.1`: with an older core these pruned
catalogs resolve to English at runtime.

### Fixed — a passcode could carry a one-click opt-out from all security mail (BREAKING: two new boot-time errors)

Reported from real Gmail (2026-08-09): an "Unsubscribe" banner on every
message, one-time passcodes and sign-in alerts included. Gmail renders that
button prominently, and `List-Unsubscribe-Post: One-Click` is
machine-actionable — a mail client, an anti-abuse scanner or a security
appliance may POST the URL with no human involved. This library's token is
minted per (user, GROUP, channel), so one automated click on a security
letter stops the mail that tells the recipient their account is under attack.

Version 0.8.0 answered a narrower version of this per letter (the personal
workspace invitation). This closes the class. The predicate was

    group != "auth" and "unsubscribe_url" in all_vars and not is_transactional(type)

— a denylist, under which every way of *failing to say* `"auth"` produced a
one-click opt-out. All of these did, and none of them look wrong at a glance:

* a routing entry with no `group` key at all;
* a misspelled or unknown group (`"Auth"`, `"sistem"`);
* a settings override of a built-in security type, because
  `STAPEL_NOTIFICATIONS["TYPES"]` **replaces** an entry rather than merging
  into it — `{"otp_code": {"channels": ["email"]}}`, a host dropping the SMS
  channel, silently dropped `"group": "auth"` with it;
* an ad-hoc `content_html` send, whose synthesised entry answered the
  registry lookup with `None`;
* a caller passing `unsubscribe_url` as a plain template variable.

The decision is now one function, `routing.unsubscribe_allowed(routing)`, and
it is an **allowlist**: the group must be in `routing.UNSUBSCRIBABLE_GROUPS`
(`messages`, `system`) and the type must be neither `"transactional": True`
nor the new `"security": True`. It takes the effective routing *entry*, not a
type name, so the raw-content escape hatch is judged by the same rule; it is
asked once where `unsubscribe_url` is minted and again where the headers are
set, both times from that entry rather than from the presence of a variable.
`may_carry_unsubscribe(type)` is the reader for host code and UIs.

`"security": True` is the second orthogonal flag, alongside `transactional`.
It says "account-security mail that must nevertheless stay switch-off-able"
— the letter that cannot live in `auth` because the recipient keeps the
right to turn it off, but must never be handed a one-click opt-out. Like
`transactional`, it governs the affordance only: the group still decides the
preference.

**Boot gates** (`manage.py check`), because a registration defect that only
shows up in somebody's inbox is not a runtime symptom:

* `notifications.E001` — a type registered under a group outside the now
  closed `routing.VALID_GROUPS`. The group also names the recipient's
  preference field, so mail under a misspelled group is mail nobody can
  switch off.
* `notifications.E002` — a settings override that drops a built-in security
  type's classification. There is no reading of that edit under which a
  passcode should become unsubscribable.
* `notifications.W003` — a type *named* like security mail (`otp`,
  `password`, `login`, `session`, `mfa`, `device`, `recovery`, …) sitting in
  a bulk-mail group. Heuristic, so warn-level; answered by the declaration it
  asks for.

**Behaviour change**: a host whose type has a missing or unknown group no
longer gets `List-Unsubscribe` on that type — and `manage.py check` now fails
rather than sending it. Genuine list mail is unaffected: an allowlisted group
still carries both headers, RFC 8058 compliance intact.

`tests/test_unsubscribe_policy.py` asserts on the headers of actually
rendered messages — every packaged security letter individually, plus each
hole above. Restoring the old predicate turns four of them red.

### Fixed — a host template rendered in the sender's language, not the recipient's

`services._dispatch` called `render_to_string` outside any language override,
so `{% trans %}`, `{% blocktranslate %}`, `|date` and every other
locale-sensitive tag in a **host** template resolved against whatever
language the process had active: the sender's in a web process, a leftover in
a consumer. The render now runs inside `translation.override(lang)` with the
language resolved for the recipient.

This does **not** change the packaged letters, and the distinction matters
for anyone diagnosing this. Every string this library owns is resolved per
recipient into the template context *before* the render, and the packaged
templates carry no prose of their own (gated by
`tests/test_no_hardcoded_copy_in_templates.py`) — so they already followed
the recipient, which is why a live measurement of a packaged letter (Russian
active in the process, an English-speaking recipient) came out English in
both subject and body. Both facts are now tests, and removing the override
leaves those two green while turning the host-template ones red.

**And the limit, stated rather than implied**: `get_email_template(type)`
takes no language argument. There is one template per type. Prose typed
literally into a template is frozen in the language it was typed in, and
neither mechanism moves it — the words have to live in `{% trans %}`,
`STAPEL_NOTIFICATIONS["TEXT"]` or the key registry.

Closed as a mechanism rather than a call site:
`tests/test_render_language.py` parses every module in the package and fails
on any `render_to_string` that is not lexically inside
`translation.override(...)`. The point fix is one `with` that the next call
site forgets — this module already had an override around its gettext lookup
three hundred lines above the render that lacked one.

## [0.9.0] — 2026-08-10

### Changed — the recipient's language is asked, not mirrored (BREAKING: two columns dropped)

There was no path by which a recipient's language reached their mail. Measured
on the meettoday sandbox, 2026-08:

    stapel_notifications.UserNotificationSettings   0 rows,  0 with a language
    stapel_profiles.Profile.app_language            None for all 66 profiles
    stapel_profiles.Profile.auto_detected_language  ru for 57, en for 6

The third line is the one that convicts the design. Profiles *had* a language
signal for 63 of 66 people; notifications had none for anybody, because it read
its own mirror of that field. The mirror was fed by `consume_profiles`, which
in this deployment cannot run at all (a standalone bus consumer on an
in-process bus — core 0.14.2 refuses instead of restart-looping) and which, in
a deployment where it could, listens on `stapel.profiles.profile-changed` while
the comm plane publishes under the action name `profile.changed`. Two
independent breaks, one silent outcome: `saved` was `None` for 100% of users,
so the chain fell through to `_active_language()` — **the language of whoever
pressed the button**. Invite from a Russian UI and the invitee got Russian mail
whoever they were; a colleague's finished English bodies could never be
selected.

The mirror is gone. `UserNotificationSettings.language` and
`.auto_detected_language` are dropped (migration `0005_drop_language_mirror`);
`consume_profiles` no longer syncs them. The language is now asked of the
module that owns it, at send time, through the `profiles.language` comm
Function (stapel-profiles >= 0.12.1) — which works identically in a monolith
and in a split deployment, because the transport is deployment configuration.

**The resolution order is now ordered by whose statement each step is**
(`language.py`), and the last step is a decision rather than a fallthrough:

1. `recipient_choice` — the language the recipient CHOSE (profiles). Nobody
   outranks a person's own statement about how they are written to; in
   particular a *local mirror* that is empty for every user has no business
   sitting above an explicit argument, which is where it used to sit.
2. `caller` — the `language` argument. The caller knows something about THIS
   message: an anonymous OTP answers a request the recipient just made. It
   ranks below (1) because what a caller passes is the *request's* language,
   which for one person notifying another is the sender's.
3. `recipient_detected` — the recipient's last OBSERVED language (profiles).
   About the right person, never stated by them.
4. `sender` — the active language of the process doing the send. **Stated
   decision for the unregistered invitee**, who has no profile and will not
   have one until they accept: the only fact in the system about how to
   address them is that someone who presumably knows them wrote from a UI in
   this language. It is a guess, and it is labelled one.
5. `default` — the project's default language (no request in scope at all).

So: a registered user who chose a language gets that language; one who chose
nothing gets the language they were last seen in; an unregistered invitee gets
the sender's, deliberately.

### Added — "no preference" and "never delivered" stop being the same answer

That ambiguity was the defect underneath the defect: both produced `None` and
nobody could tell which. A call cannot hide it — it answers or it raises — and
the difference is now recorded on both sides:

* every delivery row carries `NotificationLog.data["language_source"]` (one of
  the five names above), plus `recipient_language_unaskable: true` when
  profiles could not be reached at all. `SELECT data->>'language_source'`
  answers "how many letters did we address on a guess" without waiting for a
  complaint;
* a failed/absent provider logs `RECIPIENT LANGUAGE UNASKABLE` (greppable, like
  `NOTIFICATION UNDELIVERABLE`) instead of silently degrading;
* `manage.py check` refuses to stay quiet about a deployment that cannot ask at
  all: `notifications.W001` (in-process transport, no provider registered) and
  `notifications.W002` (http transport, no `FUNCTION_ROUTES` prefix matching
  `profiles.`). Silence with `SILENCED_SYSTEM_CHECKS` if the deployment really
  is single-language.

`UserNotificationSettings` keeps only its channel×group booleans, and those are
the same mirror shape with the same exposure — the next thing to move onto the
comm plane.


## [0.8.0] — 2026-08-10

### Added — `docs/templates.json`: templates stop being an undeclared surface

Templates are the largest extension seam this library has — a host drops a file
of the same name into a directory that resolves first and the letter is theirs.
Until now nothing declared that seam. `capabilities.json`, `errors.json`,
`flows.json`, `schema.json` and `llms.txt` between them name not one template
path and not one context variable, so a host obtained the contract by reading
`services.py`, and this library could break that host twice over with every
test on both sides staying green:

* rename a context variable and Django's `string_if_invalid = ''` renders the
  hole as an empty string — an OTP mail with a blank code, 200 OK, nobody can
  log in;
* rename a template file and the host's override shadows nothing, so the
  LIBRARY's letter goes out under the host's brand — while the host's own
  "resolves from our folder, not site-packages" guard stays GREEN, because it
  asserts the name the host chose and that file still exists.

The sixth artifact declares, per notification type: the template it resolves
to, the whole `{% extends %}`/`{% include %}` chain, and the context variables
this library passes, each with its provenance — `translation` (short names off
`NOTIFICATION_KEYS`), `branding` (always set), `conditional` (set under a
guard, and the guard's source text travels with it) and `caller` (what the
sender must supply). Emitted by `stapel_tools.template_contract` from
`routing.py`, `translation_keys.py` and the Python AST of the render call site
in `services.py`; nothing is retyped, and `make contract-check` gates the
drift. It states its own edges in `limits` rather than claiming completeness:
a caller variable that no translation string interpolates and no shipped
template renders is invisible to static derivation.

Unlike the triad it needs neither Django settings nor drf-spectacular, so a
host can regenerate and diff it on any interpreter.

### Added — the library's own suite renders every letter with a marker for what is missing

The test harness (`_codegen_settings.py`) now switches on stapel-core 0.21's
missing-variable marker, and `tests/test_template_render.py` renders every one
of the 24 routes against **exactly** the context `docs/templates.json` declares
and asserts nothing came up missing. Rename a variable on either side without
the other and the suite says which one, by name. The emitter already refused to
publish a contract that under-declares; this is the runtime confirmation that
the declaration is not merely self-consistent but sufficient to render the
letter. Production rendering is untouched — the host's own `TEMPLATES` decide
that.

### Added — `STAPEL_NOTIFICATIONS["TEXT"]`: the copy seam that matched the template seam

A host could always replace a letter's LAYOUT and never its WORDS. The subject
above all: it lives in no template, and `process_notification` refuses caller
`variables` that collide with a translation key, so `subject` could not be
passed either. The gettext route could not fix English at all — `_gettext_default`
treats "translation == msgid" as "no translation".

`TEXT` is a per-key registry merged over `NOTIFICATION_KEYS`, the string
counterpart of `EMAIL_TEMPLATES`. A bare string replaces the English default
**and becomes the gettext msgid**, so an override stays translatable through
the host's own catalogue — an override can never freeze a letter into one
language, which is the bug the key registry exists to prevent. A
`{lang: str}` dict pins specific languages and wins over the cache and the
translate service, which only ever saw the old copy. `TEXT` keys also give a
host-registered type its first copy source: such a type has no entry in
`NOTIFICATION_KEYS` at all.

### Fixed — a personal invitation carried a one-click unsubscribe

`workspace.invitation`, `.new_user` and `.reminder` are `group: "system"`, so a
known `user_id` minted an `unsubscribe_url` and with it
`List-Unsubscribe` + `List-Unsubscribe-Post: One-Click`. RFC 8058 one-click is
machine-actionable — a mail client or an anti-abuse scanner may POST that URL
with no human involved — and the token is minted per (user, GROUP, channel).
One automated click on an invitation from a named colleague therefore opted the
recipient out of every `system` email the platform will ever send, silently.

The three invitation types now carry `"transactional": True` (`routing.is_transactional`),
an orthogonal flag rather than a fourth group: no `List-Unsubscribe` headers and
no unsubscribe footer, whatever the group. The "you agreed to receive messages
from us" consent line goes with them; it was never true of an invitation.

**Behaviour change**: those three letters no longer offer an unsubscribe.
Deliberately narrow — the flag governs the AFFORDANCE only and does not exempt
a type from the recipient's own group preference.

### Fixed — a prefix collision handed invitations six unusable variables

`notification.workspace.invitation.` is a prefix of
`notification.workspace.invitation.new_user.`, so a plain prefix match gave the
plain invitation context variables literally named `new_user.subject` — names
with a dot, which Django resolves as attribute lookups and can therefore never
render. No output changed; every invitation simply paid for six extra
translation lookups and reported them as untranslated. The naming rule now
lives in `translation_keys.keys_for_type`, shared by the runtime and the
`templates.json` emitter, so the contract is derived from the function that
actually runs.

## [0.7.1] — 2026-08-09

### Added — Spanish ships as a language of the library, not as a host override

`translations/errors.es.json` (43 keys) + `docs/errors.es.md`, generated by the
same contour that produces Russian — no hand-written JSON, no product-side
override file. 41 values are lifted verbatim from the curated
`stapel-translate` builtin corpus (`origin: seed:stapel-builtin`), which is what
"clients don't spend tokens" means in practice: the corpus was paid for once.
The remaining 2 are machine translations of this module's own keys, recorded
`origin: llm` — **unreviewed**, and counted as such by the gate now that
stapel-core 0.20.1 stopped treating a curated corpus as human sign-off. Nobody
has read these; `translate_catalogs --approve` is the state transition that
changes that, and it has not been run.

Register and terminology follow the corpus rather than being invented per
module: informal *tú* address, *espacio de trabajo*, *llave de acceso*, *nombre
para mostrar*.

The harness in `tests/test_error_i18n.py` is now language-generic — a language
is a tag in `LANGUAGES` plus whatever the corpus does not carry, and the
catalog, the provenance sidecar, the reference page and the gate all follow.
Adding the next language is not a second copy of this work.

### Fixed — the translation catalogs were built into the wheel and then left out of it

`translations/errors.ru.json` has been in this repository since the i18n wave,
and it has never reached anyone who installed the package. `[tool.setuptools.package-data]`
listed `schemas/`, `migrations/`, `docs/` — and not `translations/`, so setuptools
dropped the directory on the way into the wheel. Verified by installing the built
wheel into an empty virtualenv: no `translations/` under the installed package,
and `load_app_catalogs()` therefore found nothing to merge. Every host running
this module in Russian was silently reading the English canon.

`translations/*.json` is now declared, and the check is the install, not the
manifest: the wheel is built, installed into a clean virtualenv, and the
catalogs are listed on disk.

## [0.6.3] — 2026-08-05

### Fixed — a total delivery failure could stay silent forever

`process_notification` already recorded a per-channel `NotificationLog` row
as `skipped` (0.6.1's own fix) when a channel had no address to deliver
to, and logged it at WARNING. Nobody ever read that row or that log line:
`request_notification` is a fire-and-forget bus publish, so the calling
service has no synchronous signal at all — a workspace invitation got its
201, the row was created, and the letter simply never left the building
(found live: meettoday sandbox, 2026-08).

`process_notification` now tracks, across the whole channel loop, whether
ANY routed channel actually delivered and whether ANY of them failed for a
reachability reason (no address, or the provider raised) — as opposed to
the recipient's own preference switching a channel off, which is the
system working as designed and stays quiet. When every routed channel
failed to reach the recipient, it logs a single `NOTIFICATION
UNDELIVERABLE` line at ERROR with the notification type, the channels
tried, and the identifying fields — a distinct, greppable signal that
log-based alerting (Sentry issue capture, a CloudWatch/Loki alarm on the
string) can catch. A notification that reaches at least one channel, or
that the recipient opted out of entirely, is unaffected.

## [0.6.2] — 2026-08-02

### Changed — packaging/CI only, no runtime change

- Badge canon + Python 3.14 trove classifier.
- `docs/llms.txt` — the fifth contract artifact (badge-canon §3): the
  module's own context slice for an agent, rendered from
  `capabilities.json` + the contract triad, regenerated by `make contract`
  and checked by the drift gate alongside the existing triad.
- `docs/llms.txt` now ships inside the wheel (`package-data`), matching the
  other four contract artifacts.

## [0.6.1] — 2026-08-01

### Added — the two workspace letters whose emitters already existed (#193)

Both call sites were live in `stapel-workspaces` and best-effort, so nothing
failed — the letters simply never went out (`process_notification` drops an
unregistered type with a logged error the caller never sees). The types now
exist where every other library-emitted type lives: built-in routing entry,
packaged default template, English defaults in `translation_keys.py`, ru
catalogue entries — with the usual host-override seams (`TYPES` /
`EMAIL_TEMPLATES` / `eject_notification_templates`) untouched.

- **`workspace.member_password_reset`** (`workspace_name`, `actor_name`,
  `login_url`) — an org admin reset the member's password (workspaces #110).
  Auth-class: mandatory, no unsubscribe — a reset performed by somebody
  other than the account's owner is indistinguishable from a takeover until
  the owner is told. The letter names the workspace and the admin, states
  that live sessions were signed out, and explicitly does **not** carry the
  new password (the admin hands it over out of band).
- **`workspace.invitation.reminder`** (`workspace_name`, `inviter_name`,
  `accept_url`, optional `role_name`) — re-delivery of a pending invitation
  (workspaces #109 resend). Previously the resend path reused
  `workspace.invitation`, presenting a reminder as a first invitation; its
  own type by the same rule as `.new_user` — a different message a host may
  route or template independently. The body honestly says the earlier link
  no longer works, because the workspaces side rotates the token on resend.

`stapel-workspaces` >= 0.14.1 emits the reminder type from its resend path.

## [0.6.0] — 2026-07-30

### Fixed (BREAKING for anonymous callers) — a guest session could take a device's push routing away from a real account (#168)

`stapel-core` 0.16 turns the `AUTH_ANONYMOUS` axis into a question this
module never answered — and answering it surfaced a live defect rather than
just a missing declaration.

`DeviceTokenView` deliberately **removes another account's binding** when the
same token arrives under a different user; that is the correct behaviour for
a handed-over device, and it is logged as a rebinding. But a guest session is
`is_authenticated`, so the bare `IsAuthenticated` gate admitted it, and the
following sequence needed no attacker at all:

1. a real user's device is registered for push;
2. the user logs out and the app mints an anonymous session (`AUTH_ANONYMOUS`);
3. the app re-registers the same device token;
4. the rebinding branch deletes the real account's row and binds the device to
   a throwaway identity nobody will ever log into again.

The real account silently stops receiving push, and nothing in the source said
this was possible.

`POST /devices/` and `DELETE /devices/{token}/` now carry `IsNotAnonymousUser`
— **403** where an anonymous session previously got 201/204. Nothing is lost:
an anonymous account has no durable identity worth notifying, so a guest
could only ever have registered a device it should not hold.

### Changed — the feed stays open, deliberately

`GET /feed/` declares `stapel_anonymous_access = ANONYMOUS_ALLOWED`. It reads
`NotificationLog` filtered by `request.user.id`, so a guest's answer is an
empty page — the truth, and cheaper for a bell icon that renders for every
session than a 403 it would have to special-case.

Minor, not patch: for a deployment with `AUTH_ANONYMOUS` on this is a
behaviour change on a live surface, visible in the published contract
(`docs/schema.json` now documents `IsNotAnonymousUser` on the two device
operations). Deployments without guest sessions are unaffected — an ordinary
authenticated user passes `IsNotAnonymousUser` exactly as before.

New `tests/test_guest_surface.py` writes the shared-device scenario out as a
regression test.

### Changed

- Minimum `stapel-core` raised to `>=0.16` (the release that added
  `ANONYMOUS_ALLOWED` / `ANONYMOUS_DENIED`).

## [0.5.4] — 2026-07-30

### Fixed
- **A channel with no address is logged `skipped`, not `sent`.** `_dispatch`
  returned silently (at DEBUG level) when a recipient had no email address /
  no phone number, and `process_notification` then wrote a `NotificationLog`
  row reading `sent → unknown`. The commonest shape in the whole library hit
  it: an OTP requested for an email only — an unauthenticated or anonymous
  guest signing in, no account and no phone — routes to email+sms and always
  produced a phantom "sent" SMS. Two consequences beyond the wrong audit
  trail: nobody grepping for failures could see it, and the idempotency guard
  keys on `status="sent"`, so a retry that could have delivered was suppressed
  by a delivery that never happened. `_dispatch` now returns whether it
  reached a provider; the caller logs `skipped` with a reason
  (`"no <channel> address for this recipient"`) at WARNING level. A provider
  that IS reached and then fails still raises and is logged `failed`, as
  before.

## [0.5.3] — 2026-07-29

### Added
- **A Russian catalogue ships with the package** — `locale/ru/LC_MESSAGES`,
  109 msgids covering all 123 notification strings. The library owns these
  strings, so it owns their translations: a host should not have to re-type
  this package's own English defaults into its `.po` just to get Russian mail.
  Tests assert the rendered email is Russian and that `{placeholders}` survived
  the translation — a translated subject that lost `{code}` would be worse than
  an English one.

### Fixed
- The "fell back to English" warning no longer fires on strings that have
  nothing to translate. `"© {company_year} {company_name}"` and
  `"{company_address}"` are the same in every language; gettext echoes such a
  msgid back unchanged, which is indistinguishable from "not in the catalogue",
  so every single email reported two false misses. A warning that fires when
  nothing is wrong stops being read.
- `locale/**` added to the wheel's package data — without it the catalogue
  exists in the repository and is absent from the installed package, which is
  the same silent-failure shape this release is trying to remove.


## [0.5.2] — 2026-07-29

### Fixed
- **A correctly-internationalised host still got English email.** Resolving the
  language was only half the job: this package ships English defaults and its
  only route to another language was a `translate` service. A project that had
  done the standard Django thing — `locale/ru/LC_MESSAGES/django.po` — got
  nothing from it. `_resolve_translations` now consults the host's **gettext
  catalogue** (the English default doubles as the msgid, which is how gettext
  is meant to be used) before falling back to the built-in string. Hosts with
  `.po` files get translated notifications with no extra infrastructure.
- **Falling back to English is now reported.** It was `logger.debug`, i.e.
  invisible: the mail sends, so it looks like success — it is just in the wrong
  language. A request for a non-English language that ends up on the built-in
  defaults now logs a **warning** naming the language, the count and the first
  few keys. Found live by meettoday (2026-07-29): OTP arrived in English after
  the language-resolution fix, and nothing in the logs said why.

### Changed
- **The footer link shows the host, not the brand name a third time.** One
  brand can run many instances (`3571.meettoday.app`, `meettoday.app`, a
  customer's own deployment); a footer reading the same word again tells the
  reader nothing about which one wrote to them. `company_host` is derived from
  `COMPANY_URL` and used as the link text.


## [0.5.1] — 2026-07-29

### Fixed
- **Anonymous notifications are no longer silently anglicised.** The language
  chain ended in a hardcoded `"en"`: an anonymous request has no `user_id`, so
  there is no saved preference and nothing auto-detected, and every OTP email,
  workspace invitation and GDPR notice went out in English regardless of the
  caller's locale — while Django had already resolved the request's language.
  The chain now falls through to the process's active language and then to the
  **project's** fallback (`stapel_core.language.default_language()`:
  `STAPEL_LANGUAGE["DEFAULT"]` → `settings.LANGUAGE_CODE`), because "en" as the
  final answer is a product assumption a library has no business making — a
  service for a Russian-speaking market wants `ru` there.

  Callers that pass `language` explicitly are unaffected. `stapel-auth` 0.14.4
  started passing it for OTP; this fixes every caller that does not, without
  each of them having to be found first.


## [0.5.0] — 2026-07-28

### Removed
- **The bundled default logo.** This package shipped a 233 KB 512×512 PNG and
  attached it inline to every email whose host had not set `LOGO_URL`. It was
  one product's brand mark living inside a general-purpose OSS library — every
  host that never configured branding sent mail carrying somebody else's logo.
  It was also the single largest thing in a one-line OTP email, and slow enough
  over SMTP to look like a hang.

  **Breaking:** with `LOGO_URL` unset there is now no image at all; the header
  renders `COMPANY_NAME` as a text wordmark. Hosts that want a picture set
  `LOGO_URL` to one they own, served over https. A `data:` URI does not work —
  Gmail and others block `data:` as an image source in mail (measured).

### Fixed
- **SMTP could hang forever.** `_SMTPEmailProvider` used Django's default
  connection, which has no timeout unless the host sets `EMAIL_TIMEOUT` — while
  the Resend and Mailgun providers next to it already passed `timeout=15`. A
  slow mail server held the request until the reverse proxy returned 504. The
  provider now opens its own connection with a timeout, configurable via
  `SMTP_TIMEOUT` (default 15). A host that set `EMAIL_TIMEOUT` keeps it: we
  supply a default where there was none, we do not overrule a decision.
- **Header fallback no longer renders broken.** The logo `<img>` carried a
  fixed `width="96"` intended to frame a square icon; when the image could not
  load, that width also clipped the `alt` text, so a nine-character brand name
  came out truncated inside a broken-image box. The fallback is now text, not
  an image with hopeful styling.
- **Footer company link is a link only when there is a URL.** `COMPANY_URL`
  has no default, so the footer rendered `<a href="">` — underlined, coloured,
  and inert. With no URL it is now plain text.

All three email defects were found on a live mail server by meettoday
(2026-07-28); none of them reproduced with an invalid password, because the
server rejected the login before any of this ran.


## [0.4.0] — org-program email notifications (workspaces-org-program.md §F)

### Added
- `workspace.invitation.new_user` notification type + email template
  (`workspace_invitation_new_user.html`) — the invite variant for a
  not-yet-registered recipient, where the acceptance link both creates the
  account and joins the workspace. Kept as a separate type rather than an
  override of `workspace.invitation`, a clean routing-override seam (group
  `system`, channel `email`; vars: `workspace_name`, `inviter_name`,
  `accept_url`, optional `role_name`).
- `workspace.provisioned_account` notification type + email template — first
  credentials for an org-provisioned (org-created) user. Auth-class routing
  (group `auth`, mandatory, no unsubscribe — same treatment as
  `new_device_login`/`suspicious_login`). Vars: `workspace_name`, `username`,
  `login_url`, optional `initial_password`.
- `workspace.mfa_suspension` / `workspace.mfa_restored` notification types +
  email templates — org `require_mfa` policy suspending/restoring a member's
  workspace access. Auth-class routing (group `auth`, no unsubscribe). Vars:
  `workspace_name`, optional `security_url` / `workspace_url`.
- `workspace.invitation`: additive `{role_name}` param — an optional role
  line ("You're invited to join as {role_name}.") rendered only when the
  caller passes `role_name`; omitting it keeps subject/heading/body/cta
  byte-identical to before this change (new `role_line` translation key,
  gated in the template by `{% if role_name %}`, not by reformatting the
  existing keys).

### Decision — password in the provisioned-account email
`workspace.provisioned_account` embeds the org-issued `initial_password`
directly in the template (credentials box, `{% if initial_password %}`)
rather than only linking to a "set your password" flow. Precedent: this
module already embeds a comparable one-time secret directly in an email —
`otp_code.html` renders `{{ code }}` (the OTP) straight into the body. Since
that canon exists, `provisioned_account` follows it instead of inventing a
new exception. The variable stays optional (`{% if initial_password %}`) so
a host that instead issues a "set password" link can omit it and just pass
`login_url`.

### Tests
- `tests/test_org_program_notifications.py` (new): additive-`role_name`
  rendering (with/without), the `new_user` variant's distinct copy +
  routing (system group still carries `List-Unsubscribe` once a user_id is
  known), the password-in-email precedent, and the auth-class (no
  unsubscribe) behavior for `provisioned_account` / `mfa_suspension` /
  `mfa_restored`, plus translation-key registry coverage for every new type.
- `tests/test_extensibility.py` / `tests/test_features.py`: routing
  registration + render-and-send coverage extended to the four new types,
  alongside the existing `workspace.invitation` cases.
- No `docs/{schema,flows,errors,capabilities}.json` drift — this wave adds
  notification types/templates/keys only, no serializer/view/url surface
  changed (`make contract-check` verified clean).

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
