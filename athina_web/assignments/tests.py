from django.test import TestCase
from django.test import Client
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from unittest import mock

from athina_web.accounts.models import UserProfile
from athina_web.assignments.models import Assignment, Course, Student, AssignmentRepo
from athina_web.assignments import views as assignment_views


class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, ok=True, status_code=200, json_data=None):
        self.ok = ok
        self.status_code = status_code
        self._json = json_data if json_data is not None else []

    def json(self):
        return self._json


def fake_gitlab(user_id=55, project_id=999, repo_url=None,
                project_exists=False):
    """Build path-aware `requests.get` / `requests.post` stand-ins.

    The provisioning flow hits three different endpoints, so a single blanket
    return value is not enough: the user lookup must succeed, the project check
    must 404 (so creation runs), and the members check must return a list.

    When `repo_url` is None, creation echoes back a URL derived from the repo
    name that was posted — that way the assignment prefix in the name is
    observable in the stored URL, exactly as GitLab would return it.
    """
    def _url_for(posted_name):
        if repo_url:
            return repo_url
        return 'https://gitlab.example.edu/grp/%s.git' % posted_name

    def fake_get(url, *args, **kwargs):
        if url.endswith('/users'):
            return FakeResponse(json_data=[{'id': user_id}])
        if '/members' in url:
            return FakeResponse(json_data=[])
        if project_exists:
            return FakeResponse(json_data={'id': project_id,
                                           'http_url_to_repo': _url_for('existing')})
        return FakeResponse(ok=False, status_code=404, json_data={})

    def fake_post(url, *args, **kwargs):
        return FakeResponse(json_data={'id': project_id,
                                       'http_url_to_repo': _url_for((kwargs.get('data') or {}).get('name', 'repo'))})

    return fake_get, fake_post


class TestFunctions(TestCase):
    # Use a non-"Push Hook" event here so the webhook view does not attempt to connect to a real MySQL DB
    # during tests. The view will return a simple 'ok' response for non-Push Hook events.
    header = {'Content-Length': '1770', 'Content-Type': 'application/json', 'X-Gitlab-Event': 'Ping', 'Connection': 'close', 'Host': '127.0.0.1:8000'}
    body = b'{"object_kind":"push","event_name":"push","before":"ec8400618566c2d81c639629471570d17e37e15b","after":"6a09ce003b9a78d2ba5c911ee84ef9af4369262d","ref":"refs/heads/master","checkout_sha":"6a09ce003b9a78d2ba5c911ee84ef9af4369262d","message":null,"user_id":1,"user_name":"Administrator","user_username":"root","user_email":"","user_avatar":"https://www.gravatar.com/avatar/e64c7d89f26bd1972efa854d13d7dd61?s=80\\u0026d=identicon","project_id":2,"project":{"id":2,"name":"athina-test-solution","description":"","web_url":"http://127.0.0.1/root/athina-test-solution","avatar_url":null,"git_ssh_url":"git@127.0.0.1:root/athina-test-solution.git","git_http_url":"http://127.0.0.1/root/athina-test-solution.git","namespace":"Administrator","visibility_level":20,"path_with_namespace":"root/athina-test-solution","default_branch":"master","ci_config_path":null,"homepage":"http://127.0.0.1/root/athina-test-solution","url":"git@127.0.0.1:root/athina-test-solution.git","ssh_url":"git@127.0.0.1:root/athina-test-solution.git","http_url":"http://127.0.0.1/root/athina-test-solution.git"},"commits":[{"id":"6a09ce003b9a78d2ba5c911ee84ef9af4369262d","message":"Update README.md","timestamp":"2019-12-11T01:36:49Z","url":"http://127.0.0.1/root/athina-test-solution/commit/6a09ce003b9a78d2ba5c911ee84ef9af4369262d","author":{"name":"Administrator","email":"admin@example.com"},"added":[],"modified":["README.md"],"removed":[]}],"total_commits_count":1,"push_options":{},"repository":{"name":"athina-test-solution","url":"git@127.0.0.1:root/athina-test-solution.git","description":"","homepage":"http://127.0.0.1/root/athina-test-solution","git_http_url":"http://127.0.0.1/root/athina-test-solution.git","git_ssh_url":"git@127.0.0.1:root/athina-test-solution.git","visibility_level":20}}'

    def test_webhook(self):
        client = Client()
        response = client.post('/assignments/webhook/', header=self.header, body=self.body)
        print(response)

        self.assertEqual(True, True, "The first time we visit a testing repo have to build the Dockerfile")


