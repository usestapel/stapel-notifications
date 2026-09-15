"""Settings namespace for stapel-notifications.

Everything a host project previously had to fork is an override here::

    STAPEL_NOTIFICATIONS = {
        # add/override notification types without touching routing.py
        "TYPES": {
            "invoice_ready": {
                "channels": ["email", "push"],
                "group": "system",
                "template": "myapp/email/invoice_ready.html",
            },
        },
        # map/override email templates per type (merged over the built-ins)
        "EMAIL_TEMPLATES": {
            "new_message": "myapp/email/new_message.html",
        },
        # channel backends: built-in short name or any dotted path
        "EMAIL_PROVIDER": "myproject.email.SendgridProvider",
        "SMS_PROVIDER": "twilio",
        "PUSH_PROVIDER": "fcm",
        "TELEGRAM_PROVIDER": "myproject.telegram.BotProvider",
    }

Resolution per key: STAPEL_NOTIFICATIONS dict → env → default. The provider
keys are the one special case: the environment may name a SHORT NAME this
package ships (``"resend"``, ``"mock"``, …) but never a dotted path — see
``PROVIDER_SETTINGS`` / ``PROVIDER_ENV_ENUM`` below.
"""
from stapel_core.conf import AppSettings

#: The keys whose value names the CLASS this process imports and runs to put
#: a passcode, a password reset or an account-closure notice on the wire.
#:
#: They are declared to ``AppSettings`` as ``import_strings``, which since
#: stapel-core 0.24.0 makes them implicitly ``no_env``: a same-named
#: environment variable no longer selects the delivery backend. That is the
#: whole point — anything that can set an env var in the pod (a leaked value,
#: a sibling container's config, a stray export in an entrypoint) could
#: otherwise choose which class receives every OTP this service sends. The
#: project's own settings module is trusted and still wins; the environment
#: is not. ``stapel_core.conf.W001`` names such a variable at
#: ``manage.py check`` time, because ignoring one is silent by nature.
#:
#: Since 0.21.0 that closure is NARROWED rather than absolute: the
#: environment may pick among the short names this package ships, because
#: doing so cannot introduce code. It still may not name a dotted path. See
#: ``PROVIDER_ENV_ENUM`` below for why the line is drawn exactly there.
PROVIDER_SETTINGS = (
    "EMAIL_PROVIDER",
    "SMS_PROVIDER",
    "PUSH_PROVIDER",
    "TELEGRAM_PROVIDER",
)


def _provider_names(module: str):
    """The short names one channel module ships, asked lazily.

    A zero-arg callable rather than a literal because the registry lives in
    the channel module, and importing four channel modules from this file
    would drag them into every import of the package — ``conf`` is imported
    by app configs, checks and services alike.
    """
    def names():
        from importlib import import_module

        return tuple(import_module(module)._PROVIDERS)

    return names


#: The environment MAY choose among the names this library ships, and only
#: those (stapel-core 0.70.0 ``env_enum``).
#:
#: Until this existed the four keys above were flatly env-closed, and that was
#: half-right. The threat is real — anything able to set a variable in the pod
#: would otherwise name an arbitrary dotted path and choose the class that
#: receives every passcode. But the NEED is equally real, and closing the door
#: on it did not make deployments stop: a fleet was found on 2026-09-16 with
#: ``os.getenv('EMAIL_PROVIDER', 'mock')`` hand-written in its own settings
#: module, so the variable this library documents worked there and nowhere
#: else, while ``stapel_core.conf.W001`` truthfully reported it ignored. A
#: documented surface that lies is worse than either honest answer.
#:
#: The enumeration draws the line where the threat actually is. Picking among
#: the providers THIS PACKAGE ships is a deployment choice and belongs in the
#: environment. A dotted path is new code on the privileged path — a trust
#: decision — and stays in the settings module, which only the project can
#: write. ``conf.E003`` reports a rejected value at ``manage.py check``.
PROVIDER_ENV_ENUM = {
    "EMAIL_PROVIDER": _provider_names("stapel_notifications.channels.email"),
    "SMS_PROVIDER": _provider_names("stapel_notifications.channels.sms"),
    "PUSH_PROVIDER": _provider_names("stapel_notifications.channels.push"),
    "TELEGRAM_PROVIDER": _provider_names("stapel_notifications.channels.telegram"),
}


