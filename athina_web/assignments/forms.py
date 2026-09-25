from django import forms
from .models import Assignment, Course, Student, AssignmentRepo


class AssignmentForm(forms.ModelForm):

    class Meta:
        model = Assignment
        # output_method and gitlab_project_id are intentionally NOT exposed here.
        # They are grading-engine settings (defaults: 'canvas' / 0) that are
        # written to the per-assignment .env by _refresh_env_for_user. The
        # instructor only needs to supply the repository (git_source).
        fields = ('name', 'course', 'active', 'git_source')
        widgets = {
            'git_source': forms.TextInput(attrs={
                'placeholder': 'https://gitlab.com/group/template-repo.git',
                'class': 'form-control',
            }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            from .views import _get_visible_courses
            self.fields['course'].queryset = _get_visible_courses(user)
        self.fields['git_source'].required = True


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ('name',)


class StudentForm(forms.ModelForm):
    """Add a student.

    `username` is deliberately NOT exposed: it is derived from the email prefix
    (see Student.save()) and is only a display/repo-naming value. Having a
    second editable field next to the GitLab username was confusing — the
    autofilled one did nothing for provisioning while the GitLab username, the
    one that actually matters, silently blocked it.
    """
    def __init__(self, *args, **kwargs):
        self.course = kwargs.pop('course', None)
        super().__init__(*args, **kwargs)

    def clean_email(self):
        """Reject an email already enrolled in this course.

        (course, email) is unique, so saving a duplicate raised an
        IntegrityError and the page returned a 500 instead of a message.
        """
        email = (self.cleaned_data.get('email') or '').strip()
        if self.course and email:
            clash = Student.objects.filter(course=self.course, email__iexact=email).exists()
            if clash:
                raise forms.ValidationError(
                    "%s is already enrolled in this course." % email)
        return email

    class Meta:
        model = Student
        fields = ('email', 'gitlab_username')
        widgets = {
            'gitlab_username': forms.TextInput(attrs={
                'placeholder': 'e.g. alice',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'email': 'Student email address. The part before @ becomes their username '
                     'and their GitLab account name.',
            'gitlab_username': 'Defaults to the part of the email before @. Change it only '
                               'if the student\'s GitLab account differs. The account must '
                               'already exist on your GitLab server.',
        }


class StudentEditForm(forms.ModelForm):
    """Edit an existing student — course-wide identity (email, GitLab username)."""
    def clean_email(self):
        """Reject an email that collides with another student in the same course."""
        email = (self.cleaned_data.get('email') or '').strip()
        if email and self.instance and self.instance.course_id:
            clash = (Student.objects
                     .filter(course_id=self.instance.course_id, email__iexact=email)
                     .exclude(pk=self.instance.pk)
                     .exists())
            if clash:
                raise forms.ValidationError(
                    "%s is already enrolled in this course." % email)
        return email

    class Meta:
        model = Student
        # `repository_url` is deliberately NOT here: a repository belongs to one
        # assignment, and this form edits course-wide student identity. Set a
        # repo per assignment from the assignment's Repositories page instead.
        fields = ('email', 'gitlab_username')
        widgets = {
            'gitlab_username': forms.TextInput(attrs={
                'placeholder': 'e.g. alice',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'gitlab_username': 'Defaults to the part of the email before @. Change it only '
                               'if the student\'s GitLab account differs — the account must '
                               'already exist on your GitLab server.',
        }


class AssignmentRepoForm(forms.ModelForm):
    """Set the repository URL for one student on one assignment.

    This is the manual override for cases where provisioning should be skipped:
    the student already has a repo, or it lives outside the managed GitLab group.
    """
    class Meta:
        model = AssignmentRepo
        fields = ('repository_url',)
        widgets = {
            'repository_url': forms.TextInput(attrs={
                'placeholder': 'https://gitlab.com/group/student-repo.git',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'repository_url': 'Optional. The Git repository the student uses for this '
                              'assignment. Leave blank to let provisioning create one.',
        }


class StudentBulkForm(forms.Form):
    """Bulk import students via a textarea of email addresses (one per line)."""
    emails = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 10,
            'placeholder': 'one@email.com\nanother@email.com,gitlabuser',
        }),
        label='Email addresses (one per line)',
        help_text='Paste student email addresses, one per line. The part before @ becomes '
                  'the username and the expected GitLab account name. If a student\'s '
                  'GitLab account differs, add it after a comma '
                  '(e.g. "alice@uni.edu,alicegit").',
    )
