"""
All notification translation keys with English defaults.

These are exposed via GET /notifications/api/notification-keys/ for the
translate service collector. The translate service syncs them as
TranslationEntry records with source='backend:notifications'.
"""

NOTIFICATION_KEYS: dict[str, str] = {
    # ── OTP Code (A1) ───────────────────────────────────────────
    "notification.otp_code.subject": "Your {company_name} verification code: {code}",
    "notification.otp_code.heading": "Your verification code",
    "notification.otp_code.body": "Use the code below to verify your identity:",
    "notification.otp_code.warning": "Don't share this code with anyone. If you didn't request this, you can safely ignore this email.",
    "notification.otp_code.expiry": "This code expires in {expiry_minutes} minutes.",
    "notification.otp_code.sms": "Your {company_name} code: {code}. Expires in {expiry_minutes} min.",
    # ── Auth Change Requested (A2) ──────────────────────────────
    "notification.auth_change_requested.subject": "Authenticator change requested",
    "notification.auth_change_requested.heading": "Change requested",
    "notification.auth_change_requested.body": "Your {change_type} change has been requested. New {change_type}: {masked_new_value}. Change will complete on: {scheduled_date}.",
    "notification.auth_change_requested.warning": "If you did not request this change, please contact support immediately.",
    "notification.auth_change_requested.sms": "{company_name}: Your {change_type} change has been requested. Completes {scheduled_date}. Contact support if unexpected.",
    "notification.auth_change_requested.push_title": "Authenticator change requested",
    "notification.auth_change_requested.push_body": "Your {change_type} change will complete on {scheduled_date}.",
    # ── Auth Change Reminder (A3) ───────────────────────────────
    "notification.auth_change_reminder.subject": "Reminder: {change_type} change in {days_remaining} days",
    "notification.auth_change_reminder.heading": "Change reminder",
    "notification.auth_change_reminder.body": "Your {change_type} will be changed to {masked_new_value} in {days_remaining} days.",
    "notification.auth_change_reminder.warning": "If you did not request this, please contact support.",
    "notification.auth_change_reminder.sms": "{company_name}: Your {change_type} changes in {days_remaining} days to {masked_new_value}. Contact support if unexpected.",
    "notification.auth_change_reminder.push_title": "{change_type} change in {days_remaining} days",
    "notification.auth_change_reminder.push_body": "Your {change_type} will be changed to {masked_new_value}.",
    # ── Auth Change Urgent (A4) ─────────────────────────────────
    "notification.auth_change_urgent.subject": "URGENT: {change_type} change tomorrow",
    "notification.auth_change_urgent.heading": "Change tomorrow",
    "notification.auth_change_urgent.body": "Your {change_type} will be changed TOMORROW ({scheduled_date}). This is your last chance to cancel.",
    "notification.auth_change_urgent.warning": "After this change, you will need the new {change_type} to log in.",
    "notification.auth_change_urgent.sms": "URGENT: {company_name} {change_type} changes tomorrow ({scheduled_date}). Last chance to cancel!",
    "notification.auth_change_urgent.push_title": "URGENT: {change_type} change tomorrow",
    "notification.auth_change_urgent.push_body": "Last chance to cancel your {change_type} change.",
    # ── Auth Change Completed (A5) ──────────────────────────────
    "notification.auth_change_completed.subject": "{change_type} successfully changed",
    "notification.auth_change_completed.heading": "Change completed",
    "notification.auth_change_completed.body": "Your account {change_type} has been successfully changed.",
    "notification.auth_change_completed.warning": "If you did not authorize this change, contact support immediately.",
    "notification.auth_change_completed.sms": "{company_name}: Your {change_type} has been changed. Contact support if unexpected.",
    "notification.auth_change_completed.push_title": "{change_type} changed",
    "notification.auth_change_completed.push_body": "Your account {change_type} has been updated.",
    # ── New Message (B1) ────────────────────────────────────────
    "notification.new_message.subject": "New message from {sender_name}",
    "notification.new_message.heading": "New message from {sender_name}",
    "notification.new_message.body": "You have a new message about \u201c{listing_title}\u201d.",
    "notification.new_message.cta": "Open Chat",
    "notification.new_message.push_title": "New message from {sender_name}",
    "notification.new_message.push_body": "{sender_name}: {message_preview}",
    # ── Report Reviewed (C1) ────────────────────────────────────
    "notification.report_reviewed.subject": "Your report has been reviewed",
    "notification.report_reviewed.heading": "Your report has been reviewed",
    "notification.report_reviewed.body": "You reported a listing. After checking it, we have taken action.",
    "notification.report_reviewed.cta": "View Details",
    "notification.report_reviewed.push_title": "Report reviewed",
    "notification.report_reviewed.push_body": "Your report has been reviewed and action was taken.",
    # ── Listing Expiring (C2) ───────────────────────────────────
    "notification.listing_expiring.subject": "Your listing will expire soon",
    "notification.listing_expiring.heading": "Your listing is expiring",
    "notification.listing_expiring.body": "Your listing \u201c{listing_title}\u201d will be unpublished in {days_remaining} days. You can turn on auto-republish to keep it active.",
    "notification.listing_expiring.cta": "View Listing",
    "notification.listing_expiring.push_title": "Listing expiring",
    "notification.listing_expiring.push_body": "\u201c{listing_title}\u201d expires in {days_remaining} days.",
    # ── Listing Blocked (C3) ────────────────────────────────────
    "notification.listing_blocked.subject": "Your listing has been blocked",
    "notification.listing_blocked.heading": "Your listing has been blocked",
    "notification.listing_blocked.body": "Your listing \u201c{listing_title}\u201d has been blocked for violating {company_name} guidelines.",
    "notification.listing_blocked.cta": "Learn More",
    "notification.listing_blocked.warning": "If you believe this was a mistake, please contact our support team.",
    "notification.listing_blocked.push_title": "Listing blocked",
    "notification.listing_blocked.push_body": "\u201c{listing_title}\u201d was blocked for guideline violations.",
    # ── Magic Link Login (A6) ───────────────────────────────────
    "notification.magic_link_login.subject": "Your {company_name} sign-in link",
    "notification.magic_link_login.heading": "Sign in to {company_name}",
    "notification.magic_link_login.body": "Click the button below to sign in. This link expires in 15 minutes and can only be used once.",
    "notification.magic_link_login.cta": "Sign in",
    "notification.magic_link_login.warning": "If you didn't request this, you can safely ignore this email.",
    # ── New Device Login (A7) ───────────────────────────────────
    "notification.new_device_login.subject": "New sign-in to your account",
    "notification.new_device_login.heading": "New sign-in detected",
    "notification.new_device_login.body": "Your account was signed in from a new device.\n\nDevice: {device_name}\nIP: {ip_address}",
    "notification.new_device_login.cta": "Secure my account",
    "notification.new_device_login.warning": "If this was you, no action is needed. If you don't recognise this sign-in, secure your account immediately.",
    # ── Suspicious Login (A8) ───────────────────────────────────
    "notification.suspicious_login.subject": "Suspicious sign-in detected",
    "notification.suspicious_login.heading": "Suspicious sign-in detected",
    "notification.suspicious_login.body": "A sign-in from an unrecognised location was detected on your account.\n\nDevice: {device_name}\nIP: {ip_address}",
    "notification.suspicious_login.cta": "This wasn't me — revoke all sessions",
    "notification.suspicious_login.warning": "If this was you, no action is needed. If not, click the button above immediately to revoke all sessions.",
    # ── All Sessions Revoked (A9) ───────────────────────────────
    "notification.all_sessions_revoked.subject": "All sessions revoked",
    "notification.all_sessions_revoked.heading": "All sessions have been revoked",
    "notification.all_sessions_revoked.body": "All active sessions on your account have been revoked. You have been signed out of all devices.",
    "notification.all_sessions_revoked.warning": "If you did not initiate this action, please reset your password immediately.",
    # ── Footer / Shared ─────────────────────────────────────────
    "notification.footer.legal": "\u00a9 {company_year} {company_name}",
    "notification.footer.address": "{company_address}",
    "notification.footer.consent": "You received this email because you agreed to receive messages from {company_name}.",
    "notification.footer.unsubscribe": "Unsubscribe",
    "notification.footer.manage": "Manage notifications",
}
