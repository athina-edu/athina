from django import forms
from django.contrib.auth.models import User
from .models import UserProfile


class BaseUserCreateForm(forms.Form):
    """Shared fields/validation for creating Faculty and TA accounts.

    The GitLab username is optional: if left blank it defaults to the email
    prefix (the part before '@'), which is the common convention for university
    GitLab accounts. It is needed to add the user to course groups on GitLab.
    """
    username = forms.CharField(max_length=150, label='Username',
                               help_text='Letters, digits, and @/./+/-/_ only.')
    email = forms.EmailField(label='Email')
    gitlab_username = forms.CharField(
        max_length=255, required=False, label='GitLab Username',
        help_text='Leave blank to use the email prefix (e.g. jsmith from jsmith@uni.edu).')

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("A user with that username already exists.")
        return username

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('gitlab_username'):
            email = cleaned.get('email', '')
            if email:
                cleaned['gitlab_username'] = email.split('@')[0]
        return cleaned


class FacultyCreateForm(BaseUserCreateForm):
    """Admin form to create a Faculty user."""


class TACreateForm(BaseUserCreateForm):
    """Faculty form to create a TA user."""


class TAAssignForm(forms.Form):
    """Form to assign TAs to a faculty member."""
    tas = forms.MultipleChoiceField(choices=[], widget=forms.CheckboxSelectMultiple,
                                     label='Select TAs to assign', required=False)

    def __init__(self, faculty_user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if faculty_user:
            ta_profiles = UserProfile.objects.filter(role=UserProfile.ROLE_TA)
            self.fields['tas'].choices = [
                (tp.user_id, "%s (%s)" % (tp.user.username, tp.user.email))
                for tp in ta_profiles
            ]
            # TAs whose managed_by includes this faculty user
            self.fields['tas'].initial = list(
                UserProfile.objects.filter(
                    role=UserProfile.ROLE_TA,
                    managed_by=faculty_user
                ).values_list('user__id', flat=True)
            )
