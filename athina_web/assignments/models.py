import re

from django.db import models
from django.utils import timezone


class Course(models.Model):
    """A course groups multiple assignments together."""
    name = models.CharField(max_length=200)
    owner = models.IntegerField(editable=False, default=1)
    date_created = models.DateTimeField('Date Created', default=timezone.now, editable=False)

    class Meta:
        verbose_name_plural = "courses"
        ordering = ['name']
        unique_together = ('name', 'owner')

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        keep_characters = (' ', '.', '_', '-')
        self.name = "".join(c for c in self.name if c.isalnum() or c in keep_characters).rstrip()
        super(Course, self).save(*args, **kwargs)


class Student(models.Model):
    """A student enrolled in a course, with optional GitLab provisioning."""
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='students')
    email = models.EmailField(max_length=255)
    username = models.CharField(max_length=255, blank=True,
                                help_text="Derived from email prefix if left blank.")
    gitlab_username = models.CharField(max_length=255, blank=True, default="")
    # NOTE: `repository_url` and `notified_at` are DEPRECATED.
    #
    # A student has one repository *per assignment*, not per course, so these
    # course-scoped fields cannot express reality (a student enrolled in
    # "SQL 1" and "SQL 2" has two repos, and being notified about one says
    # nothing about the other). They are retained only so the historical
    # values can be migrated into AssignmentRepo by migration 0024; the live
    # per-assignment state now lives on AssignmentRepo. Do not read them in
    # new code.
    repository_url = models.CharField(max_length=500, blank=True, default="")
    date_added = models.DateTimeField('Date Added', default=timezone.now, editable=False)
    # When the student was last emailed about their repository. NULL means they
    # have never been notified, so the provisioning flow will (re)send.
    notified_at = models.DateTimeField('Notified At', null=True, blank=True, editable=False)

    class Meta:
        unique_together = ('course', 'email')
        ordering = ['email']

    def __str__(self):
        return "%s (%s)" % (self.email, self.course.name)

    def save(self, *args, **kwargs):
        if not self.username:
            # Prefer the GitLab username when known, otherwise derive from the email prefix.
            if self.gitlab_username:
                self.username = self.gitlab_username
            elif self.email:
                self.username = self.email.split('@')[0]
        if not self.gitlab_username and self.email:
            # Convention: the GitLab account matches the email prefix. Resolving it
            # here (not only at provisioning time) means the student list shows it
            # immediately and the record is correct however it was created — by the
            # add form, bulk import, or the Django admin.
            prefix = re.sub(r'[^A-Za-z0-9._-]', '', self.email.split('@')[0])
            if prefix:
                self.gitlab_username = prefix
        super(Student, self).save(*args, **kwargs)


class Assignment(models.Model):
    name = models.CharField(max_length=200, unique=True)
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='assignments')
    absolute_path = models.CharField(max_length=200, editable=False)
    owner = models.IntegerField(editable=False, default=1)
    date_created = models.DateTimeField('Date Created', default=timezone.now, editable=False)
    data_updated = models.DateTimeField('Date Updated', auto_now=True)
    active = models.BooleanField(default=True)
    simulate = models.BooleanField(default=False)
    git_source = models.CharField(max_length=255, default="")
    git_username = models.CharField(max_length=255, default="", blank=True)
    git_password = models.CharField(max_length=255, default="", blank=True,
                                    help_text="Use an access token and not your real password.")

    # Output mode: 'canvas' (submit to Canvas LMS) or 'gitlab_issues' (create GitLab issues)
    output_method = models.CharField(max_length=20, default='canvas',
                                      choices=[('canvas', 'Canvas LMS'),
                                               ('gitlab_issues', 'GitLab Issues')])
    # Numeric GitLab project ID for grade issues (required when output_method is gitlab_issues)
    gitlab_project_id = models.PositiveIntegerField(default=0,
                                                     help_text="Numeric GitLab project ID. Find it in your GitLab project's Settings > General.")

    # Group vs individual work. When set, students can be organised into groups
    # (see StudentGroup) and provisioning creates ONE repository per group
    # instead of one per student. Off by default: most assignments are individual.
    is_group_assignment = models.BooleanField(
        default=False,
        verbose_name='Group assignment',
        help_text="Students work in groups sharing one repository. Leave unchecked "
                  "for individual work, where every student gets their own repository.")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        keep_characters = (' ', '.', '_', '-')
        self.name = "".join(c for c in self.name if c.isalnum() or c in keep_characters).rstrip()
        super(Assignment, self).save(*args, **kwargs)