class NotificationsAppSettings(AppSettings):
    """``AppSettings`` whose provider keys are imported by the channel layer.

    ``PROVIDER_SETTINGS`` are ``import_strings`` for the POLICY half of that
    declaration; ``PROVIDER_ENV_ENUM`` then reopens the environment step for
    short names only, so ``W001`` correctly stops reporting these variables
    as ignored and ``E003`` reports a value the registry does not know. The
    IMPORT half stays where it already lives —
    ``channels.sms._resolve_provider_class`` — because a provider value is
    not only a dotted path: it is *either* a built-in short name
    (``"twilio"``, ``"mock"``, the shipped ``"unconfigured"``/``"fcm"``
    defaults) *or* a dotted path. The base class's eager ``import_string``
    would raise on every short-name deployment, and it would also replace a
    typo's ``ImproperlyConfigured`` — the message ``checks.E003`` turns into
    a failed boot — with a bare ``ImportError`` from attribute access.

    So this class hands the raw string through and lets the registry-aware
    resolver do the import. It is a superset of ``import_string``, not a way
    around it: an unknown name and an unimportable path both raise, and a
    dotted path still cannot be chosen by an env var.
    """

    def __getattr__(self, key):
        if key not in PROVIDER_SETTINGS:
            return super().__getattr__(key)
        if key in self._cache:
            return self._cache[key]
        value = self._raw(key)  # _raw applies the env gate; the import does not
        self._cache[key] = value
        return value

