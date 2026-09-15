"""
Notification type → channel routing.

The built-in catalog below covers the framework's own notifications; a host
project extends or overrides it WITHOUT forking via the settings namespace::

    STAPEL_NOTIFICATIONS = {
        "TYPES": {
            "invoice_ready": {
                "channels": ["email", "push"],
                "group": "system",
                "template": "myapp/email/invoice_ready.html",
            },
            # override a built-in:
            "new_message": {"channels": ["push"], "group": "messages"},
        },
        # or map/override templates without touching the routing entry:
        "EMAIL_TEMPLATES": {
            "invoice_ready": "myapp/email/invoice_ready.html",
        },
    }

Groups:
    auth     — mandatory security notifications (no unsubscribe)
    billing  — mandatory record of money that moved (no unsubscribe)
    messages — user-to-user messages (can disable per channel)
    system   — platform notifications (can disable per channel)

The group set is CLOSED (``VALID_GROUPS``): an UNSUBSCRIBABLE group is also
the name of the per-channel preference field (``email_system``,
``push_messages``, …), so an unsubscribable group nobody has a field for is
mail the recipient cannot switch off. A type registered under an unknown
group is ``notifications.E001`` at boot.

``billing`` is the second MANDATORY group, and it is a group rather than a
flag because the thing it changes is the recipient's preference, which is
exactly what the two flags below deliberately do not touch. A receipt for
money the platform took is owed to the payer as a record — a person who
switched off ``email_system`` asked not to be told about platform news, not
to stop being told when they are charged. It is not ``auth`` either: calling
a receipt a security notification to borrow that group's mandatory-ness
would put payment mail under a security classification that decides other
things (``is_security``, the E002 demotion check) which a receipt has no
business inheriting. Mandatory groups mint NO preference field — see
``valid_pref_fields`` — so adding this one needs no column and no migration.

Two ORTHOGONAL flags, neither of them a fourth group. Both govern the
unsubscribe AFFORDANCE only and leave the group — and therefore the
recipient's preference — exactly as it is:

``"transactional": True``
    "this message is one-to-one, sent because a named human acted, not
    because a list was mailed". See ``is_transactional``.

``"security": True``
    "this is an account-security / authentication message that happens to
    live outside the ``auth`` group". A host that wants a security letter to
    stay switch-off-able keeps it in ``system`` and marks it here.

**The policy, in one sentence:** an unsubscribe affordance — the
``List-Unsubscribe`` / ``List-Unsubscribe-Post: One-Click`` headers and the
unsubscribe footer — is produced ONLY for a type whose group is explicitly in
``UNSUBSCRIBABLE_GROUPS`` and which is neither transactional nor
security-class. It is an allowlist, not a denylist: a type with a missing,
misspelled or unknown group gets NO affordance, because the old rule was
``group != "auth"`` and every way of failing to say "auth" — a typo, an
omitted key, a settings override that replaces a built-in auth entry
wholesale and drops its group — silently turned a passcode into bulk mail
with a one-click opt-out from every security email the platform sends.

``unsubscribe_allowed`` is the single decision; ``checks.py`` refuses at boot
the two registrations that are always defects (unknown group; a host override
that demotes a built-in security type out of its class).
"""
from .conf import notifications_settings

#: Groups whose mail is security/authentication class: mandatory, and never
#: carrying an unsubscribe affordance.
SECURITY_GROUPS = frozenset({"auth"})

#: Groups whose mail is the record of money that moved. Mandatory for the
#: same reason security mail is — the recipient is owed it — but NOT
#: security-class: ``is_security`` stays false, and the E002 demotion check
#: does not claim these types.
BILLING_GROUPS = frozenset({"billing"})

#: Every group a recipient may not switch off. ``_should_send`` asks THIS,
#: not the ``"auth"`` literal it used to compare against: a second mandatory
#: group added beside a hardcoded string is a group that silently becomes
#: switch-off-able the moment somebody adds it.
MANDATORY_GROUPS = SECURITY_GROUPS | BILLING_GROUPS

