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
    class Meta:
        model = Student
        fields = ('email', 'username', 'gitlab_username')
        widgets = {
            'username': forms.TextInput(attrs={
                'placeholder': 'e.g. alice',
                'class': 'form-control',
            }),
            'gitlab_username': forms.TextInput(attrs={
                'placeholder': 'e.g. alice',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'email': 'Student email address.',
            'username': 'Defaults to the part of the email before @. Used as the '
                        'student\'s username and for repository naming.',
            'gitlab_username': 'Optional. The student\'s GitLab account name, used '
                               'to grant them access to their private repository.',
        }


class StudentEditForm(forms.ModelForm):
    """Extended form for editing an existing student — includes GitLab username and repository URL."""
    class Meta:
        model = Student
        fields = ('email', 'username', 'gitlab_username', 'repository_url')
        widgets = {
            'username': forms.TextInput(attrs={
                'placeholder': 'e.g. alice',
                'class': 'form-control',
            }),
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
            'username': 'Used as the student\'s username and for repository naming.',
            'gitlab_username': 'The student\'s GitLab username. Required before provisioning a repo.',
            'repository_url': 'Optional. Set the student\'s Git repository URL for grading.',
        }


class StudentBulkForm(forms.Form):
    """Bulk import students via a textarea of email addresses (one per line)."""
    emails = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 10,
            'placeholder': 'one@email.com\nanother@email.com,gitlabuser\nthird@email.com,gitlabuser2,customuser',
        }),
        label='Email addresses (one per line)',
        help_text='Paste student email addresses, one per line. The part before @ is used '
                  'as the username. Optionally append the student\'s GitLab username after '
                  'a comma (e.g. "alice@uni.edu,alicegit"), and a third value to override '
                  'the username (e.g. "alice@uni.edu,alicegit,asmith22").',
    )
