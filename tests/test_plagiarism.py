import os
import shutil
from datetime import datetime
from unittest import mock, TestCase, skip

from athina.logger import *
from athina.plagiarism import Plagiarism, plagiarism_checks_on_users
from athina.users import *
from tests.helpers import make_config
from tests.test_athina import create_logger


class TestFunctions(TestCase):
    @skip("CopyDetect requires actual student directories with code files")
    def test_copydetect_integration(self):
        """Integration test — skipped by default, requires real directories."""
        shutil.rmtree("/tmp/u1", ignore_errors=True)
        shutil.rmtree("/tmp/u2", ignore_errors=True)
        shutil.rmtree("/tmp/u3", ignore_errors=True)
        os.makedirs("/tmp/u1", exist_ok=True)
        os.makedirs("/tmp/u2", exist_ok=True)
        os.makedirs("/tmp/u3", exist_ok=True)
        with open("/tmp/u1/test.py", 'w') as f:
            f.write("print(1)\nprint(12345)")
        with open("/tmp/u2/test.py", 'w') as f:
            f.write("print(1)\nprint(54321)")
        with open("/tmp/u3/test.py", 'w') as f:
            f.write("a=9875\nprint(a)")

        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="copydetect", threshold=0.33)
        data = p.check_plagiarism(["/tmp/u1/*.py", "/tmp/u2/*.py", "/tmp/u3/*.py"], 1, 1)
        self.assertIsInstance(data, dict)


class TestPlagiarism(TestCase):
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

    def test_plagiarism_init_other_service(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="other")
        self.assertIsNone(p.service_type)

    def test_plagiarism_init_none_service(self):
        logger = create_logger()
        p = Plagiarism(logger=logger)
        self.assertIsNone(p.service_type)

    def test_check_plagiarism_copydetect_empty(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="copydetect")
        self.assertEqual(p.check_plagiarism([], 1, 1), dict())

    def test_check_plagiarism_other_service(self):
        logger = create_logger()
        p = Plagiarism(logger=logger, service_type="other")
        self.assertEqual(p.check_plagiarism(["/tmp/*.py"], 1, 1), dict())

    def test_parse_comparison_time_new(self):
        comparisons = {}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [75]})

    def test_parse_comparison_time_existing(self):
        comparisons = {1: [50]}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [50, 75]})

    def test_plagiarism_checks_on_users_no_users(self):
        configuration, logger = make_config()
        configuration.plagiarism_service = "copydetect"
        configuration.plagiarism_pattern = "*.py"
        configuration.config_dir = "/tmp"
        configuration.assignment_id = 1
        configuration.course_id = 1
        e_learning = mock.Mock()
        with mock.patch('athina.plagiarism.return_all_students', return_value=[]):
            results = plagiarism_checks_on_users(logger, configuration, e_learning)
            self.assertEqual(results, [])