#: The ONLY groups whose mail may carry an unsubscribe affordance. Adding a
#: name here is the single edit that grants a whole class of mail a one-click
#: opt-out — which is why it is a literal, reviewable set and not "anything
#: that is not auth".
UNSUBSCRIBABLE_GROUPS = frozenset({"messages", "system"})

#: The closed group vocabulary. Every UNSUBSCRIBABLE one of these has matching
#: preference fields in ``services.valid_pref_fields()``; the mandatory ones
#: deliberately have none, because there is nothing to ask the recipient.
VALID_GROUPS = MANDATORY_GROUPS | UNSUBSCRIBABLE_GROUPS

NOTIFICATION_ROUTING = {
    # Group A: Auth/Security (mandatory, no unsubscribe)
    "otp_code":              {"channels": ["email", "sms"],          "group": "auth"},
    "auth_change_requested": {"channels": ["email", "sms", "push"], "group": "auth"},
    "auth_change_reminder":  {"channels": ["email", "sms", "push"], "group": "auth"},
    "auth_change_urgent":    {"channels": ["email", "sms", "push"], "group": "auth"},
    "auth_change_completed": {"channels": ["email", "sms", "push"], "group": "auth"},
    "magic_link_login":      {"channels": ["email"],                 "group": "auth"},
    "new_device_login":      {"channels": ["email"],                 "group": "auth"},
    "suspicious_login":      {"channels": ["email"],                 "group": "auth"},
    "all_sessions_revoked":  {"channels": ["email"],                 "group": "auth"},

    # Account lifecycle / GDPR (mandatory, no unsubscribe)
    "gdpr.export_ready":       {"channels": ["email"], "group": "auth"},
    "gdpr.inactivity_warning": {"channels": ["email"], "group": "auth"},
    "gdpr.inactivity_closed":  {"channels": ["email"], "group": "auth"},

    # Billing (mandatory, no unsubscribe) — upstream for stapel-billing's
    # payment.completed / payment.failed / subscription.changed subscribers.
    # A charge that produced no letter is the default state of a Stripe
    # account with receipts switched off, and it stayed the default here
    # too until 0.20.0: six live charges, nothing sent (a client fleet,
    # 2026-09-16).
    #
    # Email only, on purpose. These three carry an amount, a period and a
    # link to a document — a receipt is something the payer keeps and can
    # forward to an accountant, which is what mail is and what a push is
    # not. A push saying "you were charged" that cannot show the invoice is
    # a worse version of the same letter, not a second channel for it.
    "billing.payment_succeeded":   {"channels": ["email"], "group": "billing",
                                    "transactional": True,
                                    "telemetry": ["invoice_url"]},
    "billing.payment_failed":      {"channels": ["email"], "group": "billing",
                                    "transactional": True,
                                    "telemetry": ["retry_url"]},
    "billing.subscription_ending": {"channels": ["email"], "group": "billing",
                                    "transactional": True,
                                    "telemetry": ["resubscribe_url"]},

    # Group B: Messages (user can disable per channel)
    "new_message":           {"channels": ["push", "email"],        "group": "messages"},

    # Group C: System (user can disable per channel)
    "report_reviewed":       {"channels": ["push", "email"],        "group": "system"},
    "listing_expiring":      {"channels": ["push", "email"],        "group": "system"},
    # Statement of reasons + appeal path (DSA Art. 17), upstream for
    # stapel-moderation (moderation-design.md §16.3). "telemetry" declares
    # appeal_url journallable in NotificationLog.data — an undeclared caller
    # variable is dropped by telemetry.telemetry_keys() before it ever
    # reaches the journal.
    "listing_blocked":       {"channels": ["push", "email"],        "group": "system",
                               "telemetry": ["appeal_url"]},
    # Moderation notifications (stapel-moderation upstream,
    # moderation-design.md §6.3/§16.3): the report submitter gets a
    # receipt (DSA Art. 16(4)), the sanctioned user gets the decision with
    # its appeal path (DSA Art. 17), the appellant gets the outcome (DSA
    # Art. 20). Same "system" group and channel pair as their siblings
    # above — a moderation decision is mail the recipient can switch off
    # per channel, same as being told a report was reviewed.
    "moderation.report_received": {"channels": ["push", "email"],   "group": "system"},
    "moderation.sanction_issued":  {"channels": ["push", "email"],  "group": "system",
                                     "telemetry": ["appeal_url"]},
    "moderation.appeal_resolved":  {"channels": ["push", "email"],  "group": "system"},
    "workspace.invitation":  {"channels": ["email"], "group": "system",
                              "transactional": True},
    # Invite variant for a not-yet-registered recipient: the acceptance link
    # both creates the account and joins the workspace. Kept as its own type
    # (not an override of "workspace.invitation") so a host project can route
    # or template it independently — a clean routing-override seam.
    "workspace.invitation.new_user": {"channels": ["email"], "group": "system",
                                      "transactional": True},
    # Re-delivery of a pending invitation (admin "resend"): the token is
    # rotated and the TTL restarted on the workspaces side, so this letter's
    # job is "you are being reminded — here is a fresh link", not "you are
    # being invited". Its own type by the same rule as ".new_user": a
    # different message a host may route or template independently.
    "workspace.invitation.reminder": {"channels": ["email"], "group": "system",
                                      "transactional": True},
    # The two sides of a refusal. Declining used to send nothing at all: the
    # invitee got no receipt for a decision they cannot undo, and the inviter
    # kept waiting for an answer that had already been given.
    #
    # To the address that was invited — the receipt for saying no. It carries
    # no link and asks for nothing: the invitation is closed, and the letter's
    # whole job is to say so and that no account was created in the process.
    "workspace.invitation.decline_confirmed": {"channels": ["email"],
                                               "group": "system",
                                               "transactional": True},
    # To the person who sent the invitation — their invitation was refused.
    # Names the address THEY typed and nothing else about whoever declined.
    "workspace.invitation.declined": {"channels": ["email"], "group": "system",
                                      "transactional": True},

    # Org-provisioned account (org creates a login/password user directly) —
    # auth-class notification: mandatory, no unsubscribe, same as the other
    # account-credential mails above.
    "workspace.provisioned_account": {"channels": ["email"],        "group": "auth"},
    # Org security policy (require_mfa) suspends/restores membership access —
    # account-access notifications, same auth-class treatment.
    "workspace.mfa_suspension":      {"channels": ["email"],        "group": "auth"},
    "workspace.mfa_restored":         {"channels": ["email"],        "group": "auth"},
    # An org admin reset the member's password (#110). A reset performed by
    # somebody other than the account's owner is indistinguishable from a
    # takeover until the owner is told, so this is auth-class: mandatory, no
    # unsubscribe. The letter never carries the new password — it names the
    # workspace and the admin who did it.
    "workspace.member_password_reset": {"channels": ["email"],       "group": "auth"},
}

