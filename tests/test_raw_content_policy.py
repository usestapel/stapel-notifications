"""Who may put a body inside this brand's letterhead.

NOTIFY-02 (security audit 2026-08-11): ``process_notification`` accepted an
unregistered type with caller-supplied ``content_html`` and rendered it with
``|safe`` inside the branded layout, so anything able to reach the
notification bus could send byte-perfect first-party phishing.
"""
import pytest
from django.test import override_settings

from stapel_notifications.conf import notifications_settings
from stapel_notifications.models import NotificationLog
from stapel_notifications.raw_content import mode
from stapel_notifications.services import process_notification


@pytest.fixture(autouse=True)
def _reload_settings():
    notifications_settings.reload()
    yield
    notifications_settings.reload()


class _CapturingEmailProvider:
    sent = []

    def send(self, recipient, subject, html_body, headers):
        type(self).sent.append({"recipient": recipient, "subject": subject, "html": html_body})


CAPTURE = f"{_CapturingEmailProvider.__module__}._CapturingEmailProvider"
PHISH = '<a href="https://not-us.example/login">Confirm your account</a>'


@pytest.fixture
def capture_email():
    _CapturingEmailProvider.sent = []
    return _CapturingEmailProvider.sent


def _process(ntype, raw_content=None, **kwargs):
    conf = {"EMAIL_PROVIDER": CAPTURE}
    if raw_content is not None:
        conf["RAW_CONTENT"] = raw_content
    with override_settings(STAPEL_NOTIFICATIONS=conf):
        process_notification(
            notification_type=ntype,
            user_id=None,
            variables=kwargs.pop("variables", {}),
            email="dest@example.com",
            **kwargs,
        )


@pytest.mark.django_db
class TestTheHatchIsShutUnlessAskedFor:
    def test_default_is_off(self):
        assert mode() == "off"

    def test_unregistered_type_with_html_sends_nothing(self, capture_email, caplog):
        with caplog.at_level("ERROR", logger="stapel_notifications.raw_content"):
            _process("adhoc.announcement", content_html=PHISH)
        assert capture_email == []
        assert NotificationLog.objects.count() == 0
        assert any("Raw content refused" in r.getMessage() for r in caplog.records)

    def test_registered_type_keeps_its_own_template(self, capture_email):
        """A caller body cannot displace a registered letter either."""
        _process("otp_code", variables={"code": "1234", "expiry_minutes": 5},
                 content_html=PHISH)
        (mail,) = capture_email
        assert "not-us.example" not in mail["html"]
        assert "1234" in mail["html"]  # the registered otp_code template ran


@pytest.mark.django_db
class TestTextMode:
    """A body, yes; markup of the caller's choosing, no."""

    def test_markup_is_reduced_to_its_text(self, capture_email):
        _process("adhoc.announcement", raw_content="text", content_html=PHISH)
        (mail,) = capture_email
        assert "https://not-us.example/login" not in mail["html"]
        assert "&lt;a href" not in mail["html"]  # not escaped markup — no markup
        assert "Confirm your account" in mail["html"]

    def test_plain_content_text_is_unaffected(self, capture_email):
        _process("adhoc.plain", raw_content="text", content_text="line one\nline two")
        (mail,) = capture_email
        assert "line one<br>line two" in mail["html"]


@pytest.mark.django_db
class TestHtmlModeIsTheDeploymentsDeclaration:
    def test_opting_in_restores_the_escape_hatch(self, capture_email):
        _process("adhoc.announcement", raw_content="html",
                 variables={"subject": "Big news"},
                 content_html='<p id="adhoc-body">Hello there</p>')
        (mail,) = capture_email
        assert '<p id="adhoc-body">Hello there</p>' in mail["html"]
        assert mail["subject"] == "Big news"
        assert NotificationLog.objects.get(notification_type="adhoc.announcement").status == "sent"

    def test_boot_warns_about_it(self):
        from stapel_notifications.checks import check_raw_content_is_a_decision

        with override_settings(STAPEL_NOTIFICATIONS={"RAW_CONTENT": "html"}):
            (warning,) = check_raw_content_is_a_decision(None)
        assert warning.id == "stapel_notifications.W004"
        with override_settings(STAPEL_NOTIFICATIONS={}):
            assert check_raw_content_is_a_decision(None) == []

    def test_a_typo_closes_the_hatch_it_does_not_open_it(self, capture_email, caplog):
        with caplog.at_level("WARNING", logger="stapel_notifications.raw_content"):
            _process("adhoc.announcement", raw_content="HTML!", content_html=PHISH)
        assert capture_email == []
        assert any("is not one of" in r.getMessage() for r in caplog.records)


class TestTheCiGateFollowsTheSetting:
    """``check_notifications`` must not certify a call that sends nothing."""

    SRC = 'request_notification("no_such_type", email="e", content_html="<p/>")\n'

    def _issues(self, tmp_path):
        from stapel_notifications.management.commands.check_notifications import check_paths

        (tmp_path / "callsites.py").write_text(self.SRC)
        return check_paths([str(tmp_path)])

    def test_hatch_shut_makes_the_call_site_an_error(self, tmp_path):
        with override_settings(STAPEL_NOTIFICATIONS={}):
            (issue,) = self._issues(tmp_path)
        assert issue.level == "error"
        assert "RAW_CONTENT" in issue.message

    def test_hatch_open_exempts_it_as_before(self, tmp_path):
        with override_settings(STAPEL_NOTIFICATIONS={"RAW_CONTENT": "html"}):
            assert self._issues(tmp_path) == []
