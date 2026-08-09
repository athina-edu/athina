# Tests for athina.tester.firejail (firejail sandboxing).
import os
from unittest import mock, TestCase

from athina.tester.firejail import execute_with_firejail, generate_firejail_profile
from tests.helpers import make_config


class TestFirejail(TestCase):
    def test_execute_with_firejail(self):
        configuration, logger = make_config()
        configuration.athina_test_tmp_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.test_timeout = 10
        configuration.extra_params = []
        with mock.patch('athina.tester.firejail.generate_firejail_profile'), \
                mock.patch('athina.tester.firejail.subprocess.Popen') as mock_popen:
            proc = mock_popen.return_value
            proc.communicate.return_value = (b"out", b"")
            out, err = execute_with_firejail(configuration, "bash test", logger)
            self.assertEqual(out, b"out")

    def test_execute_with_firejail_file_not_found_test_mode(self):
        configuration, logger = make_config()
        configuration.athina_test_tmp_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.test_timeout = 10
        configuration.extra_params = []
        os.environ['ATHINA_TEST_MODE'] = '1'
        try:
            with mock.patch('athina.tester.firejail.generate_firejail_profile'), \
                    mock.patch('athina.tester.firejail.subprocess.Popen', side_effect=FileNotFoundError):
                out, err = execute_with_firejail(configuration, "bash test", logger)
                self.assertIsNotNone(out)
        finally:
            os.environ.pop('ATHINA_TEST_MODE', None)

    def test_execute_with_firejail_failed_to_run_test_mode(self):
        configuration, logger = make_config()
        configuration.athina_test_tmp_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.test_timeout = 10
        configuration.extra_params = []
        os.environ['ATHINA_TEST_MODE'] = '1'
        try:
            with mock.patch('athina.tester.firejail.generate_firejail_profile'), \
                    mock.patch('athina.tester.firejail.subprocess.Popen') as mock_popen:
                proc = mock_popen.return_value
                proc.communicate.return_value = (b"out", b"firejail: failed to run command")
                out, err = execute_with_firejail(configuration, "bash test", logger)
                self.assertIsNotNone(out)
        finally:
            os.environ.pop('ATHINA_TEST_MODE', None)

    def test_generate_firejail_profile(self):
        filename = "/tmp/cov_server.profile"
        if os.path.exists(filename):
            os.remove(filename)
        generate_firejail_profile(filename)
        self.assertTrue(os.path.exists(filename))
        os.remove(filename)

    def test_generate_firejail_profile_missing_source(self):
        filename = "/tmp/cov_server_missing.profile"
        if os.path.exists(filename):
            os.remove(filename)
        with mock.patch('athina.tester.firejail.os.path.dirname', return_value="/tmp/does_not_exist"):
            generate_firejail_profile(filename)
        self.assertTrue(os.path.exists(filename))
        os.remove(filename)
