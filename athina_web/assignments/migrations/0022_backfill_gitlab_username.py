# Backfill the GitLab account name for students created before it was derived
# automatically.

import re

from django.db import migrations


def backfill_gitlab_username(apps, schema_editor):
    """Give existing students their email-prefix GitLab account.

    The convention is that a student's GitLab account matches the part of their
    email before @. Student.save() now fills this in, but rows created before
    that change are blank and would fail provisioning.
    """
    Student = apps.get_model('assignments', 'Student')
    for student in Student.objects.filter(gitlab_username='').exclude(email=''):
        prefix = re.sub(r'[^A-Za-z0-9._-]', '', student.email.split('@')[0])
        if prefix:
            student.gitlab_username = prefix
            student.save(update_fields=['gitlab_username'])


class Migration(migrations.Migration):

    dependencies = [
        ('assignments', '0021_student_notified_at'),
    ]

    operations = [
        migrations.RunPython(backfill_gitlab_username, migrations.RunPython.noop),
    ]