#: AppSettings-shaped literal dict (capability-config.md §2): a top-level
#: DEFAULTS lets the capabilities.json emitter introspect axis keys/kinds
#: without re-parsing the AppSettings() call.
DEFAULTS = {
    # Notification-type registry, merged OVER routing.NOTIFICATION_ROUTING.
    # {"<type>": {"channels": [...], "group": "auth|messages|system",
    #             "template": "myapp/email/x.html"}}
    "TYPES": {},
    # Delivery-channel registry, merged OVER the four built-ins
    # (email/push/sms/telegram), last-wins per name — the channel-set
    # counterpart of TYPES. {"<channel>": Channel | "dotted.path" |
    # deliver_callable | None}, where None switches a built-in off.
    #
    # The channel set used to be an if/elif chain in services._dispatch, so
    # an in-app feed, a webhook or a chat gateway was an upstream patch and
    # nothing less. A registered channel gets the same wrapping as email —
    # preference gate, delivery claim, journal row, telemetry allowlist —
    # and becomes switch-off-able through
    # UserNotificationSettings.channel_preferences. See channels/registry.py.
    "CHANNELS": {},
    # Per-type email template overrides, merged over
    # routing.DEFAULT_EMAIL_TEMPLATES.
    "EMAIL_TEMPLATES": {},
    # Host copy registry, merged OVER translation_keys.NOTIFICATION_KEYS.
    # The string counterpart of EMAIL_TEMPLATES: a host could always replace
    # a letter's LAYOUT (template-directory precedence, EMAIL_TEMPLATES) but
    # had no honest way to replace its WORDS — subject above all, which lives
    # in no template. Overriding a whole template to change one subject line
    # is not an option either: the subject is not in the template.
    #
    #   "TEXT": {
    #       # a bare string replaces the English default AND becomes the
    #       # gettext msgid, so a host's own locale/*/django.po keeps
    #       # translating it — an override is never English-only-forever
    #       "notification.otp_code.subject": "{company_name} code: {code}",
    #       # a dict pins specific languages; the rest fall through the
    #       # normal cache -> translate -> gettext -> default chain
    #       "notification.footer.legal": {"en": "...", "ru": "..."},
    #   }
    #
    # Keys for a host's OWN type (registered via TYPES) work too — that type
    # has no entry in NOTIFICATION_KEYS, so TEXT is its only copy source.
    "TEXT": {},
    # Raw-content escape hatch: may a caller supply the body of a branded
    # letter, and may it supply MARKUP? "off" (default) | "text" | "html".
    # Anything a producer can reach can otherwise send byte-perfect
    # first-party phishing — see raw_content.py for the whole reasoning.
    "RAW_CONTENT": "off",
    # Per-type telemetry allowlist for the delivery journal:
    # {"<type>": ["order_id", ...], "*": ["tenant_id"]}. Deny-by-default —
    # a caller variable that nobody declared here (or in the routing entry's
    # "telemetry" key) is not written to NotificationLog.data, and a declared
    # one is still dropped when its VALUE is credential-shaped. See
    # telemetry.py: the journal used to persist passcodes, sign-in links and
    # provisioned passwords verbatim into a table the admin renders.
    "TELEMETRY": {},
    # Seconds after which a delivery claim whose process died is taken over
    # by a redelivery (models.NotificationDelivery). Long enough to cover a
    # slow SMTP round trip, short enough that a crashed consumer does not
    # silence a notification for the rest of the day.
    "DELIVERY_CLAIM_TTL": 900,
    # Channel backends: short registry name or dotted path to a provider
    # class with .send(...).
    #
    # The email/SMS defaults are "unconfigured", NOT "mock". A mock provider
    # returns, and services._dispatch counts a provider that returned as a
    # delivery — so a zero-config deployment used to write status="sent" into
    # the delivery journal for every OTP, password reset and account-closure
    # notice it had only logged. "unconfigured" raises instead: the same
    # deployment now records status="failed" and escalates through the
    # "NOTIFICATION UNDELIVERABLE" path.
    #
    # Log-only delivery is still available — it is now an explicit act:
    # {"EMAIL_PROVIDER": "mock", "SMS_PROVIDER": "mock"}. checks.W005 warns
    # when that lands in a non-DEBUG deployment.
    #
    # PUSH_PROVIDER keeps "fcm": it already refuses loudly without
    # credentials, so it was never the silent-success shape.
    #
    # TELEGRAM_PROVIDER has no built-in delivering backend at all — the bot
    # token is the deployment's own sending identity, so the client is
    # app-layer by dotted path (channels/telegram.py). "unconfigured" is
    # therefore both the default and the only closed state.
    "EMAIL_PROVIDER": "unconfigured",
    "SMS_PROVIDER": "unconfigured",
    "PUSH_PROVIDER": "fcm",
    "TELEGRAM_PROVIDER": "unconfigured",
    # Provider credentials (read lazily, never frozen at import)
    "RESEND_API_KEY": "",
    "MAILGUN_API_KEY": "",
    "MAILGUN_DOMAIN": "",
    "GATEWAYAPI_TOKEN": "",
    # NO DEFAULT, on purpose (0.22.0). This is the alphanumeric sender id an
    # SMS arrives from, and on most handsets it becomes the permanent thread
    # title for every passcode this deployment will ever send. It used to
    # default to this framework's own name, so a deployment that configured
    # GatewayAPI correctly — the provider raises for a missing TOKEN, so that
    # much is forced — still sent every OTP under a vendor name its customers
    # have never heard of, journalled "sent", with nothing anywhere saying so.
    # checks.E007 refuses the boot instead.
    "GATEWAYAPI_SENDER": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_PHONE_NUMBER": "",
    "GOOGLE_APPLICATION_CREDENTIALS": "",
    # Template variables
    #
    # NO DEFAULT, on purpose (0.22.0). COMPANY_NAME is not decoration: it is
    # the subject line ("Your {company_name} verification code"), the header
    # wordmark (rendered as text whenever LOGO_URL is empty, which is also the
    # default), the copyright line, the consent sentence and the SMS body. A
    # deployment that never set it sent passcodes branded with this
    # framework's name — which is the classic phishing signature, and trains
    # recipients to distrust the real mail. Identity is not something to
    # acquire by not reading the docs, so checks.E007 refuses the boot.
    "COMPANY_NAME": "",
    "COMPANY_URL": "",
    # Stays optional, but an empty value now OMITS the footer paragraph
    # instead of rendering an empty block (0.22.0). Some jurisdictions require
    # a postal address in commercial mail; none is served by a blank line
    # where one should be.
    "COMPANY_ADDRESS": "",
    "COMPANY_YEAR": "",
    "FRONTEND_URL": "",
    # Branding: logo + colors, threaded into every email template via
    # the base layout (templates/notifications/email/_base.html).
    # LOGO_URL set   → templates embed <img src="LOGO_URL">. Point it at an
    #                  image you own, served over https. A data: URI does
    #                  NOT work: Gmail and others block data: as an image
    #                  source in mail (measured on a client stand, 2026-07-28).
    # LOGO_URL empty → no image at all; the header renders COMPANY_NAME as
    #                  a text wordmark. This package ships no logo of its
    #                  own — see channels/email.py for why.
    "LOGO_URL": "",
    # SMTP connection/read timeout in seconds, used when the host has not
    # set Django's EMAIL_TIMEOUT. Without one, a slow mail server hangs the
    # request until the reverse proxy gives up.
    "SMTP_TIMEOUT": 15,
    "BRAND_PRIMARY": "#00AEEF",        # logo/accent color
    "BRAND_PRIMARY_DARK": "#2A90D9",   # buttons + links
    "BRAND_BG": "#F5F5F6",             # page background
    "BRAND_TEXT": "#1C1D20",           # headings + body copy
    # Languages to prefetch with `manage.py sync_translations` (the
    # lazy resolve-on-miss path covers anything not listed here).
    "LANGUAGES": ["en"],
}

notifications_settings = NotificationsAppSettings(
    "STAPEL_NOTIFICATIONS",
    defaults=DEFAULTS,
    # Implementation seam. A dotted path is never selected by the
    # environment — that is new code on the privileged path. A short name
    # this package ships may be: see PROVIDER_ENV_ENUM above.
    import_strings=PROVIDER_SETTINGS,
    env_enum=PROVIDER_ENV_ENUM,
)

__all__ = [
    "notifications_settings",
    "NotificationsAppSettings",
    "PROVIDER_SETTINGS",
    "PROVIDER_ENV_ENUM",
]
