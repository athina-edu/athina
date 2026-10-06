"""Add StudentGroup and GroupMembership for group assignments.

The grading engine infers groups from students sharing a repository URL, so the
web app needs somewhere to record which students share one. These models store
that intent; provisioning then creates a single repo per group.

Nothing is backfilled: existing repos are one-per-student, and there is no way
to tell which students "should" have been grouped together.
"""

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('assignments', '0023_assignmentrepo'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentGroup',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('repo_index', models.PositiveIntegerField(default=0)),
                ('date_created', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Date Created')),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='groups', to='assignments.assignment')),
            ],
            options={
                'ordering': ['repo_index', 'name'],
                'unique_together': {('assignment', 'name')},
            },
        ),
        migrations.CreateModel(
            name='GroupMembership',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_added', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Date Added')),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='assignments.studentgroup')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_memberships', to='assignments.student')),
            ],
            options={
                'ordering': ['student__email'],
                'unique_together': {('group', 'student')},
            },
        ),
    ]
