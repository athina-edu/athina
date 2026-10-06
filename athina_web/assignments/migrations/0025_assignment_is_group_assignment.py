"""Mark assignments as group or individual, and enforce one group per student.

Adds `Assignment.is_group_assignment` (off by default, so existing assignments
keep behaving as individual work) and a database constraint preventing a student
from being in two groups on the same assignment.

`GroupMembership.assignment` is denormalised from `group.assignment` so the
constraint can be expressed in SQLite/MySQL. It is backfilled here and kept in
sync by GroupMembership.save().
"""

from django.db import migrations, models
import django.db.models.deletion


def backfill_membership_assignment(apps, schema_editor):
    """Copy assignment_id onto each membership from its group."""
    GroupMembership = apps.get_model('assignments', 'GroupMembership')
    for membership in GroupMembership.objects.select_related('group'):
        membership.assignment_id = membership.group.assignment_id
        membership.save(update_fields=['assignment'])


def mark_group_assignments(apps, schema_editor):
    """Flag assignments that already have groups.

    Anyone who created groups through the web UI before this field existed
    clearly intends that assignment to be a group assignment, so infer it rather
    than leaving the checkbox off and silently disabling their existing setup.
    """
    Assignment = apps.get_model('assignments', 'Assignment')
    with_groups = (apps.get_model('assignments', 'StudentGroup')
                   .objects.values_list('assignment_id', flat=True).distinct())
    Assignment.objects.filter(pk__in=list(with_groups)).update(is_group_assignment=True)


class Migration(migrations.Migration):

    dependencies = [
        ('assignments', '0024_studentgroup'),
    ]

    operations = [
        migrations.AddField(
            model_name='assignment',
            name='is_group_assignment',
            field=models.BooleanField(
                default=False,
                help_text='Students work in groups sharing one repository. Leave unchecked '
                          'for individual work, where every student gets their own repository.',
                verbose_name='Group assignment'),
        ),
        migrations.AddField(
            model_name='groupmembership',
            name='assignment',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='group_memberships', to='assignments.assignment'),
        ),
        migrations.RunPython(backfill_membership_assignment, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='groupmembership',
            constraint=models.UniqueConstraint(
                fields=('assignment', 'student'),
                name='one_group_per_student_per_assignment'),
        ),
        migrations.RunPython(mark_group_assignments, migrations.RunPython.noop),
    ]
