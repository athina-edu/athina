import os
import shutil
from datetime import datetime
from unittest import mock, TestCase, skip

from athina.logger import *
from athina.moss import Plagiarism, plagiarism_checks_on_users
from athina.users import *
from tests.helpers import make_config
from tests.test_athina import create_logger


class TestFunctions(TestCase):
    @skip("Moss service hangs for too long. Implement timeouts in moss.py")
    def test_moss(self):
        shutil.rmtree("/tmp/u1", ignore_errors=True)
        shutil.rmtree("/tmp/u2", ignore_errors=True)
        shutil.rmtree("/tmp/u3", ignore_errors=True)
        os.makedirs("/tmp/u1", exist_ok=True)
        os.makedirs("/tmp/u2", exist_ok=True)
        os.makedirs("/tmp/u3", exist_ok=True)
        f = open("/tmp/u1/test.py", 'a')
        f.write("print(1)\nprint(12345)")
        f.close()
        f = open("/tmp/u2/test.py", 'a')
        f.write("print(1)\nprint(54321)")
        f.close()
        f = open("/tmp/u3/test.py", 'a')
        f.write("a=9875\nprint(a)")
        f.close()

        filename = "tests/user_data.sqlite3"
        if os.path.isfile(filename):
            os.remove(filename)
        user_data = Database()
        logger = self.create_logger()
        x = Plagiarism(logger=logger,
                       service_type="moss",
                       moss_id=20181579,  # Registered by Michael Tsikerdekis - Michael.Tsikerdekis@wwu.edu. Do not use.
                       moss_lang="python")
        data = x.check_plagiarism(["/tmp/u1/*.py",
                                   "/tmp/u2/*.py",
                                   "/tmp/u3/*.py"])
        self.assertEqual({1: [75], 2: [75]}, data)

    @staticmethod
    def create_logger():
        logger = Logger()
        logger.set_verbose(True)
        logger.set_debug(True)
        return logger


class TestPlagiarism(TestCase):
    def test_plagiarism_init_moss(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="moss", moss_id=123, moss_lang="python")
        self.assertEqual(p.service_type, "moss")
        self.assertEqual(p.moss_id, 123)

    def test_plagiarism_init_missing_params(self):
        logger = create_logger()
        with self.assertRaises(KeyError):
            Plagiarism(logger=logger, service_type="moss")

    def test_plagiarism_init_other_service(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="other")
        self.assertIsNone(p.service_type)

    def test_plagiarism_init_copydetect(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="copydetect", threshold=0.5)
        self.assertEqual(p.service_type, "copydetect")
        self.assertEqual(p.threshold, 0.5)

    def test_plagiarism_init_copydetect_default_threshold(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="copydetect")
        self.assertEqual(p.service_type, "copydetect")
        self.assertEqual(p.threshold, 0.33)

    def test_check_plagiarism_copydetect_empty(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="copydetect")
        self.assertEqual(p.check_plagiarism([], 1, 1), dict())

    def test_check_plagiarism_empty_folder_list(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="moss", moss_id=123, moss_lang="python")
        self.assertEqual(p.check_plagiarism([], 1, 1), dict())

    def test_check_plagiarism_other_service(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="other")
        self.assertEqual(p.check_plagiarism(["/tmp/*.py"], 1, 1), dict())

    def test_check_plagiarism_moss(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="moss", moss_id=123, moss_lang="python")
        moss_html = '''<TR>
<TD><A HREF="http://moss.stanford.edu/results/123/u1/x.py">u1/x.py</A>
<TD>75%
<TD><A HREF="http://moss.stanford.edu/results/123/u2/y.py">u2/y.py</A>
<TD>50%
</TR>'''
        with mock.patch('athina.moss.mosspy.Moss') as mock_moss, \
                mock.patch('athina.moss.request_url', return_value=moss_html), \
                mock.patch('athina.moss.update_key_in_assignment_data'):
            instance = mock_moss.return_value
            instance.send.return_value = "http://moss.url"
            result = p.check_plagiarism(["/tmp/*.py"], 1, 1)
            self.assertIn(1, result)
            self.assertIn(2, result)

    def test_check_plagiarism_moss_error(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="moss", moss_id=123, moss_lang="python")
        with mock.patch('athina.moss.mosspy.Moss') as mock_moss, \
                mock.patch.object(logger.logger, 'error') as mock_err:
            instance = mock_moss.return_value
            instance.send.side_effect = Exception("boom")
            result = p.check_plagiarism(["/tmp/*.py"], 1, 1)
            self.assertEqual(result, dict())
            mock_err.assert_called()

    def test_parse_comparison_time_new(self):
        comparisons = {}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [75]})

    def test_parse_comparison_time_existing(self):
        comparisons = {1: [50]}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [50, 75]})

    def test_plagiarism_checks_on_users(self):
        configuration, logger = make_config()
        configuration.moss_id = 123
        configuration.moss_lang = "python"
        configuration.moss_pattern = "*.py"
        configuration.moss_publish = True
        configuration.config_dir = "/tmp"
        configuration.assignment_id = 1
        configuration.course_id = 1

        e_learning = mock.Mock()
        user = Users.create(user_id=555, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git",
                            plagiarism_to_grade=True,
                            last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        with mock.patch('athina.moss.return_all_students',
                        return_value=Users.select().where(Users.course_id == 1, Users.assignment_id == 1)), \
                mock.patch('athina.moss.Plagiarism') as mock_plag, \
                mock.patch('athina.moss.os.path.isdir', return_value=True), \
                mock.patch('athina.moss.glob.glob', return_value=["/tmp/repodata1/u555/*.py"]):
            instance = mock_plag.return_value
            instance.check_plagiarism.return_value = {555: [75]}
            results = plagiarism_checks_on_users(logger, configuration, e_learning)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], 555)
        user.delete_instance()

    def test_plagiarism_checks_on_users_no_users(self):
        configuration, logger = make_config()
        configuration.moss_id = 123
        configuration.moss_lang = "python"
        configuration.moss_pattern = "*.py"
        configuration.config_dir = "/tmp"
        configuration.assignment_id = 1
        configuration.course_id = 1
        e_learning = mock.Mock()
        with mock.patch('athina.moss.return_all_students', return_value=[]):
            results = plagiarism_checks_on_users(logger, configuration, e_learning)
            self.assertEqual(results, [])

