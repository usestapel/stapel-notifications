"""Copy the packaged email templates into the host project for customization.

    python manage.py eject_notification_templates --out templates/
    python manage.py eject_notification_templates --out templates/ --only otp_code,new_message
    python manage.py eject_notification_templates --out templates/ --dry-run
    python manage.py eject_notification_templates --out templates/ --force

Files land under <out>/notifications/email/ — the same namespaced path the
package uses — so a project-level template DIR earlier in the Django loader
order overrides the packaged copies without any settings changes.
Existing files are skipped unless --force is given.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from stapel_notifications.routing import DEFAULT_EMAIL_TEMPLATES

PACKAGED_EMAIL_DIR = Path(__file__).resolve().parents[2] / "templates" / "notifications" / "email"

# Shared layout/partials every ejected type template needs.
ALWAYS_EJECT = ("_base.html", "_footer_unsubscribe.html", "_raw_content.html")


class Command(BaseCommand):
    help = (
        "Copy the packaged notification email templates (incl. the base "
        "layout) into the host project so they can be customized on-site."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", default="templates/",
            help="Target template directory (default: templates/)",
        )
        parser.add_argument(
            "--only", default="",
            help=(
                "Comma-separated notification types to eject "
                "(e.g. otp_code,new_message); default: all"
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be written without writing anything",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite files that already exist in the target",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        only = [t.strip() for t in (options["only"] or "").split(",") if t.strip()]

        if only:
            unknown = [t for t in only if t not in DEFAULT_EMAIL_TEMPLATES]
            if unknown:
                raise CommandError(
                    f"unknown notification type(s): {', '.join(unknown)} — "
                    f"choose from: {', '.join(sorted(DEFAULT_EMAIL_TEMPLATES))}"
                )
            filenames = sorted(
                {Path(DEFAULT_EMAIL_TEMPLATES[t]).name for t in only}
            )
        else:
            filenames = sorted(
                p.name for p in PACKAGED_EMAIL_DIR.glob("*.html")
                if p.name not in ALWAYS_EJECT
            )
        filenames = list(ALWAYS_EJECT) + filenames

        target_dir = Path(options["out"]) / "notifications" / "email"
        created = skipped = 0
        for name in filenames:
            src = PACKAGED_EMAIL_DIR / name
            if not src.is_file():
                self.stderr.write(self.style.WARNING(f"  missing in package: {name}"))
                continue
            dst = target_dir / name
            if dst.exists() and not force:
                self.stdout.write(f"  skipped (exists): {dst}")
                skipped += 1
                continue
            if dry_run:
                self.stdout.write(f"  would write: {dst}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                self.stdout.write(f"  created: {dst}")
            created += 1

        verb = "would write" if dry_run else "wrote"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {created} template(s) to {target_dir}/ ({skipped} skipped)"
        ))
        self.stdout.write(
            "\nNext steps:\n"
            f"  1. Make sure '{options['out']}' is listed in TEMPLATES[0]['DIRS'] "
            "(project DIRS are consulted before app templates, so your copies "
            "win by loader order).\n"
            "  2. Edit the ejected files — shared branding lives in "
            "notifications/email/_base.html; per-type content in the other "
            "files.\n"
            "  3. Alternatively map individual types to any template path via "
            "STAPEL_NOTIFICATIONS['EMAIL_TEMPLATES'].\n"
        )