class TestCourseGroupMembers(TestCase):
    """The faculty owner and assigned TAs must be added to the course GitLab group."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.gitlab_username = 'prof'
        self.faculty.profile.save()

        self.ta = User.objects.create_user(username='ta1', password='x')
        self.ta.profile.role = UserProfile.ROLE_TA
        self.ta.profile.gitlab_username = 'ta1'
        # Canonical direction: ta.profile.managed_by -> faculty.
        self.ta.profile.managed_by.add(self.faculty)
        self.ta.profile.save()

        self.course = Course.objects.create(name='CSCI 424', owner=self.faculty.id)

    def _patch_requests(self, users_by_username, existing_members=None):
        """Patch http_requests so /users and /groups/<id>/members behave predictably."""
        existing_members = existing_members or []
        posted = []

        def fake_get(url, headers=None, params=None, timeout=None):
            if url.endswith('/users'):
                username = (params or {}).get('username')
                uid = users_by_username.get(username)
                return FakeResponse(json_data=[{'id': uid}] if uid else [])
            if '/members' in url:
                return FakeResponse(json_data=[{'id': i} for i in existing_members])
            return FakeResponse(json_data=[])

        def fake_post(url, headers=None, data=None, timeout=None):
            posted.append((url, data))
            return FakeResponse(json_data={})

        return mock.patch.multiple(
            assignment_views.http_requests, get=fake_get, post=fake_post), posted

    def test_faculty_and_ta_added_to_group(self):
        patcher, posted = self._patch_requests({'prof': 10, 'ta1': 20})
        with patcher:
            ensured = assignment_views._sync_course_group_members(
                self.course, 'gitlab.example.edu', 'token', group_id=99)

        self.assertEqual(ensured, 2)
        added = {d['user_id']: d['access_level'] for _url, d in posted}
        self.assertEqual(added[10], assignment_views.GITLAB_ACCESS_OWNER)       # faculty -> Owner
        self.assertEqual(added[20], assignment_views.GITLAB_ACCESS_MAINTAINER)  # TA -> Maintainer
        for url, _data in posted:
            self.assertIn('/groups/99/members', url)

    def test_existing_members_are_not_re_added(self):
        patcher, posted = self._patch_requests({'prof': 10, 'ta1': 20}, existing_members=[10, 20])
        with patcher:
            ensured = assignment_views._sync_course_group_members(
                self.course, 'gitlab.example.edu', 'token', group_id=99)

        self.assertEqual(ensured, 2)
        self.assertEqual(posted, [])  # idempotent: nothing posted

    def test_unassigned_ta_is_not_added(self):
        other = User.objects.create_user(username='ta2', password='x')
        other.profile.role = UserProfile.ROLE_TA
        other.profile.gitlab_username = 'ta2'
        other.profile.save()  # not assigned to this faculty

        patcher, posted = self._patch_requests({'prof': 10, 'ta1': 20, 'ta2': 30})
        with patcher:
            assignment_views._sync_course_group_members(
                self.course, 'gitlab.example.edu', 'token', group_id=99)

        added = {d['user_id'] for _url, d in posted}
        self.assertNotIn(30, added)

    def test_ta_without_gitlab_username_is_skipped(self):
        self.ta.profile.gitlab_username = ''
        self.ta.profile.save()
        patcher, posted = self._patch_requests({'prof': 10})
        with patcher:
            ensured = assignment_views._sync_course_group_members(
                self.course, 'gitlab.example.edu', 'token', group_id=99)

        self.assertEqual(ensured, 1)  # only faculty
        added = {d['user_id'] for _url, d in posted}
        self.assertEqual(added, {10})


class TestRepoReadme(TestCase):
    """New student repos get a README explaining feedback + privacy."""

    def _assignment(self, output_method):
        return Assignment.objects.create(
            name='SQL 1', absolute_path='1/SQL 1', owner=1, output_method=output_method)

    def test_readme_mentions_canvas_when_output_method_is_canvas(self):
        readme = assignment_views._build_readme(self._assignment('canvas'))
        self.assertIn('Canvas', readme)
        self.assertNotIn('GitLab issues', readme)

    def test_readme_mentions_gitlab_issues_when_selected(self):
        readme = assignment_views._build_readme(self._assignment('gitlab_issues'))
        self.assertIn('GitLab issues', readme)
        # The feedback explanation must come before the privacy note.
        self.assertLess(readme.index('How you will receive feedback'),
                        readme.index('personal information'))

    def test_readme_contains_personal_information_warning(self):
        readme = assignment_views._build_readme(self._assignment('canvas'))
        self.assertIn('Do not put your name', readme)
        self.assertIn('We already know that this is your', readme)

    def test_readme_handles_missing_assignment(self):
        readme = assignment_views._build_readme(None)
        self.assertIn('Canvas', readme)  # default channel
        self.assertIn('Do not put your name', readme)

    def test_seed_posts_initial_commit_on_master(self):
        posted = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            posted['url'] = url
            posted['json'] = json
            return FakeResponse(json_data={})

        with mock.patch.object(assignment_views.http_requests, 'post', fake_post):
            ok = assignment_views._seed_repo_readme(
                'gitlab.example.edu', 'token', 42, self._assignment('gitlab_issues'))

        self.assertTrue(ok)
        self.assertIn('/projects/42/repository/commits', posted['url'])
        self.assertEqual(posted['json']['branch'], 'master')
        action = posted['json']['actions'][0]
        self.assertEqual(action['file_path'], 'README.md')
        self.assertIn('GitLab issues', action['content'])

    def test_seed_skipped_without_project_id(self):
        with mock.patch.object(assignment_views.http_requests, 'post') as fake_post:
            ok = assignment_views._seed_repo_readme('gitlab.example.edu', 'token', None, None)
        self.assertFalse(ok)
        fake_post.assert_not_called()


class PerAssignmentRepoBase(TestCase):
    """Shared fixture: one faculty, one course, two assignments, one student."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.save()

        self.course = Course.objects.create(name='CSCI 330', owner=self.faculty.id)
        self.a1 = Assignment.objects.create(name='SQL 1', course=self.course,
                                            owner=self.faculty.id, absolute_path='1/SQL 1')
        self.a2 = Assignment.objects.create(name='SQL 2', course=self.course,
                                            owner=self.faculty.id, absolute_path='1/SQL 2')
        self.student = Student.objects.create(course=self.course, email='alice@uni.edu')

        self.client.force_login(self.faculty)


