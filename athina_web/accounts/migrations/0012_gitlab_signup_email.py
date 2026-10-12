"""Editable template for asking students to create a GitLab account.

Provisioning cannot create a repository for a student with no GitLab account —
the repo is private, so they would be locked out of it. Faculty need to ask the
student to register, and want the message saved rather than retyped each time.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_ta_invitation_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='signup_email_subject',
            field=models.CharField(
                blank=True, default='', max_length=255,
                help_text="Subject for the 'create a GitLab account' email. Blank uses the default."),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='signup_email_body',
            field=models.TextField(
                blank=True, default='',
                help_text="Body of the 'create a GitLab account' email. Blank uses the default."),
        ),
    ]
