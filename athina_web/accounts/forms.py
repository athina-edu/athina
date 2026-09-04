from django import forms
from django.contrib.auth.models import User
from .models import UserProfile


class FacultyCreateForm(forms.Form):
    """Admin form to create a Faculty user."""
    username = forms.CharField(max_length=150, label='Username',
                               help_text='Letters, digits, and @/./+/-/_ only.')
    email = forms.EmailField(label='Email')

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("A user with that username already exists.")
        return username


class TACreateForm(forms.Form):
    """Faculty form to create a TA user."""
    username = forms.CharField(max_length=150, label='Username',
                               help_text='Letters, digits, and @/./+/-/_ only.')
    email = forms.EmailField(label='Email')

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("A user with that username already exists.")
        return username


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
            self.fields['tas'].initial = list(
                UserProfile.objects.get(user=faculty_user)
                .managed_by.values_list('id', flat=True)
            )
