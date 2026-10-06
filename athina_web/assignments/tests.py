import os

from django.conf import settings
from django.test import TestCase
from django.test import Client
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from unittest import mock

from athina_web.accounts.models import UserProfile
from athina_web.assignments.models import (Assignment, Course, Student, AssignmentRepo,
                                           StudentGroup, GroupMembership)
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


@override_settings(SECURE_SSL_REDIRECT=False)
class TestFunctions(TestCase):
    def test_assignments_api_rejects_anonymous_writes(self):
        """Regression: an unauthenticated POST created an Assignment record.

        The view was a ListCreateAPIView with no permission classes, so anyone
        who could reach the endpoint could write. It is read-only now; the CLI
        only ever GETs it.
        """
        client = Client()
        response = client.post('/assignments/api/', data='{"simulate": true}',
                               content_type='application/json')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Assignment.objects.count(), 0, "no record may be created")

    def test_assignments_api_still_lists_for_the_daemon(self):
        Assignment.objects.create(name='SQL 1', active=True, owner=1, absolute_path='1/SQL 1')
        response = Client().get('/assignments/api/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

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

    def test_member_list_pages_past_the_first_hundred(self):
        """A group with >100 members must not look like it has only 100.

        Without paging, members past the first page appear absent, so they are
        re-added and GitLab rejects the request.
        """
        page_one = [{'id': i} for i in range(1, 101)]      # exactly per_page
        page_two = [{'id': 101}]                          # short page ends it
        pages = {1: page_one, 2: page_two}
        requested_pages = []

        def fake_get(url, headers=None, params=None, timeout=None):
            if url.endswith('/users'):
                return FakeResponse(json_data=[{'id': 999}])
            if '/members' in url:
                page = (params or {}).get('page', 1)
                requested_pages.append(page)
                return FakeResponse(json_data=pages.get(page, []))
            return FakeResponse(json_data=[])

        with mock.patch.object(assignment_views.http_requests, 'get', fake_get), \
             mock.patch.object(assignment_views.http_requests, 'post') as posted:
            members = assignment_views._gitlab_list_group_members(
                'https://gitlab.example.edu/api/v4', {}, group_id=99)

        self.assertEqual(requested_pages, [1, 2], "must follow pagination")
        self.assertEqual(len(members), 101)

    def test_member_already_present_on_a_later_page_is_not_re_added(self):
        """The 101st member is found, so no duplicate POST is attempted."""
        def fake_get(url, headers=None, params=None, timeout=None):
            if url.endswith('/users'):
                return FakeResponse(json_data=[{'id': 101}])
            if '/members' in url:
                page = (params or {}).get('page', 1)
                if page == 1:
                    return FakeResponse(json_data=[{'id': i} for i in range(1, 101)])
                return FakeResponse(json_data=[{'id': 101}])
            return FakeResponse(json_data=[])

        with mock.patch.object(assignment_views.http_requests, 'get', fake_get), \
             mock.patch.object(assignment_views.http_requests, 'post') as posted:
            ok = assignment_views._gitlab_add_group_member(
                'https://gitlab.example.edu/api/v4', {}, 99, 'someone', 40)

        self.assertTrue(ok)
        posted.assert_not_called()


class TestRevokeUnassignedTA(TestCase):
    """Unassigning a TA must revoke their GitLab access, not just Django access."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.gitlab_username = 'prof'
        self.faculty.profile.save()

        self.ta = User.objects.create_user(username='ta1', password='x')
        self.ta.profile.role = UserProfile.ROLE_TA
        self.ta.profile.gitlab_username = 'ta1'
        self.ta.profile.managed_by.add(self.faculty)
        self.ta.profile.save()

        self.course = Course.objects.create(name='CSCI 424', owner=self.faculty.id)
        Assignment.objects.create(name='SQL 1', course=self.course,
                                  owner=self.faculty.id, absolute_path='1/SQL 1')

    def _patch(self, deleted):
        def fake_get(url, headers=None, params=None, timeout=None):
            if url.endswith('/users'):
                uname = (params or {}).get('username')
                ids = {'prof': 10, 'ta1': 20}
                return FakeResponse(json_data=[{'id': ids[uname]}] if uname in ids else [])
            if url.endswith('/groups'):
                # _ensure_course_group searches first; return the existing group.
                return FakeResponse(json_data=[{'id': 99, 'path': 'athina-1-csci-424'}])
            if '/members' in url:
                return FakeResponse(json_data=[{'id': 10}])   # only faculty present
            return FakeResponse(json_data=[])

        def fake_post(url, headers=None, data=None, timeout=None):
            return FakeResponse(json_data={'id': 99})

        def fake_delete(url, headers=None, params=None, timeout=None):
            deleted.append(url)
            return FakeResponse(json_data={})

        return mock.patch.multiple(assignment_views.http_requests,
                                   get=fake_get, post=fake_post, delete=fake_delete), \
            mock.patch.object(assignment_views, '_get_gitlab_config',
                              lambda c: ('gitlab.example.edu', 'token'))

    def test_unassigning_revokes_gitlab_membership(self):
        self.ta.profile.managed_by.clear()
        self.ta.profile.save()

        deleted = []
        patcher, gitlab_cfg = self._patch(deleted)
        with patcher, gitlab_cfg:
            _courses, _ensured, removed = assignment_views.sync_faculty_course_members(
                self.faculty, removed_ta_ids=[self.ta.pk])

        self.assertEqual(removed, 1)
        self.assertEqual(len(deleted), 1)
        self.assertIn('/groups/', deleted[0])
        self.assertIn('/members/20', deleted[0])

    def test_nothing_revoked_when_no_ta_was_removed(self):
        deleted = []
        patcher, gitlab_cfg = self._patch(deleted)
        with patcher, gitlab_cfg:
            _courses, _ensured, removed = assignment_views.sync_faculty_course_members(
                self.faculty, removed_ta_ids=[])

        self.assertEqual(removed, 0)
        self.assertEqual(deleted, [])

    def test_removal_of_a_ta_without_gitlab_username_is_skipped(self):
        self.ta.profile.gitlab_username = ''
        self.ta.profile.save()

        deleted = []
        patcher, gitlab_cfg = self._patch(deleted)
        with patcher, gitlab_cfg:
            _courses, _ensured, removed = assignment_views.sync_faculty_course_members(
                self.faculty, removed_ta_ids=[self.ta.pk])

        self.assertEqual(removed, 0)
        self.assertEqual(deleted, [])

    def test_404_means_already_removed(self):
        """GitLab returns 404 for a non-member; that is the state we want."""
        def fake_delete(url, headers=None, params=None, timeout=None):
            return FakeResponse(ok=False, status_code=404)

        with mock.patch.object(assignment_views.http_requests, 'delete', fake_delete):
            ok = assignment_views._gitlab_remove_group_member(
                'https://gitlab.example.edu/api/v4', {}, 99, 20)

        self.assertTrue(ok)

    def test_failed_removal_is_reported(self):
        def fake_delete(url, headers=None, params=None, timeout=None):
            return FakeResponse(ok=False, status_code=403)

        with mock.patch.object(assignment_views.http_requests, 'delete', fake_delete):
            ok = assignment_views._gitlab_remove_group_member(
                'https://gitlab.example.edu/api/v4', {}, 99, 20)

        self.assertFalse(ok)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestAssignTasRevokesAccess(TestCase):
    """End-to-end: the assign_tas view revokes access for unchecked TAs."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.save()

        self.ta = User.objects.create_user(username='ta1', password='x')
        self.ta.profile.role = UserProfile.ROLE_TA
        self.ta.profile.gitlab_username = 'ta1'
        self.ta.profile.managed_by.add(self.faculty)
        self.ta.profile.save()

        self.client.force_login(self.faculty)

    def test_unchecking_a_ta_passes_them_as_removed(self):
        captured = {}

        def fake_sync(faculty_user, removed_ta_ids=()):
            captured['removed'] = set(removed_ta_ids)
            return (0, 0, 0)

        with mock.patch('athina_web.assignments.views.sync_faculty_course_members', fake_sync):
            # Post with no TAs selected -> the existing TA is unassigned.
            response = self.client.post(reverse('accounts:assign_tas'), {'tas': []})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(captured['removed'], {self.ta.pk})
        # Django-side access is revoked too.
        self.ta.profile.refresh_from_db()
        self.assertFalse(self.ta.profile.managed_by.filter(pk=self.faculty.pk).exists())

    def test_keeping_a_ta_passes_no_removals(self):
        captured = {}

        def fake_sync(faculty_user, removed_ta_ids=()):
            captured['removed'] = set(removed_ta_ids)
            return (0, 0, 0)

        with mock.patch('athina_web.assignments.views.sync_faculty_course_members', fake_sync):
            self.client.post(reverse('accounts:assign_tas'), {'tas': [self.ta.pk]})

        self.assertEqual(captured['removed'], set())


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


class TestGroupModels(PerAssignmentRepoBase):
    """Group identity, naming and the one-group-per-assignment rule."""

    def test_repo_name_uses_assignment_and_group_number(self):
        group = StudentGroup.objects.create(assignment=self.a1, name='Team Rocket', repo_index=3)
        self.assertEqual(assignment_views._group_repo_name(self.a1, group), 'sql-1-g3')

    def test_slug_sanitises_the_name(self):
        group = StudentGroup(assignment=self.a1, name='Team  Rocket!! (2)')
        self.assertEqual(group.slug, 'team-rocket-2')

    def test_repo_index_is_stable_when_an_earlier_group_is_deleted(self):
        """Deleting group 2 must not renumber group 3 onto a different repo."""
        StudentGroup.objects.create(assignment=self.a1, name='One', repo_index=1)
        two = StudentGroup.objects.create(assignment=self.a1, name='Two', repo_index=2)
        three = StudentGroup.objects.create(assignment=self.a1, name='Three', repo_index=3)

        two.delete()

        self.assertEqual(StudentGroup.objects.get(pk=three.pk).repo_index, 3)
        self.assertEqual(
            assignment_views._group_repo_name(self.a1, three), 'sql-1-g3')

    def test_next_repo_index_continues_the_sequence(self):
        StudentGroup.objects.create(assignment=self.a1, name='One', repo_index=4)
        self.assertEqual(
            StudentGroup(assignment=self.a1).next_repo_index(), 5)

    def test_ungrouped_students_excludes_group_members(self):
        second = Student.objects.create(course=self.course, email='bob@uni.edu')
        group = StudentGroup.objects.create(assignment=self.a1, name='G', repo_index=1)
        GroupMembership.objects.create(group=group, student=self.student)

        ungrouped = list(assignment_views._ungrouped_students(self.a1))
        self.assertIn(second, ungrouped)
        self.assertNotIn(self.student, ungrouped)


class TestGroupProvisioning(PerAssignmentRepoBase):
    """A group gets ONE repository, shared by every member."""

    def setUp(self):
        super().setUp()
        self.bob = Student.objects.create(course=self.course, email='bob@uni.edu')
        self.group = StudentGroup.objects.create(
            assignment=self.a1, name='Team Rocket',
            repo_index=StudentGroup(assignment=self.a1).next_repo_index())
        GroupMembership.objects.create(group=self.group, student=self.student)
        GroupMembership.objects.create(group=self.group, student=self.bob)

    def _patch(self, member_ids=(55, 66)):
        get, post = fake_gitlab()
        users = {'alice': [{'id': member_ids[0]}], 'bob': [{'id': member_ids[1]}]}

        def scoped_get(url, *args, **kwargs):
            if url.endswith('/users'):
                return FakeResponse(json_data=users.get((kwargs.get('params') or {}).get('username'), []))
            return get(url, *args, **kwargs)

        return mock.patch.multiple(
            assignment_views.http_requests, get=scoped_get, post=post), \
            mock.patch.object(assignment_views, '_get_gitlab_config',
                              lambda c: ('gitlab.example.edu', 'tok')), \
            mock.patch.object(assignment_views, '_ensure_course_group',
                              lambda *a, **k: (7, 'grp', False)), \
            mock.patch.object(assignment_views, '_seed_repo_readme', lambda *a, **k: True), \
            mock.patch.object(assignment_views, '_notify_student_repo', lambda *a, **k: False), \
            mock.patch.object(assignment_views, '_sync_student_to_grading_db', lambda *a, **k: None)

    def test_all_members_share_one_repository_url(self):
        patch_http, patch_cfg, patch_grp, patch_seed, patch_notify, patch_sync = self._patch()
        with patch_http, patch_cfg, patch_grp, patch_seed, patch_notify, patch_sync:
            result = assignment_views._provision_group_gitlab(self.course, self.a1, self.group)

        self.assertIs(result, True)
        urls = {AssignmentRepo.objects.get(student=s, assignment=self.a1).repository_url
                for s in (self.student, self.bob)}
        self.assertEqual(len(urls), 1, "group members must share exactly one URL")
        self.assertIn('sql-1-g1', urls.pop())

    def test_provisioning_a_student_in_a_group_delegates_to_the_group(self):
        """The per-student entry point must not create an individual repo."""
        patch_http, patch_cfg, patch_grp, patch_seed, patch_notify, patch_sync = self._patch()
        with patch_http, patch_cfg, patch_grp, patch_seed, patch_notify, patch_sync:
            assignment_views._provision_student_gitlab(self.course, self.student, assignment=self.a1)

        url = AssignmentRepo.objects.get(student=self.student, assignment=self.a1).repository_url
        self.assertIn('sql-1-g1', url)     # group repo...
        self.assertNotIn('alice', url)     # ...not the individual naming

    def test_unresolvable_member_is_reported_but_group_still_provisions(self):
        get, post = fake_gitlab()
        users = {'alice': [{'id': 55}], 'bob': []}   # bob's account is missing

        def scoped_get(url, *args, **kwargs):
            if url.endswith('/users'):
                return FakeResponse(json_data=users.get((kwargs.get('params') or {}).get('username'), []))
            return get(url, *args, **kwargs)

        with mock.patch.object(assignment_views.http_requests, 'get', scoped_get), \
             mock.patch.object(assignment_views.http_requests, 'post', post), \
             mock.patch.object(assignment_views, '_get_gitlab_config',
                               lambda c: ('gitlab.example.edu', 'tok')), \
             mock.patch.object(assignment_views, '_ensure_course_group',
                               lambda *a, **k: (7, 'grp', False)), \
             mock.patch.object(assignment_views, '_seed_repo_readme', lambda *a, **k: True), \
             mock.patch.object(assignment_views, '_notify_student_repo', lambda *a, **k: False), \
             mock.patch.object(assignment_views, '_sync_student_to_grading_db', lambda *a, **k: None):
            result = assignment_views._provision_group_gitlab(self.course, self.a1, self.group)

        self.assertIsInstance(result, str)          # a warning string, not True
        self.assertIn('bob@uni.edu', result)
        self.assertTrue(AssignmentRepo.objects.get(
            student=self.student, assignment=self.a1).repository_url)

    def test_group_with_no_resolvable_members_fails(self):
        def empty_get(url, *args, **kwargs):
            return FakeResponse(json_data=[])

        with mock.patch.object(assignment_views.http_requests, 'get', empty_get), \
             mock.patch.object(assignment_views, '_get_gitlab_config',
                               lambda c: ('gitlab.example.edu', 'tok')):
            result = assignment_views._provision_group_gitlab(self.course, self.a1, self.group)

        self.assertIsInstance(result, str)
        self.assertIn('could be found', result)


class TestGroupSettingsEnv(PerAssignmentRepoBase):
    """The engine reads group settings from the YAML, so the web app writes .env."""

    def _write_env(self, initial='GIT_PROVIDER=gitlab'):
        assignment_dir = os.path.join(settings.BASE_DIR, self.a1.absolute_path)
        os.makedirs(assignment_dir, exist_ok=True)
        with open(os.path.join(assignment_dir, '.env'), 'w') as fh:
            fh.write(initial + '\n')

    def test_writes_group_flag_and_largest_group_size(self):
        other = Student.objects.create(course=self.course, email='bob@uni.edu')
        third = Student.objects.create(course=self.course, email='carol@uni.edu')
        g1 = StudentGroup.objects.create(assignment=self.a1, name='G1', repo_index=1)
        g2 = StudentGroup.objects.create(assignment=self.a1, name='G2', repo_index=2)
        for s in (self.student, other):
            GroupMembership.objects.create(group=g1, student=s)
        GroupMembership.objects.create(group=g2, student=third)

        self._write_env()
        assignment_views._write_group_settings(self.a1, has_groups=True)

        env = open(os.path.join(settings.BASE_DIR, self.a1.absolute_path, '.env')).read()
        self.assertIn('GROUP_ASSIGNMENT=true', env)
        self.assertIn('SAME_URL_LIMIT=2', env)   # largest group wins

    def test_without_groups_resets_to_individual(self):
        self._write_env('SAME_URL_LIMIT=5\nGROUP_ASSIGNMENT=true')
        assignment_views._write_group_settings(self.a1, has_groups=False)

        env = open(os.path.join(settings.BASE_DIR, self.a1.absolute_path, '.env')).read()
        self.assertIn('GROUP_ASSIGNMENT=false', env)
        self.assertIn('SAME_URL_LIMIT=1', env)
        # The stale value is replaced, not appended.
        self.assertEqual(env.count('SAME_URL_LIMIT='), 1)

    def test_does_not_edit_the_yaml(self):
        self._write_env()
        assignment_views._write_group_settings(self.a1, has_groups=True)
        yaml_path = os.path.join(settings.BASE_DIR, self.a1.absolute_path, 'athina.yaml')
        self.assertFalse(os.path.exists(yaml_path))


@override_settings(SECURE_SSL_REDIRECT=False)
class TestGroupViews(PerAssignmentRepoBase):
    """The group management pages."""

    def _group(self, name='Team Rocket'):
        return StudentGroup.objects.create(
            assignment=self.a1, name=name,
            repo_index=StudentGroup(assignment=self.a1).next_repo_index())

    def test_groups_page_lists_members(self):
        group = self._group()
        GroupMembership.objects.create(group=group, student=self.student)

        response = self.client.get(reverse('assignments:assignment_groups',
                                           kwargs={'assignment_id': self.a1.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['groups'][0]['group'], group)
        self.assertIn('sql-1-g1', response.content.decode())

    def test_create_group_with_members(self):
        second = Student.objects.create(course=self.course, email='bob@uni.edu')
        response = self.client.post(
            reverse('assignments:assignment_group_create', kwargs={'assignment_id': self.a1.pk}),
            {'name': 'Team Rocket', 'members': [self.student.pk, second.pk]})

        self.assertEqual(response.status_code, 302)
        group = StudentGroup.objects.get(assignment=self.a1, name='Team Rocket')
        self.assertEqual(group.repo_index, 1)
        self.assertEqual(group.memberships.count(), 2)

    def test_duplicate_group_name_is_a_form_error_not_a_500(self):
        self._group('Team Rocket')
        response = self.client.post(
            reverse('assignments:assignment_group_create', kwargs={'assignment_id': self.a1.pk}),
            {'name': 'team rocket'})

        self.assertEqual(response.status_code, 200)   # re-rendered
        self.assertContains(response, 'already a group')
        self.assertEqual(StudentGroup.objects.filter(assignment=self.a1).count(), 1)

    def test_edit_moves_students_between_groups(self):
        group = self._group()
        GroupMembership.objects.create(group=group, student=self.student)
        second = Student.objects.create(course=self.course, email='bob@uni.edu')

        response = self.client.post(
            reverse('assignments:assignment_group_edit',
                    kwargs={'assignment_id': self.a1.pk, 'group_id': group.pk}),
            {'name': group.name, 'students': [second.pk]})

        self.assertEqual(response.status_code, 302)
        members = list(group.memberships.values_list('student_id', flat=True))
        self.assertEqual(members, [second.pk])

    def test_member_in_another_group_is_not_offered(self):
        """A student cannot hold two repos on one assignment."""
        g1 = self._group('One')
        g2 = self._group('Two')
        GroupMembership.objects.create(group=g1, student=self.student)

        response = self.client.get(
            reverse('assignments:assignment_group_edit',
                    kwargs={'assignment_id': self.a1.pk, 'group_id': g2.pk}))
        offered = list(response.context['members_form'].fields['students'].queryset)
        self.assertNotIn(self.student, offered)

    def test_deleting_a_group_clears_the_shared_repo_url(self):
        group = self._group()
        GroupMembership.objects.create(group=group, student=self.student)
        AssignmentRepo.objects.create(student=self.student, assignment=self.a1,
                                      repository_url='https://x/sql-1-g1.git')

        response = self.client.post(
            reverse('assignments:assignment_group_delete',
                    kwargs={'assignment_id': self.a1.pk, 'group_id': group.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AssignmentRepo.objects.get(
            student=self.student, assignment=self.a1).repository_url, '')
        self.assertEqual(StudentGroup.objects.filter(assignment=self.a1).count(), 0)

    def test_autogroup_splits_remaining_students(self):
        for email in ('b@uni.edu', 'c@uni.edu', 'd@uni.edu'):
            Student.objects.create(course=self.course, email=email)

        response = self.client.post(
            reverse('assignments:assignment_group_autogroup',
                    kwargs={'assignment_id': self.a1.pk}), {'size': 2})

        self.assertEqual(response.status_code, 302)
        groups = StudentGroup.objects.filter(assignment=self.a1)
        self.assertEqual(groups.count(), 2)          # 4 students / 2
        self.assertEqual(sum(g.memberships.count() for g in groups), 4)
        # Every group has a distinct repo index, so no two share a repo name.
        self.assertEqual(len({g.repo_index for g in groups}), 2)

    def test_autogroup_rejects_a_size_below_two(self):
        Student.objects.create(course=self.course, email='b@uni.edu')
        self.client.post(reverse('assignments:assignment_group_autogroup',
                                 kwargs={'assignment_id': self.a1.pk}), {'size': 1})
        self.assertEqual(StudentGroup.objects.filter(assignment=self.a1).count(), 0)

    def test_autogroup_says_nothing_to_do_when_all_grouped(self):
        group = self._group()
        GroupMembership.objects.create(group=group, student=self.student)
        response = self.client.post(
            reverse('assignments:assignment_group_autogroup',
                    kwargs={'assignment_id': self.a1.pk}), {'size': 2}, follow=True)
        self.assertEqual(StudentGroup.objects.filter(assignment=self.a1).count(), 1)

    def test_ta_cannot_manage_groups(self):
        """Grouping is roster management, which TAs do not have."""
        ta = User.objects.create_user(username='ta', password='x')
        ta.profile.role = UserProfile.ROLE_TA
        ta.profile.managed_by.add(self.faculty)
        ta.profile.save()
        self.client.force_login(ta)

        # May view (course access) ...
        view = self.client.get(reverse('assignments:assignment_groups',
                                       kwargs={'assignment_id': self.a1.pk}))
        self.assertEqual(view.status_code, 200)
        # ... but not create.
        create = self.client.post(
            reverse('assignments:assignment_group_create', kwargs={'assignment_id': self.a1.pk}),
            {'name': 'Nope'})
        self.assertEqual(create.status_code, 404)


class TestTeamNames(TestCase):
    """Random team names must not collide with existing ones."""

    def test_name_avoids_taken_names(self):
        from athina_web.assignments.team_names import random_team_name
        taken = {'%s %s' % (a, n) for a in ('Amber', 'Azure') for n in ('Anchor', 'Arrow')}
        for _ in range(50):
            self.assertNotIn(random_team_name(taken).lower(), {t.lower() for t in taken})

    def test_name_avoids_taken_names_case_insensitively(self):
        from athina_web.assignments.team_names import random_team_name
        # 'Team Rocket' is taken; a generated name must never match it either way.
        for _ in range(50):
            name = random_team_name({'team rocket'})
            self.assertNotEqual(name.lower(), 'team rocket')

    def test_name_is_two_words(self):
        from athina_web.assignments.team_names import random_team_name
        self.assertEqual(len(random_team_name().split()), 2)

    def test_falls_back_to_a_suffix_when_space_is_exhausted(self):
        """Every pair taken still yields a unique name rather than looping forever."""
        from athina_web.assignments import team_names
        taken = {'%s %s' % (a, n)
                 for a in team_names.ADJECTIVES for n in team_names.NOUNS}
        name = team_names.random_team_name(taken)
        self.assertNotIn(name.lower(), {t.lower() for t in taken})
        self.assertRegex(name, r'\s\d+$')


@override_settings(SECURE_SSL_REDIRECT=False)
class TestGroupAssignmentFlag(PerAssignmentRepoBase):
    """Group vs individual is a per-assignment setting."""

    def test_defaults_to_individual(self):
        self.assertFalse(Assignment.objects.get(pk=self.a2.pk).is_group_assignment)

    def test_form_exposes_the_checkbox(self):
        from athina_web.assignments.forms import AssignmentForm
        form = AssignmentForm(user=self.faculty)
        self.assertIn('is_group_assignment', form.fields)
        self.assertFalse(form.fields['is_group_assignment'].initial)

    def test_provisioning_ignores_groups_on_an_individual_assignment(self):
        """Leftover groups must not turn an individual assignment into a group one."""
        group = StudentGroup.objects.create(assignment=self.a1, name='G', repo_index=1)
        GroupMembership.objects.create(group=group, student=self.student,
                                       assignment=self.a1)
        self.a1.is_group_assignment = False
        self.a1.save(update_fields=['is_group_assignment'])

        provisioned_groups = []
        with mock.patch.object(assignment_views, '_provision_group_gitlab',
                               lambda c, a, g: provisioned_groups.append(g) or True), \
             mock.patch.object(assignment_views, '_provision_student_gitlab',
                               lambda c, s, **k: True), \
             mock.patch.object(assignment_views, '_get_gitlab_config', lambda c: (None, None)), \
             mock.patch.object(assignment_views, '_sync_student_to_grading_db', lambda *a, **k: None), \
             mock.patch.object(assignment_views, '_write_group_settings', lambda *a, **k: None), \
             mock.patch.object(assignment_views, '_notify_student_repo', lambda *a, **k: False):
            # Go through the HTTP view so the messages framework is in play.
            self.client.post(reverse('assignments:assignment_provision_students',
                                     kwargs={'assignment_id': self.a1.pk}))

        self.assertEqual(provisioned_groups, [],
                         "groups must be ignored when is_group_assignment is False")

    def test_creating_a_group_flips_the_flag(self):
        self.client.post(
            reverse('assignments:assignment_group_create', kwargs={'assignment_id': self.a1.pk}),
            {'name': 'Team Rocket'})
        self.assertTrue(Assignment.objects.get(pk=self.a1.pk).is_group_assignment)

    def test_autogroup_flips_the_flag(self):
        Student.objects.create(course=self.course, email='b@uni.edu')
        self.client.post(
            reverse('assignments:assignment_group_autogroup',
                    kwargs={'assignment_id': self.a1.pk}), {'size': 2})
        self.assertTrue(Assignment.objects.get(pk=self.a1.pk).is_group_assignment)

    def test_board_warns_for_an_individual_assignment(self):
        self.a1.is_group_assignment = False
        self.a1.save(update_fields=['is_group_assignment'])
        response = self.client.get(reverse('assignments:assignment_groups',
                                           kwargs={'assignment_id': self.a1.pk}))
        self.assertContains(response, 'set up as individual work')


@override_settings(SECURE_SSL_REDIRECT=False)
class TestGroupMove(PerAssignmentRepoBase):
    """Drag-and-drop moves replace membership rather than adding a second one."""

    def setUp(self):
        super().setUp()
        self.g1 = StudentGroup.objects.create(assignment=self.a1, name='Alpha', repo_index=1)
        self.g2 = StudentGroup.objects.create(assignment=self.a1, name='Beta', repo_index=2)
        self.url = reverse('assignments:assignment_group_move',
                           kwargs={'assignment_id': self.a1.pk})

    def _move(self, group):
        return self.client.post(self.url, {'student': self.student.pk, 'group': group})

    def test_move_into_a_group(self):
        response = self._move(self.g1.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(GroupMembership.objects.filter(assignment=self.a1)
                 .values_list('group__name', flat=True)), ['Alpha'])

    def test_moving_between_groups_replaces_the_membership(self):
        self._move(self.g1.pk)
        self._move(self.g2.pk)

        memberships = GroupMembership.objects.filter(assignment=self.a1, student=self.student)
        self.assertEqual(memberships.count(), 1, "a student must be in one group only")
        self.assertEqual(memberships.first().group, self.g2)

    def test_moving_to_unassigned_clears_membership_and_repo(self):
        self._move(self.g1.pk)
        AssignmentRepo.objects.update_or_create(
            assignment=self.a1, student=self.student,
            defaults={'repository_url': 'https://x/sql-1-g1.git'})

        response = self._move('')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(GroupMembership.objects.filter(
            assignment=self.a1, student=self.student).exists())
        self.assertEqual(AssignmentRepo.objects.get(
            assignment=self.a1, student=self.student).repository_url, '')

    def test_joining_a_provisioned_group_adopts_its_url(self):
        """A URL shared by two existing members is the group's — adopt it."""
        other = Student.objects.create(course=self.course, email='bob@uni.edu')
        third = Student.objects.create(course=self.course, email='carol@uni.edu')
        for member in (other, third):
            GroupMembership.objects.create(group=self.g1, student=member, assignment=self.a1)
            AssignmentRepo.objects.update_or_create(
                assignment=self.a1, student=member,
                defaults={'repository_url': 'https://x/sql-1-g1.git'})
        # The arriving student has their own individual repo, which must not win.
        AssignmentRepo.objects.update_or_create(
            assignment=self.a1, student=self.student,
            defaults={'repository_url': 'https://x/sql-1-alice.git'})

        self._move(self.g1.pk)

        self.assertEqual(AssignmentRepo.objects.get(
            assignment=self.a1, student=self.student).repository_url,
            'https://x/sql-1-g1.git')

    def test_single_members_url_is_not_treated_as_the_group_repo(self):
        """A URL held by one member is individual, not the group's."""
        other = Student.objects.create(course=self.course, email='bob@uni.edu')
        GroupMembership.objects.create(group=self.g1, student=other, assignment=self.a1)
        AssignmentRepo.objects.update_or_create(
            assignment=self.a1, student=other,
            defaults={'repository_url': 'https://x/sql-1-bob.git'})

        self._move(self.g1.pk)

        # Adopting bob's URL would silently give the group one student's repo.
        self.assertEqual(AssignmentRepo.objects.get(
            assignment=self.a1, student=self.student).repository_url, '')

    def test_joining_an_unprovisioned_group_clears_an_individual_url(self):
        AssignmentRepo.objects.update_or_create(
            assignment=self.a1, student=self.student,
            defaults={'repository_url': 'https://x/sql-1-alice.git'})

        self._move(self.g1.pk)

        # Must not keep an individual repo the group-mates do not share.
        self.assertEqual(AssignmentRepo.objects.get(
            assignment=self.a1, student=self.student).repository_url, '')

    def test_move_flips_the_assignment_to_group(self):
        self._move(self.g1.pk)
        self.assertTrue(Assignment.objects.get(pk=self.a1.pk).is_group_assignment)

    def test_move_requires_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_move_rejects_a_group_from_another_assignment(self):
        foreign = StudentGroup.objects.create(assignment=self.a2, name='Elsewhere', repo_index=1)
        response = self._move(foreign.pk)
        self.assertEqual(response.status_code, 404)

    def test_ta_cannot_move_students(self):
        ta = User.objects.create_user(username='ta', password='x')
        ta.profile.role = UserProfile.ROLE_TA
        ta.profile.managed_by.add(self.faculty)
        ta.profile.save()
        self.client.force_login(ta)

        self.assertEqual(assignment_views._user_can_manage_students(ta, self.course), False)
        response = self._move(self.g1.pk)
        self.assertEqual(response.status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestRandomNameView(PerAssignmentRepoBase):
    """The Random button must never suggest an existing team name."""

    def test_returns_an_unused_name(self):
        StudentGroup.objects.create(assignment=self.a1, name='Bold Meadow', repo_index=1)
        url = reverse('assignments:assignment_group_random_name',
                      kwargs={'assignment_id': self.a1.pk})

        for _ in range(30):
            name = self.client.get(url).json()['name']
            self.assertNotEqual(name.lower(), 'bold meadow')

    def test_ta_cannot_request_names(self):
        ta = User.objects.create_user(username='ta', password='x')
        ta.profile.role = UserProfile.ROLE_TA
        ta.profile.managed_by.add(self.faculty)
        ta.profile.save()
        self.client.force_login(ta)

        response = self.client.get(reverse('assignments:assignment_group_random_name',
                                           kwargs={'assignment_id': self.a1.pk}))
        self.assertEqual(response.status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class TestGroupBoardRendering(PerAssignmentRepoBase):
    """The board shows every column and the data the drag script needs."""

    def test_board_has_unassigned_column_and_group_columns(self):
        group = StudentGroup.objects.create(assignment=self.a1, name='Alpha', repo_index=1)
        GroupMembership.objects.create(group=group, student=self.student, assignment=self.a1)
        other = Student.objects.create(course=self.course, email='bob@uni.edu')

        response = self.client.get(reverse('assignments:assignment_groups',
                                           kwargs={'assignment_id': self.a1.pk}))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Unassigned', html)
        self.assertIn('Alpha', html)
        self.assertIn('sql-1-g1', html)
        # Both students appear as draggable chips, keyed by id for the script.
        self.assertIn('data-student-id="%d"' % self.student.pk, html)
        self.assertIn('data-student-id="%d"' % other.pk, html)
        self.assertIn('draggable="true"', html)

    def test_membership_assignment_is_denormalised(self):
        group = StudentGroup.objects.create(assignment=self.a1, name='Alpha', repo_index=1)
        membership = GroupMembership.objects.create(group=group, student=self.student)
        # save() copies it from the group, so the uniquness constraint can apply.
        self.assertEqual(membership.assignment_id, self.a1.pk)

    def test_db_prevents_two_groups_for_one_student(self):
        g1 = StudentGroup.objects.create(assignment=self.a1, name='Alpha', repo_index=1)
        g2 = StudentGroup.objects.create(assignment=self.a1, name='Beta', repo_index=2)
        GroupMembership.objects.create(group=g1, student=self.student, assignment=self.a1)

        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GroupMembership.objects.create(group=g2, student=self.student,
                                               assignment=self.a1)
