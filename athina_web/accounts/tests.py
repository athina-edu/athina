from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from unittest import mock
from datetime import datetime, timedelta, timezone as dt_timezone

from athina_web.accounts.forms import TACreateForm, FacultyCreateForm
from athina_web.accounts.models import UserProfile
from athina_web.accounts import ta_email


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


class TestEmailTemplateComposition(TestCase):
    """The stored template is user input fed to str.format — it must never raise."""

    def test_default_template_fills_every_placeholder(self):
        context = ta_email.sample_context()
        subject, body = ta_email.compose_ta_email(
            subject_template=ta_email.DEFAULT_SUBJECT,
            body_template=ta_email.DEFAULT_BODY,
            context=context)
        # Every placeholder the default body actually uses is substituted.
        for token in ('name', 'username', 'password', 'site_url', 'course_list',
                      'faculty_name'):
            self.assertIn(context[token], body, "%s was not substituted" % token)
        self.assertNotIn('{', body, "no unsubstituted placeholders should remain")
        # The default subject names the courses rather than the person.
        self.assertIn(context['course_list'], subject)

    def test_unknown_placeholder_is_left_alone(self):
        """A typo must not abort the send; it should survive verbatim."""
        _subject, body = ta_email.compose_ta_email(
            subject_template='Hi', body_template='Hello {nmae} and {name}',
            context=ta_email.sample_context())
        self.assertIn('{nmae}', body)
        self.assertIn('Alex Rivera', body)

    def test_stray_brace_does_not_raise(self):
        """Prose like 'use {curly braces}' would break str.format."""
        _subject, body = ta_email.compose_ta_email(
            subject_template='Hi', body_template='Notes: { not a placeholder } ok {username}',
            context=ta_email.sample_context())
        self.assertIn('arivera', body)

    def test_empty_template_falls_back_to_the_default(self):
        subject, body = ta_email.compose_ta_email(
            subject_template='', body_template='', context=ta_email.sample_context())
        self.assertTrue(subject)
        self.assertIn('Alex Rivera', body)

    def test_subject_is_collapsed_to_one_line(self):
        """A newline in the subject would produce an invalid email header."""
        subject, _body = ta_email.compose_ta_email(
            subject_template='Hello\n{name}\r\nWelcome', body_template='x',
            context=ta_email.sample_context())
        self.assertNotIn('\n', subject)
        self.assertNotIn('\r', subject)

    def test_missing_password_is_replaced_by_a_visible_notice(self):
        profile = UserProfile(temp_password='')
        user = User(username='arivera', email='a@uni.edu')
        context = ta_email.build_credentials_context(
            user=user, profile=profile, site_url='https://x', faculty_name='Prof')
        self.assertEqual(context['password'], ta_email.MISSING_PASSWORD_NOTICE)
        self.assertIn('PASSWORD NOT AVAILABLE', context['password'])

    def test_composition_never_raises_on_hostile_input(self):
        for template in ('{', '}', '{0}', '{name!r:>10}', '{unknown}', '{{literal}}'):
            ta_email.compose_ta_email(
                subject_template=template, body_template=template,
                context=ta_email.sample_context())


class TestTempPasswordFreshness(TestCase):
    """A temp password is only sendable for a short window."""

    def setUp(self):
        self.user = User.objects.create_user(username='ta1', password='x')
        self.profile = self.user.profile

    def test_no_password_is_not_fresh(self):
        self.assertFalse(self.profile.temp_password_is_fresh())

    def test_recent_password_is_fresh(self):
        self.profile.temp_password = 'abc'
        self.profile.temp_password_created_at = timezone.now()
        self.assertTrue(self.profile.temp_password_is_fresh())

    def test_old_password_is_not_fresh(self):
        self.profile.temp_password = 'abc'
        self.profile.temp_password_created_at = timezone.now() - timedelta(hours=25)
        self.assertFalse(self.profile.temp_password_is_fresh())


