# Tests for athina.git.git (Repository class + git helpers).
import unittest
from datetime import datetime
from unittest import mock

from athina.git.git import make_proper_git_url, get_repo_commit, Repository
from athina.users import Database, Users, AssignmentData, return_a_student
from tests.helpers import make_config


class TestGit(unittest.TestCase):
    def setUp(self):
        user_data = Database()
        user_data.db.drop_tables([Users, AssignmentData])
        user_data.db.create_tables([Users, AssignmentData])

    def test_make_proper_git_url_with_git(self):
        self.assertEqual(make_proper_git_url("https://x/y.git"), "https://x/y.git")

    def test_make_proper_git_url_without_git(self):
        self.assertEqual(make_proper_git_url("https://x/y"), "https://x/y.git")

    def test_make_proper_git_url_non_string(self):
        self.assertIsNone(make_proper_git_url(None))

    def test_get_repo_commit_invalid(self):
        self.assertIsNone(get_repo_commit("/tmp/not_a_repo_xyz"))

    def test_check_error_empty(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        self.assertIsNone(repo.check_error(b""))

    def test_check_error_non_empty(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        self.assertTrue(repo.check_error(b"some error"))

    def test_retrieve_last_commit_date_file_not_found(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        with mock.patch.object(repo, '_retrieve_git_log', side_effect=FileNotFoundError):
            self.assertIsNone(repo.retrieve_last_commit_date(1))

    def test_retrieve_last_commit_date_error(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        with mock.patch.object(repo, '_retrieve_git_log', return_value=(b"", b"error")):
            self.assertIsNone(repo.retrieve_last_commit_date(1))

    def test_retrieve_last_commit_date_success(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        with mock.patch.object(repo, '_retrieve_git_log',
                               return_value=(b"2019-01-01 00:00:00 +0000", b"")):
            result = repo.retrieve_last_commit_date(1)
            self.assertIsNotNone(result)

    def test_submit_will_not_process(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=777, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        repo.submit_will_not_process(user, "msg")
        e_learning.submit_grade.assert_called_once()
        self.assertFalse(return_a_student(1, 1, 777).new_url)
        user.delete_instance()

    def test_compare_commit_date_with_due_date_new(self):
        configuration, logger = make_config()
        configuration.due_date = datetime(2100, 1, 1, 0, 0)
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=778, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git",
                            commit_date=datetime(2000, 1, 1, 0, 0))
        with mock.patch.object(repo, 'retrieve_last_commit_date',
                               return_value=datetime(2050, 1, 1, 0, 0)):
            self.assertTrue(repo.compare_commit_date_with_due_date(778, user))
        user.delete_instance()

    def test_compare_commit_date_with_due_date_force(self):
        configuration, logger = make_config()
        configuration.due_date = datetime(2100, 1, 1, 0, 0)
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=779, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git",
                            commit_date=datetime(2000, 1, 1, 0, 0),
                            force_test=True)
        with mock.patch.object(repo, 'retrieve_last_commit_date',
                               return_value=datetime(2000, 1, 1, 0, 0)):
            self.assertTrue(repo.compare_commit_date_with_due_date(779, user))
        user.delete_instance()

    def test_compare_commit_date_with_due_date_false(self):
        configuration, logger = make_config()
        configuration.due_date = datetime(2100, 1, 1, 0, 0)
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=780, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git",
                            commit_date=datetime(2000, 1, 1, 0, 0))
        with mock.patch.object(repo, 'retrieve_last_commit_date',
                               return_value=datetime(2000, 1, 1, 0, 0)):
            self.assertFalse(repo.compare_commit_date_with_due_date(780, user))
        user.delete_instance()

    def test_pull_git_repo_file_not_found(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        with mock.patch('athina.git.git.subprocess.Popen', side_effect=FileNotFoundError):
            out, err = repo.pull_git_repo(1)
            self.assertIn(b"unresolved conflict", err)

    def test_clone_git_repo_wrong_domain(self):
        configuration, logger = make_config()
        configuration.git_url = "github.com"
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=781, course_id=1, assignment_id=1,
                            repository_url="https://evil.com/x/y.git")
        with mock.patch('athina.git.git.subprocess.run') as mock_run, \
                mock.patch('athina.git.git.time.sleep'):
            repo.clone_git_repo(781, user)
            self.assertTrue(mock_run.called)
        user.delete_instance()

    def test_clone_git_repo_with_credentials(self):
        configuration, logger = make_config()
        configuration.git_url = "github.com"
        configuration.git_username = "user"
        configuration.git_password = "pass"
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=782, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        with mock.patch('athina.git.git.subprocess.run') as mock_run, \
                mock.patch('athina.git.git.time.sleep'):
            repo.clone_git_repo(782, user)
            self.assertTrue(mock_run.called)
        user.delete_instance()

    def test_clone_git_repo_webhook(self):
        configuration, logger = make_config()
        configuration.git_url = "github.com"
        configuration.git_username = "user"
        configuration.git_password = "pass"
        configuration.use_webhook = True
        e_learning = mock.Mock()
        repo = Repository(logger, configuration, e_learning)
        user = Users.create(user_id=783, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        with mock.patch('athina.git.git.subprocess.run'), \
                mock.patch('athina.git.git.time.sleep'), \
                mock.patch('athina.git.git.gitlab_set_webhook') as mock_wh:
            repo.clone_git_repo(783, user)
            mock_wh.assert_called_once()
        user.delete_instance()
