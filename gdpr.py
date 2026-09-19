from stapel_core.gdpr import GDPRProvider

from .erasure import GDPR_OWNER


class NotificationsGDPRProvider(GDPRProvider):
    #: Same name the comm receipts and probe answers carry — one owner, one
    #: declaration in ``STAPEL_GDPR["DATA_OWNERS"]``, whichever of the two
    #: participation modes a deployment uses.
    section = GDPR_OWNER

    def export(self, user_id: int) -> dict:
        from .models import (
            DevicePushToken,
            NotificationLog,
            ParkedDispatch,
            UserContact,
            UserNotificationSettings,
        )

        contact = {}
        try:
            c = UserContact.objects.get(user_id=user_id)
            contact = {
                'email': c.email,
                'phone': c.phone,
                'telegram_chat_id': c.telegram_chat_id,
            }
        except UserContact.DoesNotExist:
            pass

        settings = {}
        try:
            s = UserNotificationSettings.objects.get(user_id=user_id)
            settings = {
                'email_messages': s.email_messages,
                'email_system':   s.email_system,
                'push_messages':  s.push_messages,
                'push_system':    s.push_system,
                'sms_messages':   s.sms_messages,
                'sms_system':     s.sms_system,
                'telegram_messages': s.telegram_messages,
                'telegram_system':   s.telegram_system,
            }
        except UserNotificationSettings.DoesNotExist:
            pass

        devices = list(DevicePushToken.objects.filter(user_id=user_id).values(
            'platform', 'is_active', 'created_at',
        ))

        # Export log metadata without recipient PII. `read_at` is in here
        # because it is a record of what this person DID (opened their feed
        # and cleared these rows), not of what we sent them — the half of the
        # journal an export would be incomplete without.
        logs = list(NotificationLog.objects.filter(user_id=user_id).values(
            'notification_type', 'channel', 'status', 'language', 'created_at',
            'read_at',
        ))

        # Notifications waiting for an address to arrive. Metadata only:
        # the row's `request` holds the caller's raw template variables, and
        # an export is a file the subject downloads and keeps — it must not
        # be the one place a deep link or a caller-supplied value outlives
        # the 72-hour table it was parked in.
        parked = list(ParkedDispatch.objects.filter(user_id=user_id).values(
            'notification_type', 'channel', 'created_at',
        ))

        return {
            'contact':  contact,
            'settings': settings,
            'devices':  _serialize_dates(devices),
            'log':      _serialize_dates(logs),
            'parked':   _serialize_dates(parked),
        }

    def delete(self, user_id: int) -> None:
        """Erase the account slice — one implementation, three callers.

        The in-process provider, the deprecated ``user.deleted`` subscriber
        and the ``gdpr.erasure.requested`` subscriber all reach
        :func:`~stapel_notifications.erasure.erase_account`, so a deployment
        cannot get a different erasure depending on which participation
        mode it happens to use. The journal is anonymised rather than
        deleted there, for the reason it always was: a delivery audit trail
        with a hole in it is not an erasure, it is a missing record.
        """
        from .erasure import erase_account

        erase_account(user_id)

    def anonymize(self, user_id: int) -> None:
        # Handled in delete() — recipient and user_id cleared from logs.
        pass


def _serialize_dates(rows: list[dict]) -> list[dict]:
    return [
        {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in row.items()}
        for row in rows
    ]