@override_settings(SECURE_SSL_REDIRECT=False)
class TestCreateUserRequiresPasswordChange(TestCase):
    """An account created by an admin starts with a password the user must change."""

    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='x')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.client.force_login(self.admin)

    def test_new_ta_must_change_password_and_has_a_sendable_one(self):
        response = self.client.post(reverse('accounts:create_user'), {
            'username': 'ta1', 'email': 'ta1@uni.edu',
            'gitlab_username': 'ta1', 'role': 'ta'})

        self.assertEqual(response.status_code, 200)
        ta = User.objects.get(username='ta1')
        profile = ta.profile
        self.assertTrue(profile.must_change_password)
        self.assertTrue(profile.temp_password_is_fresh())
        # The plaintext matches what was shown to the admin.
        shown = response.context['password']
        self.assertEqual(profile.temp_password, shown)
        self.assertTrue(ta.check_password(shown))


@override_settings(SECURE_SSL_REDIRECT=False)
class TestForcedPasswordChange(TestCase):
    """Until the password is changed, the site is off limits."""

    def setUp(self):
        self.user = User.objects.create_user(username='ta1', password='temp-pass-123')
        self.user.profile.role = UserProfile.ROLE_TA
        self.user.profile.must_change_password = True
        self.user.profile.temp_password = 'temp-pass-123'
        self.user.profile.temp_password_created_at = timezone.now()
        self.user.profile.save()
        self.client.force_login(self.user)

    def test_other_pages_redirect_to_the_change_page(self):
        response = self.client.get(reverse('accounts:profile'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:password_change_required'), response['Location'])

    def test_the_change_page_itself_is_reachable(self):
        response = self.client.get(reverse('accounts:password_change_required'))
        self.assertEqual(response.status_code, 200)

    def test_home_redirects_until_changed(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)

    def test_superuser_is_never_locked_out(self):
        """A superuser locked out could not repair their own account."""
        su = User.objects.create_superuser(username='root', password='x')
        su.profile.must_change_password = True
        su.profile.save()
        self.client.force_login(su)
        self.assertEqual(self.client.get(reverse('accounts:profile')).status_code, 200)

    def test_short_password_is_rejected(self):
        response = self.client.post(reverse('accounts:password_change_required'),
                                    {'new_password': 'short', 'confirm_password': 'short'})
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.must_change_password)

    def test_mismatched_confirmation_is_rejected(self):
        self.client.post(reverse('accounts:password_change_required'),
                         {'new_password': 'a-good-password', 'confirm_password': 'different'})
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.must_change_password)

    def test_reusing_the_temp_password_is_rejected(self):
        self.client.post(reverse('accounts:password_change_required'),
                         {'new_password': 'temp-pass-123', 'confirm_password': 'temp-pass-123'})
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.must_change_password)

    def test_successful_change_clears_the_flag_and_the_temp_password(self):
        response = self.client.post(reverse('accounts:password_change_required'),
                                    {'new_password': 'a-good-password',
                                     'confirm_password': 'a-good-password'})

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        profile = self.user.profile
        self.assertFalse(profile.must_change_password)
        self.assertEqual(profile.temp_password, '')
        self.assertIsNone(profile.temp_password_created_at)
        self.assertTrue(self.user.check_password('a-good-password'))

    def test_access_is_granted_after_the_change(self):
        self.client.post(reverse('accounts:password_change_required'),
                         {'new_password': 'a-good-password', 'confirm_password': 'a-good-password'})
        self.assertEqual(self.client.get(reverse('accounts:profile')).status_code, 200)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestTAInvitePage(TestCase):
    """Compose page: preview, per-TA values, and sending."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.notification_api_key = 're_test'
        self.faculty.profile.notification_from_email = 'Prof <prof@uni.edu>'
        self.faculty.profile.save()

        self.ta = User.objects.create_user(username='ta1', email='ta1@uni.edu', password='x')
        self.ta.profile.role = UserProfile.ROLE_TA
        self.ta.profile.gitlab_username = 'ta1'
        self.ta.profile.managed_by.add(self.faculty)
        self.ta.profile.temp_password = 'Temp!2345'
        self.ta.profile.temp_password_created_at = timezone.now()
        self.ta.profile.must_change_password = True
        self.ta.profile.save()

        self.client.force_login(self.faculty)

    def test_page_renders_with_preview_and_placeholders(self):
        response = self.client.get(reverse('accounts:ta_invite'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('{password}', response.content.decode())
        # Preview uses example values, not the real TA's password.
        self.assertIn('Alex Rivera', response.context['preview_body'])

    def test_restore_buttons_receive_valid_json(self):
        """Regression: `escapejs` escapes characters but does NOT add quotes.

        Interpolating it into a JS string context produced
        `subject.value = Hello {name};` — a syntax error that silently killed the
        "Restore" buttons. The values are now handed over via json_script.
        """
        import json
        import re

        response = self.client.get(reverse('accounts:ta_invite'))
        html = response.content.decode()

        match = re.search(r'<script id="email-defaults"[^>]*>(.*?)</script>', html, re.S)
        self.assertIsNotNone(match, "json_script payload missing")
        payload = json.loads(match.group(1))          # raises if not valid JSON

        for key in ('default_subject', 'default_body', 'saved_subject', 'saved_body'):
            self.assertIn(key, payload)
        self.assertTrue(payload['default_body'])

        # The old, broken form must not appear.
        self.assertNotIn('subject.value = Your Athina', html)
        self.assertNotIn('body.value = Hello', html)

    def test_per_ta_page_previews_that_ta(self):
        response = self.client.get(
            reverse('accounts:ta_invite_one', kwargs={'user_id': self.ta.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['preview_name'], 'ta1')
        self.assertIn('ta1', response.context['preview_body'])

    def test_site_url_in_preview_is_absolute(self):
        response = self.client.get(reverse('accounts:ta_invite'))
        self.assertIn('http://testserver', response.context['preview_body'])

    def test_sending_uses_the_edited_message(self):
        captured = {}

        def fake_send(**kwargs):
            captured.update(kwargs)
            return 'msg-id'

        with mock.patch('athina_web.accounts.views.send_email_safe', fake_send):
            self.client.post(reverse('accounts:ta_invite'), {
                'subject': 'Welcome {name}',
                'body': 'Login at {site_url} as {username} with {password}'})

        self.assertEqual(captured['subject'], 'Welcome ta1')
        self.assertIn('ta1@uni.edu', captured['to'])
        self.assertIn('Temp!2345', captured['text'])
        self.assertIn('http://testserver', captured['text'])

    def test_send_records_a_success_message(self):
        with mock.patch('athina_web.accounts.views.send_email_safe', lambda **kw: 'id'):
            response = self.client.post(reverse('accounts:ta_invite'),
                                        {'subject': 's', 'body': 'b'}, follow=True)
        self.assertContains(response, 'Sent credentials to 1 TA')

    def test_ta_without_a_password_gets_one_generated_on_send(self):
        """Django cannot re-derive a password from its hash.

        Rather than make the faculty member click once per TA, sending issues a
        fresh password so the email can actually carry credentials.
        """
        self.ta.profile.temp_password = ''
        self.ta.profile.save()

        sent = []
        with mock.patch('athina_web.accounts.views.send_email_safe',
                        lambda **kw: sent.append(kw) or 'id'):
            response = self.client.post(reverse('accounts:ta_invite'),
                                        {'subject': 's', 'body': 'password: {password}'},
                                        follow=True)

        self.assertEqual(len(sent), 1, "the email must still go out")
        self.ta.profile.refresh_from_db()
        self.ta.refresh_from_db()
        # A real password is now stored and substituted into the message.
        self.assertTrue(self.ta.profile.temp_password)
        self.assertIn(self.ta.profile.temp_password, sent[0]['text'])
        self.assertNotIn('PASSWORD NOT AVAILABLE', sent[0]['text'])
        self.assertTrue(self.ta.profile.must_change_password)
        self.assertTrue(self.ta.check_password(self.ta.profile.temp_password))
        self.assertContains(response, 'New temporary passwords were generated')

    def test_expired_password_is_regenerated_on_send(self):
        old = self.ta.profile.temp_password
        self.ta.profile.temp_password_created_at = timezone.now() - timedelta(hours=48)
        self.ta.profile.save()

        sent = []
        with mock.patch('athina_web.accounts.views.send_email_safe',
                        lambda **kw: sent.append(kw) or 'id'):
            self.client.post(reverse('accounts:ta_invite'), {'subject': 's', 'body': 'b'})

        self.ta.profile.refresh_from_db()
        self.assertNotEqual(self.ta.profile.temp_password, old)

    def test_flash_messages_are_not_double_escaped(self):
        """Regression: a Python string containing '&amp;' gets escaped again.

        The message then renders the literal text '&amp;' to the user, which is
        how 'Reset &amp; send' leaked into the UI. Django escaping a correct
        '&' is fine; double-escaping is not.
        """
        self.ta.profile.temp_password = ''
        self.ta.profile.save()

        with mock.patch('athina_web.accounts.views.send_email_safe', lambda **kw: 'id'):
            response = self.client.post(reverse('accounts:ta_invite'),
                                        {'subject': 's', 'body': 'b'}, follow=True)

        html = response.content.decode()
        self.assertNotIn('&amp;amp;', html, "message was double-escaped")
        self.assertNotIn('&amp; send', html, "entity leaked into a message")

    def test_generate_missing_issues_passwords_for_all(self):
        second = User.objects.create_user(username='ta2', email='t2@uni.edu', password='x')
        second.profile.role = UserProfile.ROLE_TA
        second.profile.managed_by.add(self.faculty)
        second.profile.save()

        # Neither TA has a sendable password yet.
        for user in (self.ta, second):
            user.profile.temp_password = ''
            user.profile.save()

        response = self.client.post(reverse('accounts:ta_invite_generate'), follow=True)

        for user in (self.ta, second):
            user.refresh_from_db()
            user.profile.refresh_from_db()
            self.assertTrue(user.profile.temp_password_is_fresh())
            self.assertTrue(user.profile.must_change_password)
            self.assertTrue(user.check_password(user.profile.temp_password))
        self.assertContains(response, 'Generated new temporary passwords for 2 TA')

    def test_generate_missing_leaves_valid_passwords_alone(self):
        existing = self.ta.profile.temp_password
        response = self.client.post(reverse('accounts:ta_invite_generate'), follow=True)

        self.ta.profile.refresh_from_db()
        self.assertEqual(self.ta.profile.temp_password, existing)
        self.assertContains(response, 'already has a valid temporary password')

    def test_generate_missing_requires_post(self):
        response = self.client.get(reverse('accounts:ta_invite_generate'))
        self.assertEqual(response.status_code, 302)   # redirected, not applied

    def test_ta_with_no_email_address_is_reported(self):
        self.ta.email = ''
        self.ta.save(update_fields=['email'])

        with mock.patch('athina_web.accounts.views.send_email_safe', lambda **kw: 'id'):
            response = self.client.post(reverse('accounts:ta_invite'),
                                        {'subject': 's', 'body': 'b'}, follow=True)

        self.assertContains(response, 'no email address')

    def test_delivery_failure_is_reported(self):
        with mock.patch('athina_web.accounts.views.send_email_safe', lambda **kw: None):
            response = self.client.post(reverse('accounts:ta_invite'),
                                        {'subject': 's', 'body': 'b'}, follow=True)
        self.assertContains(response, 'Could not email')

    def test_saving_as_default_persists_the_template(self):
        self.client.post(reverse('accounts:ta_invite'), {
            'subject': 'My subject', 'body': 'My body', 'save_as_default': 'on'})

        self.faculty.profile.refresh_from_db()
        self.assertEqual(self.faculty.profile.welcome_email_subject, 'My subject')
        self.assertEqual(self.faculty.profile.welcome_email_body, 'My body')

    def test_saved_template_is_prefilled_and_used_on_the_next_visit(self):
        self.faculty.profile.welcome_email_subject = 'Saved {name}'
        self.faculty.profile.welcome_email_body = 'Saved body for {username}'
        self.faculty.profile.save()

        response = self.client.get(reverse('accounts:ta_invite'))
        self.assertEqual(response.context['subject_template'], 'Saved {name}')
        self.assertIn('Saved body for', response.context['preview_body'])

    def test_ta_cannot_reach_the_compose_page(self):
        ta = User.objects.create_user(username='ta9', password='x')
        ta.profile.role = UserProfile.ROLE_TA
        ta.profile.save()
        self.client.force_login(ta)
        self.assertEqual(self.client.get(reverse('accounts:ta_invite')).status_code, 404)

    def test_faculty_cannot_email_someone_elses_ta(self):
        other = User.objects.create_user(username='prof2', password='y')
        other.profile.role = UserProfile.ROLE_FACULTY
        other.profile.save()
        stranger = User.objects.create_user(username='ta2', email='t2@uni.edu', password='x')
        stranger.profile.role = UserProfile.ROLE_TA
        stranger.profile.managed_by.add(other)
        stranger.profile.save()

        response = self.client.get(
            reverse('accounts:ta_invite_one', kwargs={'user_id': stranger.pk}))
        self.assertEqual(response.status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestTAInviteReset(TestCase):
    """Reset & send: generate a password, store it, then compose."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.save()
        self.ta = User.objects.create_user(username='ta1', email='ta1@uni.edu', password='old')
        self.ta.profile.role = UserProfile.ROLE_TA
        self.ta.profile.managed_by.add(self.faculty)
        self.ta.profile.save()
        self.client.force_login(self.faculty)

    def test_reset_stores_a_fresh_password_and_redirects_to_compose(self):
        response = self.client.post(
            reverse('accounts:ta_invite_reset', kwargs={'user_id': self.ta.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:ta_invite_one', kwargs={'user_id': self.ta.pk}),
                      response['Location'])
        self.ta.refresh_from_db()          # the in-memory hash is now stale
        self.ta.profile.refresh_from_db()
        profile = self.ta.profile
        self.assertTrue(profile.temp_password_is_fresh())
        self.assertTrue(profile.must_change_password)
        self.assertTrue(self.ta.check_password(profile.temp_password))

    def test_reset_invalidates_the_previous_password(self):
        self.client.post(reverse('accounts:ta_invite_reset', kwargs={'user_id': self.ta.pk}))
        self.ta.refresh_from_db()
        self.assertFalse(self.ta.check_password('old'))

    def test_faculty_cannot_reset_another_faculty_ta(self):
        other = User.objects.create_user(username='prof2', password='y')
        other.profile.role = UserProfile.ROLE_FACULTY
        other.profile.save()
        stranger = User.objects.create_user(username='ta2', password='x')
        stranger.profile.role = UserProfile.ROLE_TA
        stranger.profile.managed_by.add(other)
        stranger.profile.save()

        response = self.client.post(
            reverse('accounts:ta_invite_reset', kwargs={'user_id': stranger.pk}))
        self.assertEqual(response.status_code, 404)


class TestAsUtcSentinel(TestCase):
    """The grader's year-1 "never" sentinel must not reach timezone conversion.

    ``astimezone()`` on ``0001-01-01`` raises ``OverflowError`` for any zone
    west of UTC, because year 0 is not representable. The assignment page used
    ``{{ user.14|as_utc|date:"..." }}`` on ``commit_date``, so every ungraded
    student row (sentinel value) produced a 500 for users in a US timezone.
    """

    def test_sentinel_is_treated_as_missing(self):
        from athina_web.accounts.templatetags.timezone_tags import as_utc
        self.assertIsNone(as_utc(datetime(1, 1, 1, 0, 0, 0)))

    def test_real_datetime_is_returned_utc_aware(self):
        from athina_web.accounts.templatetags.timezone_tags import as_utc
        value = as_utc(datetime(2026, 10, 20, 18, 40, 0))
        self.assertEqual(value.tzinfo, dt_timezone.utc)

    def test_filter_output_renders_in_a_western_timezone(self):
        """The full template path: as_utc then the date filter, in US/Pacific."""
        from django.template import Context, Template
        with timezone.override('America/Los_Angeles'):
            rendered = Template(
                '{% load timezone_tags %}{% with v2=v|as_utc %}'
                '{% if v2 %}{{ v2|date:"Y-m-d" }}{% else %}never{% endif %}'
                '{% endwith %}').render(
                    Context({'v': datetime(1, 1, 1, 0, 0, 0)}))
        self.assertEqual(rendered.strip(), 'never')

    def test_real_value_still_converts_to_local_time(self):
        from django.template import Context, Template
        with timezone.override('America/Los_Angeles'):
            rendered = Template(
                '{% load timezone_tags %}{{ v|as_utc|date:"Y-m-d H:i" }}').render(
                    Context({'v': datetime(2026, 10, 20, 18, 40, 0)}))
        self.assertEqual(rendered.strip(), '2026-10-20 11:40')
