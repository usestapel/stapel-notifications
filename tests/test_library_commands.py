"""eject_notification_templates and check_notifications management commands."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from stapel_notifications.management.commands.check_notifications import check_paths


# ── eject_notification_templates ────────────────────────────────


def _eject(tmp_path, **kwargs):
    out = StringIO()
    call_command(
        "eject_notification_templates", out=str(tmp_path), stdout=out, **kwargs
    )
    return out.getvalue()


class TestEjectTemplates:
    def test_copies_all_templates_including_base_layout(self, tmp_path):
        output = _eject(tmp_path)
        email_dir = tmp_path / "notifications" / "email"
        assert (email_dir / "_base.html").is_file()
        assert (email_dir / "_footer_unsubscribe.html").is_file()
        assert (email_dir / "_raw_content.html").is_file()
        assert (email_dir / "otp_code.html").is_file()
        assert (email_dir / "new_message.html").is_file()
        # ejected copies are byte-identical to the packaged ones
        from stapel_notifications.management.commands.eject_notification_templates import (
            PACKAGED_EMAIL_DIR,
        )

        assert (email_dir / "otp_code.html").read_text() == (
            PACKAGED_EMAIL_DIR / "otp_code.html"
        ).read_text()
        # next-steps hint about loader-order override
        assert "Next steps" in output
        assert "TEMPLATES[0]['DIRS']" in output

    def test_only_filters_types_but_keeps_partials(self, tmp_path):
        _eject(tmp_path, only="otp_code,new_message")
        email_dir = tmp_path / "notifications" / "email"
        assert (email_dir / "otp_code.html").is_file()
        assert (email_dir / "new_message.html").is_file()
        assert (email_dir / "_base.html").is_file()  # layout always ejected
        assert not (email_dir / "suspicious_login.html").exists()

    def test_only_rejects_unknown_type(self, tmp_path):
        with pytest.raises(CommandError, match="unknown notification type"):
            _eject(tmp_path, only="no_such_type")

    def test_dry_run_writes_nothing(self, tmp_path):
        output = _eject(tmp_path, dry_run=True)
        assert "would write" in output
        assert not (tmp_path / "notifications").exists()

    def test_rerun_skips_existing_without_force(self, tmp_path):
        _eject(tmp_path)
        target = tmp_path / "notifications" / "email" / "otp_code.html"
        target.write_text("customized")
        output = _eject(tmp_path)
        assert "skipped (exists)" in output
        assert target.read_text() == "customized"  # not clobbered

    def test_force_overwrites(self, tmp_path):
        _eject(tmp_path)
        target = tmp_path / "notifications" / "email" / "otp_code.html"
        target.write_text("customized")
        _eject(tmp_path, force=True)
        assert target.read_text() != "customized"


# ── check_notifications ─────────────────────────────────────────


GOOD = 'request_notification("otp_code", user_id="u1")\n'
GOOD_KWARG = 'request_notification(notification_type="new_message", user_id="u1")\n'
BAD = 'request_notification("no_such_type", user_id="u1")\n'
ESCAPE_HATCH = 'request_notification("no_such_type_2", email="e", content_html="<p/>")\n'
NONE_CONTENT = 'request_notification("no_such_type_3", email="e", content_html=None, content_text=None)\n'
DYNAMIC = 'request_notification(some_variable, user_id="u1")\n'
NO_TYPE = 'request_notification(user_id="u1")\n'


class TestCheckNotifications:
    def _issues(self, tmp_path, source):
        (tmp_path / "callsites.py").write_text(source)
        return check_paths([str(tmp_path)])

    def test_registered_literal_types_pass(self, tmp_path):
        assert self._issues(tmp_path, GOOD + GOOD_KWARG) == []

    def test_unregistered_literal_type_is_an_error(self, tmp_path):
        (issue,) = self._issues(tmp_path, BAD)
        assert issue.level == "error"
        assert "no_such_type" in issue.message
        assert issue.line == 1

    def test_content_escape_hatch_is_exempt(self, tmp_path):
        assert self._issues(tmp_path, ESCAPE_HATCH) == []

    def test_explicit_none_content_does_not_count_as_escape_hatch(self, tmp_path):
        (issue,) = self._issues(tmp_path, NONE_CONTENT)
        assert issue.level == "error"

    def test_dynamic_type_is_a_warning(self, tmp_path):
        (issue,) = self._issues(tmp_path, DYNAMIC)
        assert issue.level == "warning"
        assert "dynamic" in issue.message

    def test_call_without_type_is_an_error(self, tmp_path):
        (issue,) = self._issues(tmp_path, NO_TYPE)
        assert issue.level == "error"

    def test_types_registered_via_settings_pass(self, tmp_path):
        src = 'request_notification("invoice_ready", user_id="u1")\n'
        with override_settings(
            STAPEL_NOTIFICATIONS={
                "TYPES": {"invoice_ready": {"channels": ["email"], "group": "system"}}
            }
        ):
            assert self._issues(tmp_path, src) == []

    def test_empty_registry_guard(self, tmp_path, monkeypatch):
        from stapel_notifications.management.commands import check_notifications as mod

        monkeypatch.setattr(mod, "registered_types", lambda: [])
        (issue,) = self._issues(tmp_path, BAD)
        assert issue.level == "warning"
        assert "registry is empty" in issue.message

    def test_command_exits_1_on_error(self, tmp_path):
        (tmp_path / "bad.py").write_text(BAD)
        with pytest.raises(SystemExit) as exc:
            call_command(
                "check_notifications", str(tmp_path),
                stdout=StringIO(), stderr=StringIO(),
            )
        assert exc.value.code == 1

    def test_command_passes_on_clean_tree(self, tmp_path):
        (tmp_path / "good.py").write_text(GOOD)
        out = StringIO()
        call_command("check_notifications", str(tmp_path), stdout=out)
        assert "OK" in out.getvalue()
