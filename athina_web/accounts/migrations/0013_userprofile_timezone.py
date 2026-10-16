"""Per-user display timezone.

Grading timestamps are stored as UTC, so the UI needs to know which timezone to
render them in. 'auto' (the default) uses the browser's setting; any other value
is an IANA timezone name chosen from the profile page.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_gitlab_signup_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='timezone',
            field=models.CharField(
                blank=True, default='auto', max_length=64,
                help_text="Timezone used to display dates. 'auto' follows the "
                          "browser setting; otherwise an IANA name such as "
                          "America/Los_Angeles."),
        ),
    ]
