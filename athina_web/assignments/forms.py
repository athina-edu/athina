from django import forms
from .models import Assignment, Course, Student


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
            'email': 'Student email address. The part before @ becomes their username.',
            'gitlab_username': 'The student\'s GitLab account name, which must already '
                               'exist on your GitLab server. Used to give them access to '
                               'their private repository, and required before provisioning.',
        }


class StudentEditForm(forms.ModelForm):
    """Edit an existing student — email, GitLab username and repository URL."""
    class Meta:
        model = Student
        fields = ('email', 'gitlab_username', 'repository_url')
        widgets = {
            'gitlab_username': forms.TextInput(attrs={
                'placeholder': 'e.g. alice',
                'class': 'form-control',
            }),
            'repository_url': forms.TextInput(attrs={
                'placeholder': 'https://gitlab.com/group/student-repo.git',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'gitlab_username': 'The student\'s GitLab account name. Required before '
                               'provisioning a repo, and must exist on your GitLab server.',
            'repository_url': 'Optional. Set the student\'s Git repository URL for grading.',
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
