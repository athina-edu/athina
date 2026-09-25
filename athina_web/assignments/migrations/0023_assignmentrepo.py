"""Introduce AssignmentRepo: repositories belong to one assignment, not the course.

Historically `Student.repository_url` / `notified_at` were course-scoped, which
could not express that a student has a separate repo per assignment. This
migration creates the per-assignment table and moves any existing repo
assignment onto it.

The data migration is deliberately conservative: a legacy `repository_url` is
only attributed to an assignment when the grading database (or, failing that,
repo naming) makes the owner unambiguous. See the docstring on
`migrate_legacy_repositories` for the exact rules.
"""

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def migrate_legacy_repositories(apps, schema_editor):
    """Copy legacy course-scoped repo data onto per-assignment rows.

    A legacy `repository_url` is attached to an assignment only when we can be
    reasonably sure it belongs there:

    1. If the grading engine's `users` table is reachable, the row for the
       student records the assignment explicitly — that is authoritative, so we
       copy the URL onto the matching assignment.
    2. Otherwise, if the course has exactly ONE assignment, the URL can only
       belong to that one.
    3. Otherwise we create no repo row. Guessing would hand a student the wrong
       URL for an assignment, so we leave it for re-provisioning.

    The legacy columns are intentionally left in place (not dropped) so this
    migration is reversible and so a failed deploy can be rolled back without
    losing data.
    """
    Student = apps.get_model('assignments', 'Student')
    AssignmentRepo = apps.get_model('assignments', 'AssignmentRepo')

    # Step 1: authoritative assignment ids from the grading DB, if available.
    #
    # The connection is built here rather than imported from the app: migrations
    # must not depend on view code, which imports the very models being migrated.
    assignment_id_by_email = {}
    try:
        import pymysql
        from athina_web.athina_db import db_info

        details = db_info()
        if details.athina_mysql_host:
            conn = pymysql.connect(host=details.athina_mysql_host,
                                   user=details.athina_mysql_username,
                                   password=details.athina_mysql_password,
                                   port=int(details.athina_mysql_port),
                                   db="athina")
            try:
                cur = conn.cursor()
                cur.execute("SELECT secondary_id, assignment_id, repository_url FROM users "
                            "WHERE secondary_id IS NOT NULL AND secondary_id <> ''")
                for secondary_id, assignment_id, repo_url in cur.fetchall():
                    if repo_url:
                        assignment_id_by_email[secondary_id.strip().lower()] = (assignment_id, repo_url)
            finally:
                conn.close()
    except Exception:
        # Grading DB not reachable (e.g. running migrations before MySQL is up).
        # Fall through to the single-assignment heuristic.
        assignment_id_by_email = {}

    for student in Student.objects.exclude(repository_url=''):
        assignment = None
        match = None

        # Rule 1: the grading DB told us which assignment this repo belongs to.
        match = assignment_id_by_email.get((student.email or '').strip().lower())
        if match:
            assignment = student.course.assignments.filter(pk=match[0]).first()

        # Rule 2: exactly one assignment — the URL cannot belong anywhere else.
        if assignment is None:
            course_assignments = list(student.course.assignments.all())
            if len(course_assignments) == 1:
                assignment = course_assignments[0]

        if assignment is None:
            continue

        repo_url = student.repository_url
        if match and match[1]:
            repo_url = match[1]

        AssignmentRepo.objects.update_or_create(
            assignment=assignment, student=student,
            defaults={
                'repository_url': repo_url,
                'notified_at': student.notified_at,
            },
        )


def reverse_migration(apps, schema_editor):
    """Nothing to do: the legacy columns were never removed."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('assignments', '0022_backfill_gitlab_username'),
    ]

    operations = [
        migrations.CreateModel(
            name='AssignmentRepo',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('repository_url', models.CharField(blank=True, default='', max_length=500)),
                ('notified_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Notified At')),
                ('date_created', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Date Created')),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='repos', to='assignments.assignment')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignment_repos', to='assignments.student')),
            ],
            options={
                'ordering': ['student__email'],
                'unique_together': {('assignment', 'student')},
            },
        ),
        migrations.RunPython(migrate_legacy_repositories, reverse_migration),
    ]
