from stapel_core.gdpr import GDPRProvider


class NotificationsGDPRProvider(GDPRProvider):
    section = 'notifications'

    def export(self, user_id: int) -> dict:
        from .models import DevicePushToken, NotificationLog, UserContact, UserNotificationSettings

        contact = {}
        try:
            c = UserContact.objects.get(user_id=user_id)
            contact = {'email': c.email, 'phone': c.phone}
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
                'language':       s.language,
            }
        except UserNotificationSettings.DoesNotExist:
            pass

        devices = list(DevicePushToken.objects.filter(user_id=user_id).values(
            'platform', 'is_active', 'created_at',
        ))

        # Export log metadata without recipient PII
        logs = list(NotificationLog.objects.filter(user_id=user_id).values(
            'notification_type', 'channel', 'status', 'language', 'created_at',
        ))

        return {
            'contact':  contact,
            'settings': settings,
            'devices':  _serialize_dates(devices),
            'log':      _serialize_dates(logs),
        }

    def delete(self, user_id: int) -> None:
        from .models import DevicePushToken, NotificationLog, UserContact, UserNotificationSettings

        UserContact.objects.filter(user_id=user_id).delete()
        DevicePushToken.objects.filter(user_id=user_id).delete()
        UserNotificationSettings.objects.filter(user_id=user_id).delete()
        # Anonymise log rows rather than delete — preserves delivery audit trail
        NotificationLog.objects.filter(user_id=user_id).update(
            recipient='',
            user_id=None,
        )

    def anonymize(self, user_id: int) -> None:
        # Handled in delete() — recipient and user_id cleared from logs.
        pass


def _serialize_dates(rows: list[dict]) -> list[dict]:
    return [
        {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in row.items()}
        for row in rows
    ]