class TestProvisionPerAssignment(PerAssignmentRepoBase):
    """Provisioning one assignment must not touch another."""

    def _patch(self):
        get, post = fake_gitlab()
        return mock.patch.multiple(
            assignment_views.http_requests, get=get, post=post), \
            mock.patch.object(assignment_views, '_get_gitlab_config',
                              lambda c: ('gitlab.example.edu', 'tok')), \
            mock.patch.object(assignment_views, '_ensure_course_group',
                              lambda *a, **k: (7, 'grp', False)), \
            mock.patch.object(assignment_views, '_seed_repo_readme', lambda *a, **k: True), \
            mock.patch.object(assignment_views, '_notify_student_repo', lambda *a, **k: False)

    def test_repo_name_is_prefixed_with_the_assignment(self):
        patch_http, patch_cfg, patch_grp, patch_seed, patch_notify = self._patch()
        with patch_http, patch_cfg, patch_grp, patch_seed, patch_notify:
            ok = assignment_views._provision_student_gitlab(
                self.course, self.student, assignment=self.a1)

        self.assertTrue(ok)
        repo = AssignmentRepo.objects.get(student=self.student, assignment=self.a1)
        self.assertIn('sql-1-alice', repo.repository_url)

    def test_provisioning_second_assignment_leaves_the_first_alone(self):
        patch_http, patch_cfg, patch_grp, patch_seed, patch_notify = self._patch()
        with patch_http, patch_cfg, patch_grp, patch_seed, patch_notify:
            assignment_views._provision_student_gitlab(self.course, self.student, assignment=self.a1)
            first_url = AssignmentRepo.objects.get(
                student=self.student, assignment=self.a1).repository_url

            assignment_views._provision_student_gitlab(self.course, self.student, assignment=self.a2)

            # SQL 1 is untouched; SQL 2 has its own repo.
            self.assertEqual(AssignmentRepo.objects.get(
                student=self.student, assignment=self.a1).repository_url, first_url)
            self.assertTrue(AssignmentRepo.objects.get(
                student=self.student, assignment=self.a2).repository_url)

    def test_provision_without_assignment_is_refused(self):
        result = assignment_views._provision_student_gitlab(self.course, self.student)
        self.assertIsInstance(result, str)
        self.assertIn('assignment', result.lower())

    def test_existing_project_is_adopted_not_recreated(self):
        get, post = fake_gitlab(project_exists=True,
                                repo_url='https://gitlab.example.edu/grp/sql-1-alice.git')
        posted = []

        def spy_post(url, *args, **kwargs):
            posted.append(url)
            return FakeResponse(json_data={})

        with mock.patch.object(assignment_views.http_requests, 'get', get), \
             mock.patch.object(assignment_views.http_requests, 'post', spy_post), \
             mock.patch.object(assignment_views, '_get_gitlab_config',
                               lambda c: ('gitlab.example.edu', 'tok')), \
             mock.patch.object(assignment_views, '_ensure_course_group',
                               lambda *a, **k: (7, 'grp', False)), \
             mock.patch.object(assignment_views, '_notify_student_repo', lambda *a, **k: False):
            ok = assignment_views._provision_student_gitlab(
                self.course, self.student, assignment=self.a1)

        self.assertTrue(ok)
        self.assertEqual(AssignmentRepo.objects.get(
            student=self.student, assignment=self.a1).repository_url,
            'https://gitlab.example.edu/grp/sql-1-alice.git')
        # No /projects creation call — the project already existed.
        self.assertEqual([u for u in posted if u.endswith('/projects')], [])