class AssignmentRepo(models.Model):
    """The Git repository a student uses for ONE assignment.

    Repositories are per-assignment, not per-course: "SQL 1" and "SQL 2" each
    get their own repo named ``<assignment>-<gitlab_username>``. This model is
    the authoritative source for that pairing and mirrors one row of the grading
    engine's ``users`` table (keyed by course_id + assignment_id + student).

    ``notified_at`` is tracked per assignment too, so provisioning "SQL 2" does
    not believe the student has already been emailed because "SQL 1" was.
    """
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='repos')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='assignment_repos')
    repository_url = models.CharField(max_length=500, blank=True, default="")
    notified_at = models.DateTimeField('Notified At', null=True, blank=True, editable=False)
    date_created = models.DateTimeField('Date Created', default=timezone.now, editable=False)

    class Meta:
        # One repository per student per assignment.
        unique_together = ('assignment', 'student')
        ordering = ['student__email']

    def __str__(self):
        return "%s → %s" % (self.student.email, self.assignment.name)


class StudentGroup(models.Model):
    """A set of students who share one repository on one assignment.

    The grading engine has no explicit group concept: it infers a group from
    students whose ``repository_url`` is identical (see
    ``Tester._get_group_user_list``), gated by ``same_url_limit``. This model is
    the source of truth the web app uses to *create* that situation — group
    members are provisioned one shared repository, so every member ends up with
    the same URL and the engine treats them as a group.

    Groups are per assignment, matching how repositories work.
    """
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=200)
    # Stable number used to build the repository name ("<assignment>-g<N>").
    # Stored rather than derived from position, so deleting group 2 does not
    # renumber group 3 and point it at a different repository.
    repo_index = models.PositiveIntegerField(default=0)
    date_created = models.DateTimeField('Date Created', default=timezone.now, editable=False)

    class Meta:
        ordering = ['repo_index', 'name']
        unique_together = ('assignment', 'name')

    def __str__(self):
        return "%s / %s" % (self.assignment.name, self.name)

    @property
    def slug(self):
        """A GitLab-safe name fragment, e.g. 'Team Rocket' -> 'team-rocket'."""
        slug = re.sub(r'[^a-z0-9]+', '-', (self.name or '').lower()).strip('-')
        return re.sub(r'-+', '-', slug)

    def next_repo_index(self):
        """The next unused repo index within this group's assignment."""
        used = StudentGroup.objects.filter(assignment=self.assignment).aggregate(
            models.Max('repo_index'))['repo_index__max']
        return (used or 0) + 1


class GroupMembership(models.Model):
    """Which students belong to a StudentGroup.

    A student may appear in at most one group per assignment — a second
    membership would mean two repositories for the same assignment, which the
    grading engine cannot express. That is enforced by
    ``UniqueConstraint(assignment)`` below rather than only in the views, so a
    concurrent drag-and-drop cannot create two memberships at once.
    """
    group = models.ForeignKey(StudentGroup, on_delete=models.CASCADE, related_name='memberships')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='group_memberships')
    # Denormalised from group.assignment so the uniqueness constraint below can
    # be expressed in the database. Kept in sync by save().
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE,
                                   related_name='group_memberships', null=True)
    date_added = models.DateTimeField('Date Added', default=timezone.now, editable=False)

    class Meta:
        unique_together = ('group', 'student')
        ordering = ['student__email']
        constraints = [
            # One group per student per assignment.
            models.UniqueConstraint(fields=('assignment', 'student'),
                                    name='one_group_per_student_per_assignment'),
        ]

    def save(self, *args, **kwargs):
        if self.assignment_id is None and self.group_id is not None:
            self.assignment_id = self.group.assignment_id
        super().save(*args, **kwargs)

    def __str__(self):
        return "%s in %s" % (self.student.email, self.group)
