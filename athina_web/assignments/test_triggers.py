"""Tests for the web app's per-student daemon signalling.

The webhook handler and the Force-rerun button must tell the daemon which student
to grade, not merely "start a pass". These tests cover the signal payloads and the
matching of a webhook to the students it belongs to.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from athina_web.accounts.models import UserProfile
from athina_web.assignments.models import Assignment, Course, Student
from athina_web.assignments import views as assignment_views


@override_settings(SECURE_SSL_REDIRECT=False)
class TestTriggerSignals(TestCase):
    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.save()
        self.course = Course.objects.create(name='CSCI 330', owner=self.faculty.id)
        self.assignment = Assignment.objects.create(
            name='SQL 1', course=self.course, owner=self.faculty.id,
            absolute_path='1/SQL 1')

    def test_one_event_covering_the_students(self):
        """One event per assignment, carrying the assignment directory."""
        with mock.patch.object(assignment_views, 'write_student_trigger') as write:
            assignment_views._trigger_daemon(5, 9, [11, 12], self.assignment)

        write.assert_called_once()
        self.assertEqual(write.call_args[0][0], 5)
        self.assertEqual(write.call_args[0][1], 9)
        self.assertIn(write.call_args[0][2], (11, 12))
        # Trailing separator matters: the engine globs '<dir>/*.yaml'.
        self.assertTrue(write.call_args[1]['assignment_dir'].endswith('SQL 1/'))

    def test_no_ids_falls_back_to_full_pass(self):
        """When the students cannot be named, ask for the whole pass."""
        with mock.patch.object(assignment_views, 'write_student_trigger') as write, \
                mock.patch.object(assignment_views, 'write_full_pass_trigger') as full:
            assignment_views._trigger_daemon()
        write.assert_not_called()
        full.assert_called_once()

    def test_group_members_share_a_single_event(self):
        """One repository = one job: the engine grades every member itself."""
        with mock.patch.object(assignment_views, 'write_student_trigger') as write:
            assignment_views._trigger_daemon(5, 9, [11, 13], self.assignment)
        write.assert_called_once()

    def test_write_failure_is_swallowed(self):
        """A failed signal must not break the instructor's request."""
        with mock.patch.object(assignment_views, 'write_student_trigger',
                               side_effect=OSError("volume gone")):
            assignment_views._trigger_daemon(5, 9, [11], self.assignment)  # no raise

    def test_assignment_lookup_uses_the_model_pk(self):
        """db-mode assignment_id IS the Django PK, so the direct hit is used."""
        found = assignment_views._assignment_from_ids(self.course.pk, self.assignment.pk)
        self.assertEqual(found, self.assignment)

    def test_assignment_lookup_returns_none_when_unmatched(self):
        self.assertIsNone(assignment_views._assignment_from_ids(999, 999))


@override_settings(SECURE_SSL_REDIRECT=False)
class TestPushEventTargeting(TestCase):
    """A push webhook must wake the daemon for that repository's students only."""

    def setUp(self):
        self.faculty = User.objects.create_user(username='prof', password='x')
        self.faculty.profile.role = UserProfile.ROLE_FACULTY
        self.faculty.profile.save()
        self.course = Course.objects.create(name='CSCI 330', owner=self.faculty.id)
        self.assignment = Assignment.objects.create(
            name='SQL 1', course=self.course, owner=self.faculty.id,
            absolute_path='1/SQL 1')
        Student.objects.create(course=self.course, email='alice@uni.edu')

    def test_push_targets_only_matching_students(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [(11, 5, 9), (12, 5, 9)]
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor

        body = b'{"project": {"git_http_url": "https://gitlab/x/sql-1-g1.git"}}'
        with mock.patch.object(assignment_views, 'connect_to_db', return_value=conn), \
                mock.patch.object(assignment_views, '_trigger_daemon') as trigger, \
                mock.patch.object(assignment_views, '_assignment_from_ids',
                                  return_value=self.assignment):
            response = self.client.post(
                '/assignments/webhook/', data=body, content_type='application/json',
                HTTP_X_GITLAB_EVENT='Push Hook', HTTP_X_GITLAB_TOKEN='tok')

        self.assertEqual(response.status_code, 200)
        trigger.assert_called_once()
        args = trigger.call_args[0]
        self.assertEqual(args[0], 5)
        self.assertEqual(args[1], 9)
        self.assertEqual(sorted(args[2]), [11, 12])

    def test_push_with_no_matching_student_does_not_start_a_pass(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor

        body = b'{"project": {"git_http_url": "https://gitlab/x/unknown.git"}}'
        with mock.patch.object(assignment_views, 'connect_to_db', return_value=conn), \
                mock.patch.object(assignment_views, '_trigger_daemon') as trigger:
            response = self.client.post(
                '/assignments/webhook/', data=body, content_type='application/json',
                HTTP_X_GITLAB_EVENT='Push Hook', HTTP_X_GITLAB_TOKEN='tok')

        self.assertEqual(response.status_code, 200)
        trigger.assert_not_called()

    def test_non_push_event_is_ignored(self):
        with mock.patch.object(assignment_views, 'connect_to_db') as connect:
            response = self.client.post('/assignments/webhook/', data=b'{}',
                                        content_type='application/json',
                                        HTTP_X_GITLAB_EVENT='Ping')
        self.assertEqual(response.status_code, 200)
        connect.assert_not_called()
