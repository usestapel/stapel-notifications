"""Channel backends: SMTP/Resend/Mailgun email, GatewayAPI/Twilio SMS, FCM push."""

import sys
import types

import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, data=None):
        self.ok = ok
        self.status_code = status_code
        self.text = "fake-body"
        self._data = data or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def fake_requests(monkeypatch):
    mod = types.ModuleType("requests")
    mod.calls = []
    mod.response = _FakeResponse(data={"id": "msg-1", "ids": [7]})

    def post(url, **kwargs):
        mod.calls.append((url, kwargs))
        return mod.response

    mod.post = post
    monkeypatch.setitem(sys.modules, "requests", mod)
    return mod


# ── Email ───────────────────────────────────────────────────────


class TestSMTPEmail:
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_sends_html_with_headers_and_inline_logo(self):
        from django.core import mail

        from stapel_notifications.channels.email import _SMTPEmailProvider

        _SMTPEmailProvider().send(
            "dest@example.com",
            "Hello",
            "<b>hi</b>",
            {"List-Unsubscribe": "<https://u>"},
        )
        (msg,) = mail.outbox
        assert msg.subject == "Hello"
        assert msg.to == ["dest@example.com"]
        assert msg.from_email == "no-reply@example.com"
        assert msg.body == "<b>hi</b>"
        assert msg.content_subtype == "html"
        assert msg.extra_headers["List-Unsubscribe"] == "<https://u>"
        assert len(msg.attachments) == 1  # inline cid:logo


