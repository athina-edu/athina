"""Integration tests for Tester.process_student_assignment.

These drive the real orchestration path (DB lookup, lock handling, grade
submission, report storage) while stubbing out the two things that need
external services: the per-test runner and the CPU/memory throttle.
"""
import os
import tempfile
from datetime import datetime
from unittest import TestCase, mock

from athina.tester.tester import Tester
from athina.users import Database, Users
from tests.helpers import make_config


class TestProcessStudentAssignment(TestCase):
    def setUp(self):
        Database().connect_to_db()
        self.tmpdir = tempfile.mkdtemp()
        self.configuration, self.logger = make_config()
        self.configuration.course_id = 1
        self.configuration.assignment_id = 1
        self.configuration.config_dir = self.tmpdir
        self.configuration.total_points = 100
        self.configuration.test_scripts = ["bash test"]
        self.configuration.test_weights = [1.0]
        self.configuration.no_repo = True
        self.configuration.llm_enabled = False
        self.configuration.output_method = "canvas"
        self.e_learning = mock.Mock()
        # submit_grade returns an issue IID that gets stored as an integer.
        self.e_learning.submit_grade.return_value = 0
        self.repository = mock.Mock()
        self.repository.retrieve_last_commit_date.return_value = datetime(2020, 1, 1)

    def tearDown(self):
        Users.delete().where(Users.course_id == 1, Users.assignment_id == 1).execute()

    def _tester(self, test_grade=0.9):
        tester = Tester(Database(), self.logger, self.configuration,
                        self.e_learning, self.repository)
        tester._run_test = lambda x, reports, grades, user: (
            reports.append(b"test report output"), grades.append(test_grade))
        return tester

    def _student(self, user_id=900, **overrides):
        fields = dict(user_id=user_id, course_id=1, assignment_id=1,
                      repository_url="https://github.com/x/y.git",
                      user_fullname="Alice", force_test=True)
        fields.update(overrides)
        return Users.create(**fields)

    # ---- CPU throttle -------------------------------------------------

    def test_waits_while_cpu_is_busy(self):
        self._student()
        tester = self._tester()
        # CPU busy on the first check, free afterwards.
        with mock.patch('athina.tester.tester.psutil.cpu_percent',
                        side_effect=[99, 1]), \
                mock.patch('athina.tester.tester.time.sleep') as mock_sleep:
            tester.process_student_assignment(900)
        mock_sleep.assert_called_once()

    # ---- Guard clauses -------------------------------------------------

    def test_skips_when_no_changes_and_not_due(self):
        self._student(force_test=False, changed_state=False, last_graded=datetime.now())
        tester = self._tester()
        with mock.patch.object(tester, '_run_test') as mock_run:
            result = tester.process_student_assignment(900)
        mock_run.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].changed_state is False)

    def test_does_not_clear_force_test_when_skipping(self):
        """force_test must survive so a later cycle still runs the test."""
        self._student(force_test=False, changed_state=False, last_graded=datetime.now())
        tester = self._tester()
        tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        self.assertFalse(refreshed.force_test)

    # ---- Happy path ----------------------------------------------------

    def test_runs_test_and_records_grade(self):
        self._student()
        tester = self._tester(test_grade=0.9)
        result = tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        self.assertEqual(refreshed.last_grade, 90)
        self.assertFalse(refreshed.changed_state)
        self.assertFalse(refreshed.force_test)
        self.assertEqual(len(result), 1)

    def test_stores_raw_report_without_llm_feedback(self):
        self._student()
        tester = self._tester()
        tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        # last_report is a BlobField, so it reads back as bytes.
        self.assertIn(b"test report output", refreshed.last_report)
        self.assertNotIn(b"LLM Feedback:", refreshed.last_report)

    def test_submits_grade_when_publish_enabled(self):
        self._student()
        self.configuration.grade_publish = True
        self.e_learning.submit_grade.return_value = 12
        tester = self._tester()
        tester.process_student_assignment(900)
        self.e_learning.submit_grade.assert_called_once()
        refreshed = Users.get(Users.user_id == 900)
        self.assertEqual(refreshed.gitlab_issue_iid, 12)

    def test_logs_instead_of_submitting_when_publish_disabled(self):
        self._student()
        self.configuration.grade_publish = False
        tester = self._tester()
        tester.process_student_assignment(900)
        self.e_learning.submit_grade.assert_not_called()

    def test_marks_plagiarism_to_grade(self):
        self._student()
        tester = self._tester()
        tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        self.assertTrue(refreshed.plagiarism_to_grade)

    def test_releases_tester_lock(self):
        self._student()
        tester = self._tester()
        tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        self.assertFalse(refreshed.tester_active)

    # ---- LLM feedback --------------------------------------------------

    def test_llm_guidance_is_generated_and_stored(self):
        self._student()
        self.configuration.llm_enabled = True
        tester = self._tester()
        with mock.patch('athina.tester.tester.generate_llm_feedback',
                        return_value="Check your loop."):
            tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        self.assertEqual(refreshed.llm_guidance, "Check your loop.")

    def test_llm_guidance_is_excluded_from_raw_report(self):
        self._student()
        self.configuration.llm_enabled = True
        tester = self._tester()
        with mock.patch('athina.tester.tester.generate_llm_feedback',
                        return_value="Check your loop."):
            tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        # Stored separately for the web modal, not duplicated in last_report.
        self.assertNotIn(b"LLM Feedback:", refreshed.last_report)

    def test_llm_failure_does_not_abort_grading(self):
        self._student()
        self.configuration.llm_enabled = True
        tester = self._tester()
        with mock.patch('athina.tester.tester.generate_llm_feedback',
                        side_effect=Exception("llm down")):
            tester.process_student_assignment(900)
        refreshed = Users.get(Users.user_id == 900)
        self.assertEqual(refreshed.last_grade, 90)  # grading still happened
        self.assertFalse(refreshed.llm_guidance)

    # ---- GitLab issues mode -------------------------------------------

    def test_gitlab_mode_creates_initial_issue(self):
        from athina.gitlab_issues import GitLabIssues

        self._student()
        self.configuration.output_method = "gitlab_issues"
        self.e_learning = GitLabIssues(self.configuration, self.logger)
        tester = self._tester()
        with mock.patch.object(self.e_learning, 'create_initial_issue', return_value=5) \
                as mock_create, \
                mock.patch.object(self.e_learning, 'update_issue', return_value=True) \
                as mock_update:
            tester.process_student_assignment(900)
        mock_create.assert_called_once()
        mock_update.assert_called_once()
        refreshed = Users.get(Users.user_id == 900)
        self.assertEqual(refreshed.gitlab_issue_iid, 5)

    def test_gitlab_mode_skips_issue_when_repo_unparseable(self):
        from athina.gitlab_issues import GitLabIssues

        self._student(repository_url="")
        self.configuration.output_method = "gitlab_issues"
        self.e_learning = GitLabIssues(self.configuration, self.logger)
        tester = self._tester()
        with mock.patch.object(self.e_learning, 'create_initial_issue') as mock_create:
            tester.process_student_assignment(900)
        mock_create.assert_not_called()

    # ---- Docker prebuild ----------------------------------------------

    def test_builds_docker_image_when_dockerfile_present(self):
        self._student()
        with open(os.path.join(self.tmpdir, "Dockerfile"), "w") as handle:
            handle.write("FROM scratch")
        tester = self._tester()
        with mock.patch('athina.tester.tester.docker_build') as mock_build:
            tester.process_student_assignment(900)
        mock_build.assert_called_once()

    def test_no_docker_build_without_dockerfile(self):
        self._student()
        tester = self._tester()
        with mock.patch('athina.tester.tester.docker_build') as mock_build:
            tester.process_student_assignment(900)
        mock_build.assert_not_called()

    # ---- Group assignments --------------------------------------------

    def test_group_members_sharing_repo_are_graded(self):
        self._student(user_id=901, user_fullname="Alice")
        self._student(user_id=902, user_fullname="Bob")
        tester = self._tester()
        result = tester.process_student_assignment(901)
        self.assertEqual(len(result), 2)
        self.assertEqual(Users.get(Users.user_id == 902).last_grade, 90)

    def test_group_grade_submitted_once_when_group_assignment(self):
        self._student(user_id=903)
        self._student(user_id=904)
        self.configuration.group_assignment = True
        self.configuration.grade_publish = True
        tester = self._tester()
        tester.process_student_assignment(903)
        self.assertEqual(self.e_learning.submit_grade.call_count, 1)
