import os
import subprocess
from unittest import mock, TestCase

from tests.test_athina import create_test_config, create_logger, create_fake_user_db
from athina.configuration import Configuration
from athina.tester.docker import docker_build, docker_run, _terminate_container, _terminate_all_containers, \
    _docker_chown
from tests.helpers import make_config


class TestFunctions(TestCase):
    def test_docker_build(self):
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        # Create fake directories
        create_test_config()
        user_data = create_fake_user_db()

        result = docker_build(configuration, logger)
        self.assertEqual(result, True, "The first time we visit a testing repo have to build the Dockerfile")
        result = docker_build(configuration, logger)
        self.assertEqual(result, False, "We do not rebuild if we have already built for a specific commit")


class TestDocker(TestCase):
    def test_docker_build_first(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        with mock.patch('athina.tester.docker.get_repo_commit', return_value="abc"), \
                mock.patch('athina.tester.docker.load_key_from_assignment_data', return_value=None), \
                mock.patch('athina.tester.docker.update_key_in_assignment_data'), \
                mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen:
            proc = mock_popen.return_value
            proc.returncode = 0
            proc.communicate.return_value = (b"out", b"")
            self.assertTrue(docker_build(configuration, logger))

    def test_docker_build_skip(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        with mock.patch('athina.tester.docker.get_repo_commit', return_value="abc"), \
                mock.patch('athina.tester.docker.load_key_from_assignment_data',
                           side_effect=["abc", "1"]), \
                mock.patch('athina.tester.docker.update_key_in_assignment_data'):
            self.assertFalse(docker_build(configuration, logger))

    def test_docker_build_error(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        with mock.patch('athina.tester.docker.get_repo_commit', return_value="abc"), \
                mock.patch('athina.tester.docker.load_key_from_assignment_data', return_value=None), \
                mock.patch('athina.tester.docker.update_key_in_assignment_data'), \
                mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen:
            proc = mock_popen.return_value
            proc.returncode = 1
            proc.communicate.return_value = (b"out", b"error")
            self.assertFalse(docker_build(configuration, logger))

    def test_docker_build_file_not_found_test_mode(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        os.environ['ATHINA_TEST_MODE'] = '1'
        try:
            with mock.patch('athina.tester.docker.get_repo_commit', return_value="abc"), \
                    mock.patch('athina.tester.docker.load_key_from_assignment_data', return_value=None), \
                    mock.patch('athina.tester.docker.update_key_in_assignment_data'), \
                    mock.patch('athina.tester.docker.subprocess.Popen', side_effect=FileNotFoundError):
                self.assertTrue(docker_build(configuration, logger))
        finally:
            os.environ.pop('ATHINA_TEST_MODE', None)

    def test_docker_build_unexpected_error(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        with mock.patch('athina.tester.docker.get_repo_commit', return_value="abc"), \
                mock.patch('athina.tester.docker.load_key_from_assignment_data', return_value=None), \
                mock.patch('athina.tester.docker.update_key_in_assignment_data'), \
                mock.patch('athina.tester.docker.subprocess.Popen', side_effect=RuntimeError("boom")):
            self.assertFalse(docker_build(configuration, logger))

    def test_docker_run_file_not_found_test_mode(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.athina_test_tmp_dir = "/tmp/test"
        configuration.extra_params = []
        configuration.docker_memory_limit = "2g"
        configuration.test_timeout = 10
        os.environ['ATHINA_TEST_MODE'] = '1'
        try:
            with mock.patch('athina.tester.docker.subprocess.Popen', side_effect=FileNotFoundError), \
                    mock.patch('athina.tester.docker._terminate_container'), \
                    mock.patch('athina.tester.docker._docker_chown'):
                out, err = docker_run("bash test", configuration, logger)
                self.assertIsNotNone(out)
        finally:
            os.environ.pop('ATHINA_TEST_MODE', None)

    def test_docker_run_timeout(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.athina_test_tmp_dir = "/tmp/test"
        configuration.extra_params = []
        configuration.docker_memory_limit = "2g"
        configuration.test_timeout = 10
        with mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen, \
                mock.patch('athina.tester.docker._terminate_container') as mock_term, \
                mock.patch('athina.tester.docker._docker_chown'):
            proc = mock_popen.return_value
            proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 10)
            proc.communicate.return_value = (b"out", b"")
            out, err = docker_run("bash test", configuration, logger)
            mock_term.assert_called()
            self.assertEqual(out, b"out")

    def test_docker_run_permission_denied_test_mode(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.athina_test_tmp_dir = "/tmp/test"
        configuration.extra_params = []
        configuration.docker_memory_limit = "2g"
        configuration.test_timeout = 10
        os.environ['ATHINA_TEST_MODE'] = '1'
        try:
            with mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen, \
                    mock.patch('athina.tester.docker._terminate_container'), \
                    mock.patch('athina.tester.docker._docker_chown'):
                proc = mock_popen.return_value
                proc.wait.return_value = 0
                proc.communicate.return_value = (b"out", b"permission denied")
                out, err = docker_run("bash test", configuration, logger)
                self.assertIsNotNone(out)
        finally:
            os.environ.pop('ATHINA_TEST_MODE', None)

    def test_docker_run_generic_exception(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.athina_test_tmp_dir = "/tmp/test"
        configuration.extra_params = []
        configuration.docker_memory_limit = "2g"
        configuration.test_timeout = 10
        with mock.patch('athina.tester.docker.subprocess.Popen', side_effect=RuntimeError("boom")), \
                mock.patch('athina.tester.docker._terminate_container'), \
                mock.patch('athina.tester.docker._docker_chown'):
            out, err = docker_run("bash test", configuration, logger)
            self.assertEqual(out, b"")

    def test_terminate_container(self):
        with mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen:
            _terminate_container("container1")
            mock_popen.assert_called_once()

    def test_terminate_container_file_not_found(self):
        with mock.patch('athina.tester.docker.subprocess.Popen', side_effect=FileNotFoundError):
            _terminate_container("container1")  # should not raise

    def test_terminate_all_containers(self):
        with mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen:
            _terminate_all_containers()
            mock_popen.assert_called_once()

    def test_docker_run_flag_branches(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        configuration.athina_student_code_dir = "/tmp/student"
        configuration.athina_test_tmp_dir = "/tmp/test"
        configuration.extra_params = []
        configuration.docker_memory_limit = "2g"
        configuration.test_timeout = 10
        configuration.docker_use_seccomp = False
        configuration.docker_use_net_admin = True
        configuration.docker_no_internet = True
        with mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen, \
                mock.patch('athina.tester.docker._terminate_container'), \
                mock.patch('athina.tester.docker._docker_chown'):
            proc = mock_popen.return_value
            proc.wait.return_value = 0
            proc.communicate.return_value = (b"out", b"")
            out, err = docker_run("bash test", configuration, logger)
            self.assertEqual(out, b"out")
            # Verify the flag branches were added to the run statement
            args = mock_popen.call_args[0][0]
            self.assertIn("--cap-add=SYS_PTRACE", args)
            self.assertIn("--cap-add=NET_ADMIN", args)
            self.assertIn("--network", args)

    def test_docker_chown(self):
        configuration, logger = make_config()
        configuration.config_dir = "/tmp"
        with mock.patch('athina.tester.docker.subprocess.Popen') as mock_popen:
            proc = mock_popen.return_value
            proc.communicate.return_value = (b"out", b"")
            out, err = _docker_chown(configuration, logger, "/tmp/student")
            self.assertEqual(out, b"out")
