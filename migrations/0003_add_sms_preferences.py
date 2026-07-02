from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_add_auto_detected_language'),
    ]

    operations = [
        migrations.AddField(
            model_name='usernotificationsettings',
            name='sms_messages',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='usernotificationsettings',
            name='sms_system',
            field=models.BooleanField(default=True),
        ),
        # State-only sync with models.DevicePushToken.platform (choices were
        # never recorded in 0001; adding choices produces no SQL).
        migrations.AlterField(
            model_name='devicepushtoken',
            name='platform',
            field=models.CharField(
                choices=[('ios', 'iOS'), ('android', 'Android'), ('web', 'Web')],
                max_length=10,
            ),
        ),
    ]
