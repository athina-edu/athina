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
    repository_url = models.CharField(max_length=500, blank=True, default="")
    date_added = models.DateTimeField('Date Added', default=timezone.now, editable=False)

    class Meta:
        unique_together = ('course', 'email')
        ordering = ['email']

    def __str__(self):
        return "%s (%s)" % (self.email, self.course.name)

    def save(self, *args, **kwargs):
        if not self.username and self.email:
            self.username = self.email.split('@')[0]
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

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        keep_characters = (' ', '.', '_', '-')
        self.name = "".join(c for c in self.name if c.isalnum() or c in keep_characters).rstrip()
        super(Assignment, self).save(*args, **kwargs)
