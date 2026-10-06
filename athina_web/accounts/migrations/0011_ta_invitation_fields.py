"""TA account invitations: editable email template and forced password change.

Adds a per-faculty welcome-email template (so one saved body can be reused for
every TA) plus fields tracking whether a user must change the password an admin
set for them.

`temp_password` holds the plaintext temporarily: Django stores only a hash, so
without it a faculty member could never email the credentials that were
generated. It is treated as expired after 24 hours (see
`UserProfile.temp_password_is_fresh`), which is why no data migration is needed
for existing users — they simply have no temp password to send.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_notification_reply_to'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='welcome_email_subject',
            field=models.CharField(
                blank=True, default='', max_length=255,
                help_text='Subject line for the TA account email. Leave blank to use the default.'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='welcome_email_body',
            field=models.TextField(
                blank=True, default='',
                help_text='Body of the TA account email. Leave blank to use the default.'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='must_change_password',
            field=models.BooleanField(
                default=False,
                help_text='Force the user to choose a new password at next login.'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='temp_password',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='temp_password_created_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
