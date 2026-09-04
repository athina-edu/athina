from django import forms
from .models import Assignment, Course, Student


class AssignmentForm(forms.ModelForm):

    class Meta:
        model = Assignment
        fields = ('name', 'course', 'active', 'git_source', 'output_method', 'gitlab_project_id')
        widgets = {
            'git_source': forms.TextInput(attrs={
                'placeholder': 'https://gitlab.com/group/template-repo.git',
                'class': 'form-control',
            }),
            'gitlab_project_id': forms.NumberInput(attrs={
                'placeholder': 'e.g. 12345',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'output_method': 'Canvas LMS: grades submitted to Canvas. GitLab Issues: grades posted as GitLab issues.',
            'gitlab_project_id': 'Required for GitLab Issues mode. Find it in your project Settings > General.',
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
        fields = ('email',)
        help_texts = {
            'email': 'Student email address. Username is derived automatically from the email prefix.',
        }


class StudentEditForm(forms.ModelForm):
    """Extended form for editing an existing student — includes repository URL."""
    class Meta:
        model = Student
        fields = ('email', 'repository_url')
        widgets = {
            'repository_url': forms.TextInput(attrs={
                'placeholder': 'https://gitlab.com/group/student-repo.git',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'repository_url': 'Optional. Set the student\'s Git repository URL for grading.',
        }


class StudentBulkForm(forms.Form):
    """Bulk import students via a textarea of email addresses (one per line)."""
    emails = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 10, 'placeholder': 'one@email.com\nanother@email.com'}),
        label='Email addresses (one per line)',
        help_text='Paste student email addresses, one per line. '
                  'Usernames are derived from the part before @.',
    )