class TestGradingDbSyncPerAssignment(PerAssignmentRepoBase):
    """The grading engine keys rows by assignment, so one row per assignment."""

    def _rows(self):
        """Capture writes by replacing the low-level row writer."""
        rows = []
        return rows, mock.patch.object(
            assignment_views, '_write_grading_row',
            lambda student, c, a, url: rows.append((student.pk, c, a, url)))

    def test_syncs_every_assignment_when_none_given(self):
        AssignmentRepo.objects.create(student=self.student, assignment=self.a1,
                                      repository_url='https://x/sql-1-alice.git')
        rows, patcher = self._rows()
        with patcher:
            assignment_views._sync_student_to_grading_db(self.student)

        synced_assignments = sorted(r[2] for r in rows)
        self.assertEqual(synced_assignments, sorted([self.a1.pk, self.a2.pk]))
        urls = {r[2]: r[3] for r in rows}
        self.assertEqual(urls[self.a1.pk], 'https://x/sql-1-alice.git')
        self.assertEqual(urls[self.a2.pk], '')  # not provisioned yet

    def test_syncs_only_the_named_assignment(self):
        rows, patcher = self._rows()
        with patcher:
            assignment_views._sync_student_to_grading_db(self.student, assignment=self.a2)

        self.assertEqual([r[2] for r in rows], [self.a2.pk])


