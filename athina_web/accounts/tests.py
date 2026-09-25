from django.test import TestCase, override_settings
from django.contrib.auth.models import User

from athina_web.accounts.forms import TACreateForm, FacultyCreateForm
from athina_web.accounts.models import UserProfile


class TestUserCreateForms(TestCase):
    """GitLab username defaults to the email prefix when left blank."""

    def test_ta_form_defaults_gitlab_username_to_email_prefix(self):
        form = TACreateForm(data={'username': 'ta1', 'email': 'jsmith@uni.edu',
                                  'gitlab_username': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['gitlab_username'], 'jsmith')

    def test_ta_form_keeps_explicit_gitlab_username(self):
        form = TACreateForm(data={'username': 'ta1', 'email': 'jsmith@uni.edu',
                                  'gitlab_username': 'jsmith-gitlab'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['gitlab_username'], 'jsmith-gitlab')

    def test_faculty_form_defaults_gitlab_username_to_email_prefix(self):
        form = FacultyCreateForm(data={'username': 'prof', 'email': 'prof@uni.edu',
                                       'gitlab_username': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['gitlab_username'], 'prof')

    def test_duplicate_username_rejected(self):
        User.objects.create_user(username='taken', password='x')
        form = TACreateForm(data={'username': 'taken', 'email': 'a@uni.edu',
                                  'gitlab_username': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestEditUserView(TestCase):
    """Faculty can set/correct a TA's GitLab username after creation."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.save()

        self.ta = User.objects.create_user(username='ta1', email='jsmith@uni.edu', password='x')
        self.ta.profile.role = UserProfile.ROLE_TA
        self.ta.profile.managed_by.add(self.faculty)  # canonical direction
        self.ta.profile.save()

        self.client.force_login(self.faculty)

    def test_faculty_can_set_gitlab_username(self):
        resp = self.client.post('/accounts/users/%d/edit/' % self.ta.pk,
                                {'gitlab_username': 'jsmith-gitlab'})
        self.assertEqual(resp.status_code, 302)
        self.ta.profile.refresh_from_db()
        self.assertEqual(self.ta.profile.gitlab_username, 'jsmith-gitlab')

    def test_blank_falls_back_to_email_prefix(self):
        self.client.post('/accounts/users/%d/edit/' % self.ta.pk, {'gitlab_username': ''})
        self.ta.profile.refresh_from_db()
        self.assertEqual(self.ta.profile.gitlab_username, 'jsmith')

    def test_faculty_cannot_edit_unrelated_ta(self):
        other = User.objects.create_user(username='ta2', email='other@uni.edu', password='x')
        other.profile.role = UserProfile.ROLE_TA
        other.profile.gitlab_username = ''
        other.profile.save()
        resp = self.client.post('/accounts/users/%d/edit/' % other.pk,
                                {'gitlab_username': 'hacked'})
        self.assertEqual(resp.status_code, 404)
        other.profile.refresh_from_db()
        self.assertEqual(other.profile.gitlab_username, '')