# Built-in email templates for types that do not carry their own
# "template" key.  Namespaced under templates/notifications/email/ so
# host projects' own "email/*" templates cannot collide.
DEFAULT_EMAIL_TEMPLATES = {
    "otp_code": "notifications/email/otp_code.html",
    "auth_change_requested": "notifications/email/auth_change.html",
    "auth_change_reminder": "notifications/email/auth_change.html",
    "auth_change_urgent": "notifications/email/auth_change.html",
    "auth_change_completed": "notifications/email/auth_change.html",
    "new_message": "notifications/email/new_message.html",
    "billing.payment_succeeded": "notifications/email/billing_payment_succeeded.html",
    "billing.payment_failed": "notifications/email/billing_payment_failed.html",
    "billing.subscription_ending": "notifications/email/billing_subscription_ending.html",
    "report_reviewed": "notifications/email/report_reviewed.html",
    "listing_expiring": "notifications/email/listing_expiring.html",
    "listing_blocked": "notifications/email/listing_blocked.html",
    "moderation.report_received": "notifications/email/moderation_report_received.html",
    "moderation.sanction_issued": "notifications/email/moderation_sanction_issued.html",
    "moderation.appeal_resolved": "notifications/email/moderation_appeal_resolved.html",
    "magic_link_login": "notifications/email/magic_link_login.html",
    "new_device_login": "notifications/email/new_device_login.html",
    "suspicious_login": "notifications/email/suspicious_login.html",
    "all_sessions_revoked": "notifications/email/all_sessions_revoked.html",
    "gdpr.export_ready": "notifications/email/gdpr_export_ready.html",
    "gdpr.inactivity_warning": "notifications/email/gdpr_inactivity_warning.html",
    "gdpr.inactivity_closed": "notifications/email/gdpr_inactivity_closed.html",
    "workspace.invitation": "notifications/email/workspace_invitation.html",
    "workspace.invitation.new_user": "notifications/email/workspace_invitation_new_user.html",
    "workspace.invitation.reminder": "notifications/email/workspace_invitation_reminder.html",
    "workspace.invitation.decline_confirmed": "notifications/email/workspace_invitation_decline_confirmed.html",
    "workspace.invitation.declined": "notifications/email/workspace_invitation_declined.html",
    "workspace.provisioned_account": "notifications/email/workspace_provisioned_account.html",
    "workspace.mfa_suspension": "notifications/email/workspace_mfa_suspension.html",
    "workspace.mfa_restored": "notifications/email/workspace_mfa_restored.html",
    "workspace.member_password_reset": "notifications/email/workspace_member_password_reset.html",
}


