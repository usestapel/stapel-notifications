from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0003_add_sms_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="usercontact",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Soft-deactivated during an account-closure grace period "
                    "(user.deletion_initiated); reactivated by the next contact "
                    "sync. Inactive contacts are not used as notification "
                    "recipients."
                ),
            ),
        ),
    ]
