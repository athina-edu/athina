# Tests for athina.cli (CLI entry point).
from unittest import mock, TestCase

from athina.cli import lock_process, signal_handler, parse_command_line, main, core_iteration, run
from tests.helpers import make_config


class TestCli(TestCase):
    def test_run(self):
        with mock.patch('athina.cli.parse_command_line') as mock_parse, \
                mock.patch('athina.cli.lock_process') as mock_lock, \
                mock.patch('athina.cli.Logger') as mock_logger_cls, \
                mock.patch('athina.cli.signal.signal'), \
                mock.patch('athina.cli.main') as mock_main:
            mock_parse.return_value.service = False
            mock_parse.return_value.verbose = False
            run()
            mock_main.assert_called_once()
            mock_lock.return_value.release.assert_called_once()

    def test_run_service(self):
        with mock.patch('athina.cli.parse_command_line') as mock_parse, \
                mock.patch('athina.cli.lock_process') as mock_lock, \
                mock.patch('athina.cli.Logger') as mock_logger_cls, \
                mock.patch('athina.cli.signal.signal'), \
                mock.patch('athina.cli.main') as mock_main, \
                mock.patch('athina.cli.time.sleep', side_effect=KeyboardInterrupt):
            mock_parse.return_value.service = True
            mock_parse.return_value.verbose = False
            with self.assertRaises(KeyboardInterrupt):
                run()

    def test_lock_process_success(self):
        with mock.patch('athina.cli.filelock.FileLock') as mock_lock:
            instance = mock_lock.return_value
            result = lock_process()
            self.assertEqual(result, instance)
            instance.acquire.assert_called_once_with(timeout=10)

    def test_lock_process_timeout(self):
        import filelock
        with mock.patch('athina.cli.filelock.FileLock') as mock_lock, \
                mock.patch('athina.cli.sys.exit') as mock_exit:
            instance = mock_lock.return_value
            instance.acquire.side_effect = filelock.Timeout("/run/lock/athina.py.lock")
            lock_process()
            mock_exit.assert_called_once()

    def test_signal_handler(self):
        with mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.lock') as mock_lock, \
                mock.patch('athina.cli.sys.exit') as mock_exit:
            signal_handler(None, None)
            mock_logger.logger.info.assert_called_once()
            mock_lock.release.assert_called_once()
            mock_exit.assert_called_once_with(0)

    def test_parse_command_line(self):
        with mock.patch('athina.cli.sys.argv', ['athina-cli', '-c', '/tmp/config', '-v']):
            args = parse_command_line()
            self.assertEqual(args.config, '/tmp/config')
            self.assertTrue(args.verbose)

    def test_main_with_config(self):
        with mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.Database') as mock_db, \
                mock.patch('athina.cli.Configuration') as mock_config, \
                mock.patch('athina.cli.core_iteration') as mock_core:
            mock_args.json = None
            mock_args.config = '/tmp/config'
            main()
            mock_core.assert_called_once()

    def test_main_with_json(self):
        with mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.Database') as mock_db, \
                mock.patch('athina.cli.Configuration') as mock_config, \
                mock.patch('athina.cli.core_iteration') as mock_core, \
                mock.patch('athina.cli.request_url', return_value=[{'directory': '/tmp/a'}]):
            mock_args.json = 'http://example.com/api'
            mock_args.config = None
            main()
            self.assertEqual(mock_core.call_count, 1)

    def test_main_no_args_raises(self):
        with mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.Database') as mock_db:
            mock_args.json = None
            mock_args.config = None
            with self.assertRaises(SyntaxError):
                main()

    def test_main_config_error_continues(self):
        with mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.Database') as mock_db, \
                mock.patch('athina.cli.Configuration') as mock_config, \
                mock.patch('athina.cli.core_iteration') as mock_core:
            mock_args.json = None
            mock_args.config = '/tmp/config'
            config_instance = mock_config.return_value
            config_instance.load_configuration.side_effect = ValueError("bad")
            main()
            mock_core.assert_not_called()

    def test_core_iteration_no_auth(self):
        configuration, logger = make_config()
        configuration.auth_token = ""
        with mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.Canvas') as mock_canvas, \
                mock.patch('athina.cli.Repository') as mock_repo, \
                mock.patch('athina.cli.Tester') as mock_tester:
            mock_args.repo_url_testing = None
            user_data = mock.Mock()
            core_iteration(configuration, user_data)
            mock_canvas.assert_called_once()
            mock_tester.return_value.start_testing_db.assert_called_once()

    def test_core_iteration_with_auth(self):
        configuration, logger = make_config()
        configuration.auth_token = "token"
        configuration.enforce_due_date = True
        configuration.check_plagiarism_hour = 99  # won't match current hour
        with mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.Canvas') as mock_canvas, \
                mock.patch('athina.cli.Repository') as mock_repo, \
                mock.patch('athina.cli.Tester') as mock_tester, \
                mock.patch('athina.cli.return_all_students', return_value=[mock.Mock()]), \
                mock.patch('athina.cli.os.chmod'):
            mock_args.repo_url_testing = None
            e_learning = mock_canvas.return_value
            e_learning.needs_update = True
            user_data = mock.Mock()
            core_iteration(configuration, user_data)
            e_learning.get_all_submissions.assert_called_once()
            e_learning.get_additional_user_info.assert_called_once()
            e_learning.get_assignment_due_date.assert_called_once()
            e_learning.update_last_update.assert_called_once()

    def test_core_iteration_with_auth_no_update(self):
        configuration, logger = make_config()
        configuration.auth_token = "token"
        configuration.enforce_due_date = False
        configuration.check_plagiarism_hour = 99
        with mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.Canvas') as mock_canvas, \
                mock.patch('athina.cli.Repository') as mock_repo, \
                mock.patch('athina.cli.Tester') as mock_tester, \
                mock.patch('athina.cli.return_all_students', return_value=[mock.Mock()]), \
                mock.patch('athina.cli.os.chmod'):
            mock_args.repo_url_testing = None
            e_learning = mock_canvas.return_value
            e_learning.needs_update = False
            user_data = mock.Mock()
            core_iteration(configuration, user_data)
            e_learning.get_all_submissions.assert_called_once()
            user_data.check_duplicate_url.assert_called_once()

    def test_core_iteration_plagiarism_check(self):
        configuration, logger = make_config()
        configuration.auth_token = ""
        configuration.check_plagiarism_hour = 99
        with mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.Canvas') as mock_canvas, \
                mock.patch('athina.cli.Repository') as mock_repo, \
                mock.patch('athina.cli.Tester') as mock_tester, \
                mock.patch('athina.cli.datetime.datetime') as mock_dt, \
                mock.patch('athina.cli.plagiarism_checks_on_users') as mock_plag, \
                mock.patch('athina.cli.os.chmod'):
            mock_args.repo_url_testing = None
            mock_dt.now.return_value.replace.return_value.hour = 99
            user_data = mock.Mock()
            core_iteration(configuration, user_data)
            mock_plag.assert_called_once()

    def test_core_iteration_repo_url_testing(self):
        import peewee
        configuration, logger = make_config()
        configuration.auth_token = ""
        with mock.patch('athina.cli.LOGGER') as mock_logger, \
                mock.patch('athina.cli.ARGS') as mock_args, \
                mock.patch('athina.cli.Canvas') as mock_canvas, \
                mock.patch('athina.cli.Repository') as mock_repo, \
                mock.patch('athina.cli.Tester') as mock_tester, \
                mock.patch('athina.cli.return_a_student', side_effect=peewee.DoesNotExist), \
                mock.patch('athina.cli.Users') as mock_users, \
                mock.patch('athina.cli.sys.exit') as mock_exit:
            mock_users.DoesNotExist = peewee.DoesNotExist
            mock_args.repo_url_testing = 'https://github.com/x/y.git'
            user_data = mock.Mock()
            core_iteration(configuration, user_data)
            mock_exit.assert_called_once_with(0)
