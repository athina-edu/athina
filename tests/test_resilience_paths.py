"""Tests for resilience paths around git and docker execution.

These cover the defensive branches that keep grading alive when a student's
repo has no .git directory, or when Docker is unavailable and the engine is
running in test mode (ATHINA_TEST_MODE=1), where it falls back to local
execution.
"""
import os
import subprocess
import tempfile
from unittest import TestCase, mock

from athina.git.git import Repository
from athina.tester.docker import _run_local_test, docker_run
from athina.users import Database
from tests.helpers import make_config


def _docker_config():
    configuration, logger = make_config()
    configuration.config_dir = tempfile.mkdtemp()
    configuration.athina_test_tmp_dir = configuration.config_dir
    configuration.athina_student_code_dir = configuration.config_dir
    configuration.test_timeout = 10
    configuration.docker_memory_limit = "1g"
    configuration.extra_params = []
    configuration.docker_use_seccomp = True
    configuration.docker_use_net_admin = False
    configuration.docker_no_internet = False
    return configuration, logger


class TestGitLogWithoutGitDir(TestCase):
    """A repo dir lacking .git must not be shelled out to git."""

    def setUp(self):
        Database().connect_to_db()
        self.tmpdir = tempfile.mkdtemp()
        self.configuration, self.logger = make_config()
        self.configuration.config_dir = self.tmpdir
        self.configuration.assignment_id = 1
        self.configuration.course_id = 1
        self.repository = Repository(self.logger, self.configuration, mock.Mock())

    def test_missing_git_dir_returns_error_without_shelling_out(self):
        os.makedirs("%s/repodata1/u950" % self.tmpdir, exist_ok=True)
        with mock.patch('athina.git.git.subprocess.Popen') as popen, \
                mock.patch.object(self.logger.logger, 'warning') as warn:
            out, err = self.repository._retrieve_git_log(950)
        popen.assert_not_called()
        self.assertEqual(out, b"")
        self.assertEqual(err, b"no .git directory")
        warn.assert_called_once()

    def test_missing_git_dir_makes_commit_date_none(self):
        os.makedirs("%s/repodata1/u952" % self.tmpdir, exist_ok=True)
        self.assertIsNone(self.repository.retrieve_last_commit_date(952))

    def test_present_git_dir_runs_git_log(self):
        os.makedirs("%s/repodata1/u951/.git" % self.tmpdir, exist_ok=True)
        proc = mock.Mock()
        proc.communicate.return_value = (b"2020-01-02 03:04:05 +0000", b"")
        with mock.patch('athina.git.git.subprocess.Popen', return_value=proc) as popen:
            out, err = self.repository._retrieve_git_log(951)
        popen.assert_called_once()
        self.assertIn(b"2020-01-02", out)


class TestLocalTestFallback(TestCase):
    """_run_local_test is the non-Docker fallback used in test mode."""

    def test_returns_process_output(self):
        configuration, logger = _docker_config()
        proc = mock.Mock()
        proc.communicate.return_value = (b"stdout", b"stderr")
        with mock.patch('athina.tester.docker.subprocess.Popen', return_value=proc):
            out, err = _run_local_test(configuration, logger)
        self.assertEqual((out, err), (b"stdout", b"stderr"))

    def test_timeout_kills_process_and_warns(self):
        configuration, logger = _docker_config()
        proc = mock.Mock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)
        proc.communicate.return_value = (b"", b"")
        with mock.patch('athina.tester.docker.subprocess.Popen', return_value=proc), \
                mock.patch.object(logger.logger, 'warning') as warn:
            _run_local_test(configuration, logger)
        proc.kill.assert_called_once()
        warn.assert_called_once()


