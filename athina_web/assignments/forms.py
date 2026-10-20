from django import forms
from .models import Assignment, Course, Student, StudentGroup, GroupMembership, AssignmentRepo


class AssignmentForm(forms.ModelForm):

    # Not a model field: the grading engine keeps this in its own database
    # (assignmentdata.due_date, UTC). It is rendered here so the instructor can set
    # it per assignment; the view converts the entered local time to UTC and stores
    # it. 'required=False' means leaving it blank clears enforcement.
    due_date = forms.DateTimeField(
        required=False,
        label='Due date',
        help_text='Deadline for accepting commits. Interpreted in your display '
                  'timezone (see your profile). Leave blank for no deadline.',
        widget=forms.DateTimeInput(attrs={
            'type': 'datetime-local',
            'class': 'form-control',
        }, format='%Y-%m-%dT%H:%M'),
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S',
                       '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'],
    )

    class Meta:
        model = Assignment
        # output_method and gitlab_project_id are intentionally NOT exposed here.
        # They are grading-engine settings (defaults: 'canvas' / 0) that are
        # written to the per-assignment .env by _refresh_env_for_user. The
        # instructor only needs to supply the repository (git_source).
        fields = ('name', 'course', 'active', 'is_group_assignment', 'git_source')
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
        self.fields['is_group_assignment'].label = 'Group assignment'
        self.fields['is_group_assignment'].help_text = (
            'Students work in groups that share one repository. Leave unchecked for '
            'individual work, where every student gets their own repository.')


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


class StudentGroupForm(forms.ModelForm):
    """Create or rename a group. Members are managed separately."""

    def __init__(self, *args, **kwargs):
        self.assignment = kwargs.pop('assignment', None)
        super().__init__(*args, **kwargs)

    def clean_name(self):
        """Reject a name already used on this assignment.

        (assignment, name) is unique, so a duplicate raised an IntegrityError and
        the page returned a 500 instead of a message.
        """
        name = (self.cleaned_data.get('name') or '').strip()
        assignment = self.assignment or getattr(self.instance, 'assignment', None)
        if assignment and name:
            clash = (StudentGroup.objects
                     .filter(assignment=assignment, name__iexact=name)
                     .exclude(pk=self.instance.pk)
                     .exists())
            if clash:
                raise forms.ValidationError(
                    "%s is already a group on this assignment." % name)
        return name

    class Meta:
        model = StudentGroup
        fields = ('name',)
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Team Rocket',
                'class': 'form-control',
            }),
        }
        help_texts = {
            'name': 'A label for your own reference. The repository is named after the '
                    'assignment and the group number, not this name.',
        }


class GroupMembersForm(forms.Form):
    """Pick which students belong to a group.

    Rendered as checkboxes. Students already in another group on this assignment
    are shown separately (and excluded here) because a student can only hold one
    repository per assignment.
    """
    students = forms.ModelMultipleChoiceField(
        queryset=Student.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Members',
    )

    def __init__(self, *args, **kwargs):
        self.group = kwargs.pop('group', None)
        super().__init__(*args, **kwargs)
        if self.group is None:
            return
        # Eligible: everyone on the course roster, minus students already grouped
        # elsewhere on this assignment.
        taken = (GroupMembership.objects
                 .filter(group__assignment=self.group.assignment)
                 .exclude(group=self.group)
                 .values_list('student_id', flat=True))
        self.fields['students'].queryset = (
            Student.objects.filter(course=self.group.assignment.course)
            .exclude(pk__in=list(taken))
            .order_by('email'))
        self.fields['students'].initial = list(
            self.group.memberships.values_list('student_id', flat=True))