def get_routing(notification_type: str) -> dict | None:
    """Effective routing entry: settings TYPES override the built-ins."""
    overrides = notifications_settings.TYPES or {}
    if notification_type in overrides:
        return overrides[notification_type]
    return NOTIFICATION_ROUTING.get(notification_type)


def registered_types() -> list[str]:
    return sorted({**NOTIFICATION_ROUTING, **(notifications_settings.TYPES or {})})


def get_channels(notification_type: str) -> list[str]:
    """Get channels for a notification type."""
    routing = get_routing(notification_type)
    if not routing:
        return []
    return routing.get("channels", [])


def get_group(notification_type: str) -> str:
    """Get group for a notification type."""
    routing = get_routing(notification_type)
    if not routing:
        return ""
    return routing.get("group", "")


def is_transactional(notification_type: str) -> bool:
    """True when this type is a one-to-one message, not bulk mail.

    A transactional message must not offer an unsubscribe, and above all must
    not carry ``List-Unsubscribe`` + ``List-Unsubscribe-Post: One-Click``.
    RFC 8058 one-click is machine-actionable: the mail client, an anti-abuse
    scanner or a security appliance may POST that URL WITHOUT a human, and
    this library's unsubscribe token is minted per (user, GROUP, channel) —
    so one automated click on a personal workspace invitation opted the
    recipient out of every ``system`` email the platform will ever send them,
    silently. A person being invited by a named colleague never asked to be
    on a list, so there is nothing there to leave.

    Deliberately narrow: this flag governs the unsubscribe AFFORDANCE only.
    It does not exempt the type from the recipient's own group preference —
    a recipient who turned ``email_system`` off still does not get these.
    Whether a personal invitation should override that preference is a
    product question this flag does not answer.
    """
    return bool((get_routing(notification_type) or {}).get("transactional"))


