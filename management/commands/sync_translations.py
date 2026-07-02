"""Initial population of the notification TranslationCache.

Pulls every NOTIFICATION_KEYS key through the ``translate.resolve`` comm
Function for each configured language and stores the values locally.
Run once after deploy (or whenever the cache should be rebuilt); ongoing
updates arrive via the ``translations.changed`` action, and individual
misses are resolved lazily at render time.

    python manage.py sync_translations
    python manage.py sync_translations --languages de,fr
"""

from django.core.management.base import BaseCommand

from stapel_notifications.conf import notifications_settings
from stapel_notifications.translation_keys import NOTIFICATION_KEYS
from stapel_notifications.translations import resolve_and_cache


class Command(BaseCommand):
    help = (
        "Populate the notification TranslationCache from the translate "
        "module (via the translate.resolve comm Function)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--languages",
            default="",
            help=(
                "Comma-separated language codes to sync "
                "(default: STAPEL_NOTIFICATIONS['LANGUAGES'])"
            ),
        )

    def handle(self, *args, **options):
        languages = [
            lang.strip()
            for lang in (options["languages"] or "").split(",")
            if lang.strip()
        ] or list(notifications_settings.LANGUAGES or ["en"])

        keys = sorted(NOTIFICATION_KEYS)
        total = 0
        for language in languages:
            try:
                resolved = resolve_and_cache(keys, language)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(
                    f"{language}: translate.resolve failed ({exc}) — is the "
                    "translate module installed/reachable?"
                ))
                continue
            total += len(resolved)
            self.stdout.write(
                f"{language}: synced {len(resolved)}/{len(keys)} key(s)"
            )
        self.stdout.write(self.style.SUCCESS(
            f"done — {total} translation value(s) cached for "
            f"{len(languages)} language(s)"
        ))