class TestDockerFallbackToLocal(TestCase):
    """Permission/daemon/image errors fall back to local execution in test mode."""

    def _failed_proc(self, stderr):
        proc = mock.Mock()
        proc.communicate.return_value = (b"", stderr)
        return proc

    def _test_mode_patches(self, stderr):
        """Patch docker_run's collaborators, with local fallback succeeding."""
        return mock.patch.multiple(
            'athina.tester.docker',
            subprocess=mock.Mock(Popen=mock.Mock(return_value=self._failed_proc(stderr))),
            _run_local_test=mock.Mock(return_value=(b"local out", b"")),
            _terminate_container=mock.Mock(),
            _docker_chown=mock.Mock(),
        )

    def test_permission_denied_falls_back_in_test_mode(self):
        configuration, logger = _docker_config()
        with mock.patch.dict(os.environ, {'ATHINA_TEST_MODE': '1'}), \
                self._test_mode_patches(b"permission denied"), \
                mock.patch('athina.tester.docker._run_local_test',
                           return_value=(b"local out", b"")) as local:
            out, _ = docker_run("s", configuration, logger)
        local.assert_called_once()
        self.assertEqual(out, b"local out")

    def test_daemon_error_falls_back_in_test_mode(self):
        configuration, logger = _docker_config()
        with mock.patch.dict(os.environ, {'ATHINA_TEST_MODE': '1'}), \
                mock.patch('athina.tester.docker.subprocess.Popen',
                           return_value=self._failed_proc(b"cannot connect to the docker daemon")), \
                mock.patch('athina.tester.docker._run_local_test',
                           return_value=(b"local out", b"")) as local, \
                mock.patch('athina.tester.docker._terminate_container'), \
                mock.patch('athina.tester.docker._docker_chown'):
            out, _ = docker_run("s", configuration, logger)
        local.assert_called_once()
        self.assertEqual(out, b"local out")

    def test_image_pull_denied_falls_back_in_test_mode(self):
        configuration, logger = _docker_config()
        with mock.patch.dict(os.environ, {'ATHINA_TEST_MODE': '1'}), \
                mock.patch('athina.tester.docker.subprocess.Popen',
                           return_value=self._failed_proc(b"pull access denied for image")), \
                mock.patch('athina.tester.docker._run_local_test',
                           return_value=(b"local out", b"")) as local, \
                mock.patch('athina.tester.docker._terminate_container'), \
                mock.patch('athina.tester.docker._docker_chown'):
            docker_run("s", configuration, logger)
        local.assert_called_once()

    def test_no_fallback_outside_test_mode(self):
        configuration, logger = _docker_config()
        env = {k: v for k, v in os.environ.items() if k != 'ATHINA_TEST_MODE'}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch('athina.tester.docker.subprocess.Popen',
                           return_value=self._failed_proc(b"permission denied")), \
                mock.patch('athina.tester.docker._run_local_test') as local, \
                mock.patch('athina.tester.docker._terminate_container'), \
                mock.patch('athina.tester.docker._docker_chown'):
            docker_run("s", configuration, logger)
        local.assert_not_called()

    def test_fallback_error_is_captured_not_raised(self):
        configuration, logger = _docker_config()
        with mock.patch.dict(os.environ, {'ATHINA_TEST_MODE': '1'}), \
                mock.patch('athina.tester.docker.subprocess.Popen',
                           return_value=self._failed_proc(b"permission denied")), \
                mock.patch('athina.tester.docker._run_local_test',
                           side_effect=Exception("local also failed")), \
                mock.patch('athina.tester.docker._terminate_container'), \
                mock.patch('athina.tester.docker._docker_chown'):
            out, err = docker_run("s", configuration, logger)
        self.assertEqual(out, b"")
        self.assertIn(b"local also failed", err)

    def test_missing_docker_binary_reraises_outside_test_mode(self):
        configuration, logger = _docker_config()
        env = {k: v for k, v in os.environ.items() if k != 'ATHINA_TEST_MODE'}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch('athina.tester.docker.subprocess.Popen',
                           side_effect=FileNotFoundError("no docker")):
            with self.assertRaises(FileNotFoundError):
                docker_run("s", configuration, logger)