def is_security(notification_type: str) -> bool:
    """True when this type is account-security / authentication class.

    Either by group (``auth``) or by the explicit ``"security": True`` flag,
    which exists for the letter that must stay switch-off-able — and so
    cannot live in ``auth`` — while still never offering a one-click opt-out:
    "your password was changed", "a new device signed in", "your export is
    ready". Like ``transactional`` this governs the AFFORDANCE only; the group
    still decides the recipient's preference and whether the mail is
    mandatory.
    """
    routing = get_routing(notification_type) or {}
    return bool(routing.get("security")) or routing.get("group") in SECURITY_GROUPS


def unsubscribe_allowed(routing: dict | None) -> bool:
    """May mail described by this routing entry carry an unsubscribe?

    THE single decision behind both the ``unsubscribe_url`` context variable
    (which drives the footer) and the ``List-Unsubscribe`` /
    ``List-Unsubscribe-Post: One-Click`` headers. Takes the effective routing
    ENTRY rather than a type name so the raw-content escape hatch — which
    synthesises an entry for an unregistered type — is judged by the same
    rule as everything else, instead of falling through a registry lookup
    that returns None.

    Allowlist, and deliberately so. The predicate this replaced was
    ``group != "auth"``, under which every way of *not saying* "auth" —
    ``"group": "Auth"``, a missing ``group`` key, a settings override that
    replaces a built-in auth entry wholesale — put a machine-actionable
    one-click opt-out from all security mail on a passcode. Now the wrong
    thing has to be spelled out: a type only becomes unsubscribable by naming
    a group that is in ``UNSUBSCRIBABLE_GROUPS``.
    """
    routing = routing or {}
    if routing.get("security") or routing.get("transactional"):
        return False
    return routing.get("group") in UNSUBSCRIBABLE_GROUPS


def may_carry_unsubscribe(notification_type: str) -> bool:
    """``unsubscribe_allowed`` asked by type name — the reader for hosts."""
    return unsubscribe_allowed(get_routing(notification_type))


def get_email_template(notification_type: str) -> str | None:
    """Template for a type.

    Precedence: per-type ``"template"`` key in the routing entry →
    ``STAPEL_NOTIFICATIONS["EMAIL_TEMPLATES"]`` override → built-in default.
    """
    routing = get_routing(notification_type) or {}
    overrides = notifications_settings.EMAIL_TEMPLATES or {}
    return (
        routing.get("template")
        or overrides.get(notification_type)
        or DEFAULT_EMAIL_TEMPLATES.get(notification_type)
    )


# ── WebSocket routes ────────────────────────────────────────────────────
#
# Two unrelated meanings of "routing" share this module, and only because
# ``stapel_realtime.build_websocket_application()`` discovers a module's
# sockets at ``<app>.routing.websocket_urlpatterns`` — the manifest lives in
# the library, the host assembly reads it, and nobody hand-wires a
# ProtocolTypeRouter. Everything above routes a notification TYPE to its
# channels; this section routes a BROWSER to the feed stream.
#
# Resolved through the module ``__getattr__`` rather than a plain assignment
# on purpose. This module is imported at boot (checks.py reads the catalog
# above), while the consumer needs stapel-realtime and Channels — an optional
# extra here. A top-level import would drag an ASGI stack into every
# deployment that only ever sends email; a top-level ``try/except ImportError``
# would do it to every deployment that merely has Channels installed for some
# other library. So the routes are built the first time somebody asks for
# them, which is exactly when the host is assembling its ASGI application.
#
# A host without the extra gets an empty route list, not a crash: the socket
# is absent, the REST feed is not. checks.py W006 makes that state visible
# rather than silent.

#: One mount, under the fleet's canonical ``ws/<module>/…`` prefix
#: (``realtime.W004`` warns about anything else). No user segment: the
#: consumer derives its stream key from the authenticated scope.
WEBSOCKET_ROUTE = "ws/notifications/inbox"


def __getattr__(name):
    if name != "websocket_urlpatterns":
        raise AttributeError(name)
    try:
        from .consumers import NotificationInboxConsumer
    except ImportError:
        return []
    from django.urls import path

    return [path(WEBSOCKET_ROUTE, NotificationInboxConsumer.as_asgi())]