class TestResendEmail:
    def test_requires_api_key(self, fake_requests):
        from stapel_notifications.channels.email import _ResendEmailProvider

        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            _ResendEmailProvider().send("d@example.com", "s", "<b/>", None)

    @override_settings(
        STAPEL_NOTIFICATIONS={"RESEND_API_KEY": "re_key"},
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_posts_payload_with_logo_and_headers(self, fake_requests):
        from stapel_notifications.channels.email import _ResendEmailProvider

        _ResendEmailProvider().send(
            "d@example.com", "Subj", "<b>x</b>", {"List-Unsubscribe": "<u>"}
        )
        (url, kwargs), = fake_requests.calls
        assert url == "https://api.resend.com/emails"
        assert kwargs["headers"]["Authorization"] == "Bearer re_key"
        payload = kwargs["json"]
        assert payload["to"] == ["d@example.com"]
        assert payload["subject"] == "Subj"
        assert payload["html"] == "<b>x</b>"
        assert payload["headers"] == {"List-Unsubscribe": "<u>"}
        assert payload["attachments"][0]["content_id"] == "logo"

    @override_settings(STAPEL_NOTIFICATIONS={"RESEND_API_KEY": "re_key"})
    def test_api_error_raises(self, fake_requests):
        from stapel_notifications.channels.email import _ResendEmailProvider

        fake_requests.response = _FakeResponse(ok=False, status_code=422)
        with pytest.raises(RuntimeError, match="Resend API error 422"):
            _ResendEmailProvider().send("d@example.com", "s", "x", None)


class TestMailgunEmail:
    def test_requires_credentials(self, fake_requests):
        from stapel_notifications.channels.email import _MailgunEmailProvider

        with pytest.raises(RuntimeError, match="MAILGUN_API_KEY"):
            _MailgunEmailProvider().send("d@example.com", "s", "x", None)

    @override_settings(
        STAPEL_NOTIFICATIONS={
            "MAILGUN_API_KEY": "mg_key",
            "MAILGUN_DOMAIN": "mg.example.com",
        },
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_posts_to_domain_endpoint(self, fake_requests):
        from stapel_notifications.channels.email import _MailgunEmailProvider

        _MailgunEmailProvider().send("d@example.com", "Subj", "<b>x</b>", None)
        (url, kwargs), = fake_requests.calls
        assert url == "https://api.mailgun.net/v3/mg.example.com/messages"
        assert kwargs["auth"] == ("api", "mg_key")
        assert kwargs["data"]["to"] == "d@example.com"
        assert kwargs["data"]["html"] == "<b>x</b>"


def test_email_mask():
    from stapel_notifications.channels.email import _mask

    assert _mask("alice@example.com") == "a***@example.com"
    assert _mask("not-an-email") == "***"
    assert _mask("@example.com") == "***@example.com"


# ── SMS ─────────────────────────────────────────────────────────


class TestGatewayAPISMS:
    def test_requires_token(self, fake_requests):
        from stapel_notifications.channels.sms import _GatewayAPISMSProvider

        with pytest.raises(RuntimeError, match="GATEWAYAPI_TOKEN"):
            _GatewayAPISMSProvider().send("+4512345678", "hi")

    @override_settings(
        STAPEL_NOTIFICATIONS={"GATEWAYAPI_TOKEN": "gw_tok", "GATEWAYAPI_SENDER": "Acme"}
    )
    def test_posts_msisdn_payload(self, fake_requests):
        from stapel_notifications.channels.sms import _GatewayAPISMSProvider

        _GatewayAPISMSProvider().send("+4512345678", "hello")
        (url, kwargs), = fake_requests.calls
        assert url == "https://gatewayapi.com/rest/mtsms"
        assert kwargs["headers"]["Authorization"] == "Token gw_tok"
        assert kwargs["json"] == {
            "sender": "Acme",
            "message": "hello",
            "recipients": [{"msisdn": 4512345678}],
        }


class TestTwilioSMS:
    @pytest.fixture
    def fake_twilio(self, monkeypatch):
        created = []

        class _Messages:
            def create(self, **kwargs):
                created.append(kwargs)

        class Client:
            def __init__(self, sid, token):
                self.sid = sid
                self.token = token
                self.messages = _Messages()

        rest = types.ModuleType("twilio.rest")
        rest.Client = Client
        twilio = types.ModuleType("twilio")
        twilio.rest = rest
        monkeypatch.setitem(sys.modules, "twilio", twilio)
        monkeypatch.setitem(sys.modules, "twilio.rest", rest)
        return created

    def test_requires_credentials(self, fake_twilio):
        from stapel_notifications.channels.sms import _TwilioSMSProvider

        with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
            _TwilioSMSProvider().send("+4512345678", "hi")

    @override_settings(
        STAPEL_NOTIFICATIONS={
            "TWILIO_ACCOUNT_SID": "AC1",
            "TWILIO_AUTH_TOKEN": "tok",
            "TWILIO_PHONE_NUMBER": "+1555000",
        }
    )
    def test_sends_via_client(self, fake_twilio):
        from stapel_notifications.channels.sms import _TwilioSMSProvider

        _TwilioSMSProvider().send("+4512345678", "hello")
        assert fake_twilio == [
            {"body": "hello", "from_": "+1555000", "to": "+4512345678"}
        ]


def test_sms_mask():
    from stapel_notifications.channels.sms import _mask

    assert _mask("+4512345678") == "+4***5678"
    assert _mask("123") == "***"


# ── Push (FCM) ──────────────────────────────────────────────────


@pytest.fixture
def fake_firebase(monkeypatch):
    from stapel_notifications.channels import push as push_mod

    sent = []

    class UnregisteredError(Exception):
        pass

    class Notification:
        def __init__(self, title, body):
            self.title = title
            self.body = body

    class Message:
        def __init__(self, notification, token, data):
            self.notification = notification
            self.token = token
            self.data = data
            self.apns = None

    def send(message):
        if message.token == "tok-dead":
            raise UnregisteredError("gone")
        if message.token == "tok-err":
            raise RuntimeError("fcm hiccup")
        sent.append(message)

    messaging = types.ModuleType("firebase_admin.messaging")
    messaging.Notification = Notification
    messaging.Message = Message
    messaging.APNSConfig = lambda payload: {"payload": payload}
    messaging.APNSPayload = lambda aps: {"aps": aps}
    messaging.Aps = lambda sound: {"sound": sound}
    messaging.UnregisteredError = UnregisteredError
    messaging.send = send

    credentials = types.ModuleType("firebase_admin.credentials")
    credentials.Certificate = lambda path: {"path": path}

    firebase = types.ModuleType("firebase_admin")
    firebase._apps = [object()]  # already initialised — skip Certificate load
    firebase.credentials = credentials
    firebase.messaging = messaging
    firebase.initialize_app = lambda cred: None

    monkeypatch.setitem(sys.modules, "firebase_admin", firebase)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", credentials)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", messaging)
    monkeypatch.setattr(push_mod, "_app_initialized", False)
    return sent


@pytest.mark.django_db
class TestFCMPush:
    def _tokens(self, user, *tokens):
        from stapel_notifications.models import DevicePushToken

        for token, platform in tokens:
            DevicePushToken.objects.create(
                user_id=user.id, token=token, platform=platform
            )

    def test_raises_when_firebase_unconfigured(self, user):
        from stapel_notifications.channels import push as push_mod

        with pytest.raises(RuntimeError, match="Firebase not configured"):
            push_mod._FCMPushProvider().send(str(user.id), "t", "b", None)

    @override_settings(
        STAPEL_NOTIFICATIONS={"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/creds.json"}
    )
    def test_sends_to_active_tokens_and_deactivates_dead_ones(
        self, user, fake_firebase
    ):
        from stapel_notifications.channels.push import _FCMPushProvider
        from stapel_notifications.models import DevicePushToken

        self._tokens(
            user, ("tok-ios", "ios"), ("tok-dead", "android"), ("tok-err", "web")
        )
        count = _FCMPushProvider().send(
            str(user.id), "Title", "Body", {"chat_url": "/c/1", "n": 2}
        )
        assert count == 1
        (msg,) = fake_firebase
        assert msg.token == "tok-ios"
        assert msg.notification.title == "Title"
        assert msg.data == {"chat_url": "/c/1", "n": "2"}  # values stringified
        assert msg.apns is not None  # iOS sound config attached
        # UnregisteredError deactivates the token; generic errors do not
        assert DevicePushToken.objects.get(token="tok-dead").is_active is False
        assert DevicePushToken.objects.get(token="tok-err").is_active is True

    @override_settings(
        STAPEL_NOTIFICATIONS={"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/creds.json"}
    )
    def test_no_tokens_returns_zero(self, user, fake_firebase):
        from stapel_notifications.channels.push import _FCMPushProvider

        assert _FCMPushProvider().send(str(user.id), "t", "b", None) == 0
        assert fake_firebase == []