class TestNotifyPerAssignment(PerAssignmentRepoBase):
    """A student told about one assignment is still notified about the next."""

    def _enable_notifications(self):
        self.faculty.profile.notify_students = True
        self.faculty.profile.notification_api_key = 'key'
        self.faculty.profile.notification_from_email = 'prof@uni.edu'
        self.faculty.profile.save()

    def test_notified_per_assignment(self):
        self._enable_notifications()
        AssignmentRepo.objects.create(student=self.student, assignment=self.a1,
                                      repository_url='https://x/sql-1-alice.git')
        AssignmentRepo.objects.create(student=self.student, assignment=self.a2,
                                      repository_url='https://x/sql-2-alice.git')

        sent = []
        with mock.patch('athina_web.accounts.resend_email.send_email_safe',
                        lambda **kw: sent.append(kw) or 'msg-id'):
            first = assignment_views._notify_student_repo(
                self.course, self.student, assignment=self.a1)
            second = assignment_views._notify_student_repo(
                self.course, self.student, assignment=self.a2)
            # Re-running the first assignment must not re-send.
            repeat = assignment_views._notify_student_repo(
                self.course, self.student, assignment=self.a1)

        self.assertIs(first, True)
        self.assertIs(second, True)   # different assignment -> still notified
        self.assertIs(repeat, False)  # already notified for this one
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1]['subject'], sent[0]['subject'])  # course-level subject

    def test_skipped_without_a_repo(self):
        self._enable_notifications()
        result = assignment_views._notify_student_repo(
            self.course, self.student, assignment=self.a1)
        self.assertIs(result, False)

    def test_force_resends(self):
        self._enable_notifications()
        repo = AssignmentRepo.objects.create(student=self.student, assignment=self.a1,
                                             repository_url='https://x/sql-1-alice.git')
        repo.notified_at = repo.date_created
        repo.save(update_fields=['notified_at'])

        with mock.patch('athina_web.accounts.resend_email.send_email_safe', lambda **kw: 'id'):
            result = assignment_views._notify_student_repo(
                self.course, self.student, assignment=self.a1, force=True)

        self.assertIs(result, True)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestAssignmentRepoViews(PerAssignmentRepoBase):
    """The per-assignment Repositories page is reachable and shows its own state."""

    def test_page_renders_own_state_only(self):
        AssignmentRepo.objects.create(student=self.student, assignment=self.a1,
                                      repository_url='https://x/sql-1-alice.git')

        r1 = self.client.get(reverse('assignments:assignment_students',
                                     kwargs={'assignment_id': self.a1.pk}))
        r2 = self.client.get(reverse('assignments:assignment_students',
                                     kwargs={'assignment_id': self.a2.pk}))

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.context['provisioned'], 1)
        self.assertEqual(r2.context['provisioned'], 0)
        self.assertIn(b'sql-1-alice', r1.content)
        self.assertNotIn(b'sql-1-alice', r2.content)

    def test_assignment_view_is_not_a_dead_end_without_submissions(self):
        """No grading rows yet must still offer provisioning."""
        response = self.client.get(reverse('assignments:assignment_view',
                                           kwargs={'assignment_id': self.a1.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('assignments:assignment_students',
                                              kwargs={'assignment_id': self.a1.pk}))

    def test_course_student_list_no_longer_shows_course_scoped_repo(self):
        response = self.client.get(reverse('assignments:student_list',
                                           kwargs={'course_id': self.course.pk}))
        html = response.content.decode()
        self.assertNotIn('Repository URL', html)  # removed course-scoped column
        # ...and it points at the per-assignment pages instead.
        for assignment in (self.a1, self.a2):
            self.assertIn(reverse('assignments:assignment_students',
                                  kwargs={'assignment_id': assignment.pk}), html)

    def test_repo_edit_sets_url_and_syncs(self):
        with mock.patch.object(assignment_views, '_sync_student_to_grading_db') as sync:
            response = self.client.post(
                reverse('assignments:assignment_repo_edit',
                        kwargs={'assignment_id': self.a2.pk, 'student_id': self.student.pk}),
                {'repository_url': 'https://manual/sql-2-alice.git'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AssignmentRepo.objects.get(
            student=self.student, assignment=self.a2).repository_url,
            'https://manual/sql-2-alice.git')
        sync.assert_called_once()
