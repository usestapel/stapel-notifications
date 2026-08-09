# stapel: contract-phase
"""Drop the language mirror: the language is asked of profiles, not copied.

Contract-phase note (release-management.md): this REMOVES two columns, and
that is the point of the change rather than an incident of it. The columns
mirrored ``profiles.Profile.app_language`` / ``.auto_detected_language``
through a bus consumer that cannot run on an in-process bus, so the mirror
was empty everywhere it was read from — nothing is lost by dropping it, and
leaving it would leave a second, silently-stale answer to a question that
now has one owner (``profiles.language``, see stapel_notifications/language.py).

Marked contract-phase in the SAME release that stops reading the columns,
rather than one release later, because the usual reason for the corridor —
a rollback might read the column again — cannot bite: the columns are empty
in every deployment that has them (the writer never ran), so a rollback
reads nothing either way, and the release notes for 0.9.0 carry the measured
numbers.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_usercontact_is_active"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="usernotificationsettings",
            name="language",
        ),
        migrations.RemoveField(
            model_name="usernotificationsettings",
            name="auto_detected_language",
        ),
    ]
