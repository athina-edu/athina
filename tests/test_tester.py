# Tests for athina.tester.tester (Tester class helpers).
import unittest
from datetime import datetime
from unittest import mock

from athina.tester.tester import Tester
from athina.users import Users
from tests.helpers import make_config


class TestTester(unittest.TestCase):
    def test_trim_test_output_short(self):
        configuration, logger = make_config()
        configuration.max_file_size = 1024
        e_learning = mock.Mock()
        repo = mock.Mock()
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        out = b"short output"
        self.assertEqual(tester._trim_test_output(out), out)

    def test_trim_test_output_long(self):
        configuration, logger = make_config()
        configuration.max_file_size = 10
        e_learning = mock.Mock()
        repo = mock.Mock()
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        out = b"a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\nn\no\np\nq\nr\ns\nt\n"
        result = tester._trim_test_output(out)
        self.assertIn(b"truncated", result)

    def test_update_user_db(self):
        configuration, logger = make_config()
        configuration.no_repo = False
        e_learning = mock.Mock()
        repo = mock.Mock()
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        user = Users.create(user_id=950, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        result = tester._update_user_db(user, datetime(2020, 1, 1, 0, 0))
        self.assertTrue(result.plagiarism_to_grade)
        self.assertFalse(result.new_url)
        self.assertEqual(result.commit_date, datetime(2020, 1, 1, 0, 0))
        user.delete_instance()

    def test_update_user_db_no_repo(self):
        configuration, logger = make_config()
        configuration.no_repo = True
        e_learning = mock.Mock()
        repo = mock.Mock()
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        user = Users.create(user_id=951, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        result = tester._update_user_db(user, datetime(2020, 1, 1, 0, 0))
        self.assertTrue(result.plagiarism_to_grade)
        user.delete_instance()

    def test_get_group_user_list_no_url(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = mock.Mock()
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        user = Users.create(user_id=952, course_id=1, assignment_id=1, repository_url=None)
        user_list = tester._get_group_user_list(user)
        self.assertEqual(len(user_list), 1)
        user.delete_instance()

    def test_get_group_user_list_with_url(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = mock.Mock()
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        u1 = Users.create(user_id=953, course_id=1, assignment_id=1,
                          repository_url="https://github.com/unique_group/y.git")
        u2 = Users.create(user_id=954, course_id=1, assignment_id=1,
                          repository_url="https://github.com/unique_group/y.git")
        user_list = tester._get_group_user_list(u1)
        self.assertEqual(len(user_list), 2)
        u1.delete_instance()
        u2.delete_instance()

    def test_check_commit_date_tested(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = mock.Mock()
        repo.retrieve_last_commit_date.return_value = datetime(2020, 1, 1, 0, 0)
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        user = Users.create(user_id=955, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        result = tester._check_commit_date_tested(user)
        self.assertEqual(result, datetime(2020, 1, 1, 0, 0))
        user.delete_instance()

    def test_check_commit_date_tested_none(self):
        configuration, logger = make_config()
        e_learning = mock.Mock()
        repo = mock.Mock()
        repo.retrieve_last_commit_date.return_value = None
        user_data = mock.Mock()
        tester = Tester(user_data, logger, configuration, e_learning, repo)
        user = Users.create(user_id=956, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        result = tester._check_commit_date_tested(user)
        self.assertEqual(result, datetime(1, 1, 1, 0, 0))
        user.delete_instance()
