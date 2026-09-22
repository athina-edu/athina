"""Tests for athina.moss — the plagiarism detection module.

The implementation lives in ``athina.moss``; ``athina.plagiarism`` is a
backwards-compatibility shim that re-exports from it (asserted below).
"""
from datetime import datetime
from unittest import mock, TestCase

import numpy as np

from athina.moss import (Plagiarism, _collect_submission_directories,
                         _max_similarity_for, _mean_similarity,
                         plagiarism_checks_on_users)
from athina.users import *
from tests.helpers import make_config
from tests.test_athina import create_logger


def _detector_stub(similarity, file_list):
    """Build a stand-in for a ran CopyDetector."""
    detector = mock.Mock()
    detector.similarity = np.array(similarity)
    detector.file_list = file_list
    return detector


class TestModuleLayout(TestCase):
    def test_plagiarism_shim_reexports_same_objects(self):
        """athina.plagiarism must expose the very same objects, not copies."""
        import athina.moss
        import athina.plagiarism

        self.assertIs(athina.plagiarism.Plagiarism, athina.moss.Plagiarism)
        self.assertIs(athina.plagiarism.plagiarism_checks_on_users,
                      athina.moss.plagiarism_checks_on_users)

    def test_moss_support_is_fully_removed(self):
        """The dead MOSS online-service path should be gone."""
        import athina.moss

        self.assertFalse(hasattr(athina.moss, '_HAS_MOSSPY'))
        self.assertFalse(hasattr(athina.moss.Plagiarism, 'moss_id'))
        self.assertFalse(hasattr(athina.moss.Plagiarism, 'moss_lang'))
        self.assertFalse(hasattr(athina.moss.Plagiarism, '_check_moss'))


class TestHelpers(TestCase):
    def test_mean_similarity_pads_missing_students_with_zero(self):
        # One student scored 50; two others had no matches at all.
        self.assertEqual(_mean_similarity({1: [50]}, 3), 50 / 3)

    def test_mean_similarity_no_data_returns_zero(self):
        self.assertEqual(_mean_similarity({}, 0), 0)
        self.assertEqual(_mean_similarity({}, 5), 0)

    def test_mean_similarity_averages_all_values(self):
        self.assertEqual(_mean_similarity({1: [80, 20], 2: [40]}, 2), (80 + 20 + 40) / 3)

    def test_max_similarity_for_known_user(self):
        self.assertEqual(_max_similarity_for({1: [30, 90], 2: [10]}, 1), 90)

    def test_max_similarity_for_unknown_user_is_zero(self):
        self.assertEqual(_max_similarity_for({1: [30]}, 999), 0)

    def test_max_similarity_for_empty_is_zero(self):
        self.assertEqual(_max_similarity_for({}, 1), 0)

    def test_collect_submission_directories_uses_configured_pattern(self):
        configuration, _ = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        configuration.config_dir = "/tmp"
        configuration.plagiarism_pattern = "*.py"
        student = mock.Mock(user_id=17)

        with mock.patch('athina.moss.return_all_students', return_value=[student]), \
                mock.patch('athina.moss.os.path.isdir', return_value=True), \
                mock.patch('athina.moss.glob.glob', return_value=["x"]):
            dirs = _collect_submission_directories(configuration)

        self.assertEqual(dirs, ["/tmp/repodata1/u17/*.py"])

    def test_collect_submission_directories_skips_empty_dirs(self):
        configuration, _ = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        configuration.config_dir = "/tmp"
        student = mock.Mock(user_id=17)

        # Directory exists but contains no matching files -> excluded.
        with mock.patch('athina.moss.return_all_students', return_value=[student]), \
                mock.patch('athina.moss.os.path.isdir', return_value=True), \
                mock.patch('athina.moss.glob.glob', return_value=[]):
            self.assertEqual(_collect_submission_directories(configuration), [])


class TestPlagiarism(TestCase):
    def test_plagiarism_init_other_service(self):
        p = Plagiarism(logger=create_logger(), service_type="other")
        self.assertIsNone(p.service_type)

    def test_plagiarism_init_no_service(self):
        p = Plagiarism(logger=create_logger())
        self.assertIsNone(p.service_type)

    def test_plagiarism_init_copydetect(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect", threshold=0.5)
        self.assertEqual(p.service_type, "copydetect")
        self.assertEqual(p.threshold, 0.5)

    def test_plagiarism_init_copydetect_default_threshold(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        self.assertEqual(p.threshold, 0.33)

    def test_init_copydetect_without_package_raises(self):
        with mock.patch('athina.moss._HAS_COPYDETECT', False):
            with self.assertRaises(ImportError):
                Plagiarism(logger=create_logger(), service_type="copydetect")

    def test_check_plagiarism_empty_folder_list(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        self.assertEqual(p.check_plagiarism([], 1, 1), dict())

    def test_check_plagiarism_other_service(self):
        p = Plagiarism(logger=create_logger(), service_type="other")
        self.assertEqual(p.check_plagiarism(["/tmp/*.py"], 1, 1), dict())

    def test_parse_comparison_time_new(self):
        comparisons = {}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [75]})

    def test_parse_comparison_time_existing(self):
        comparisons = {1: [50]}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [50, 75]})


class TestParsePatterns(TestCase):
    def test_extracts_dirs_and_extensions(self):
        with mock.patch('athina.moss.os.path.isdir', return_value=True):
            dirs, exts = Plagiarism._parse_patterns(
                ["/tmp/r/u1/*.py", "/tmp/r/u2/*.py", "/tmp/r/u3/*.java"])
        self.assertEqual(dirs, ["/tmp/r/u1", "/tmp/r/u2", "/tmp/r/u3"])
        self.assertEqual(exts, {"py", "java"})

    def test_ignores_non_glob_and_missing_dirs(self):
        with mock.patch('athina.moss.os.path.isdir', return_value=False):
            dirs, exts = Plagiarism._parse_patterns(["/tmp/r/u1/notes.txt"])
        self.assertEqual(dirs, [])
        self.assertEqual(exts, set())


class TestMapResults(TestCase):
    def test_maps_similarity_matrix_to_user_ids(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.75], [0.75, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u2/b.py"],
        )
        self.assertEqual(p._map_results(detector), {1: [75], 2: [75]})

    def test_skips_self_comparisons(self):
        """Two files from the same student must not score as a match."""
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.9], [0.9, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u1/b.py"],
        )
        self.assertEqual(p._map_results(detector), {})

    def test_skips_unparseable_paths(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.9], [0.9, 0.0]],
            file_list=["/tmp/r/nouser/a.py", "/tmp/r/nouser/b.py"],
        )
        self.assertEqual(p._map_results(detector), {})

    def test_zero_similarity_is_ignored(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.0], [0.0, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u2/b.py"],
        )
        self.assertEqual(p._map_results(detector), {})

    def test_multiple_files_per_student_accumulate(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.5, 0.8], [0.5, 0.0, 0.3], [0.8, 0.3, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u2/b.py", "/tmp/r/u2/c.py"],
        )
        result = p._map_results(detector)
        # u1 (file 0) matches u2/b (file 1) at 50 and u2/c (file 2) at 80.
        # The (u2/b, u2/c) pair is a self-comparison and is skipped.
        self.assertEqual(sorted(result[1]), [50, 80])
        self.assertEqual(sorted(result[2]), [50, 80])


class TestRunDetector(TestCase):
    def test_returns_detector_on_success(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        with mock.patch('athina.moss._copydetect') as cd:
            detector = p._run_detector(["/tmp/u1"], ["py"])
        cd.CopyDetector.assert_called_once()
        cd.CopyDetector.return_value.run.assert_called_once()
        self.assertIs(detector, cd.CopyDetector.return_value)

    def test_returns_none_and_logs_on_failure(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        with mock.patch('athina.moss._copydetect') as cd, \
                mock.patch.object(p.logger.logger, 'error') as mock_err:
            cd.CopyDetector.return_value.run.side_effect = Exception("boom")
            self.assertIsNone(p._run_detector(["/tmp/u1"], ["py"]))
            mock_err.assert_called_once()


class TestSaveHtmlReport(TestCase):
    def test_records_report_path(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = mock.Mock()
        with mock.patch('athina.moss.update_key_in_assignment_data') as mock_update:
            p._save_html_report(detector, ["/tmp/repodata1/u1"], 1, 2)
        detector.generate_html_report.assert_called_once()
        self.assertEqual(mock_update.call_args[0][0], 1)
        self.assertEqual(mock_update.call_args[0][1], 2)
        self.assertEqual(mock_update.call_args[0][2], "plagiarism_report")

    def test_report_failure_is_not_fatal(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = mock.Mock()
        detector.generate_html_report.side_effect = Exception("no report")
        with mock.patch.object(p.logger.logger, 'warning') as mock_warn:
            p._save_html_report(detector, ["/tmp/repodata1/u1"], 1, 2)
        mock_warn.assert_called_once()


class TestPlagiarismChecksOnUsers(TestCase):
    def setUp(self):
        # Ensure the users/assignmentdata tables exist for this test DB.
        Database().connect_to_db()

    def _config(self):
        configuration, _ = make_config()
        configuration.plagiarism_pattern = "*.py"
        configuration.config_dir = "/tmp"
        configuration.assignment_id = 1
        configuration.course_id = 1
        return configuration

    def _due_students(self):
        return list(Users.select().where(Users.course_id == 1,
                                         Users.assignment_id == 1))

    def _fresh_each_call(self):
        """Mimic production: return_all_students re-queries on every call.

        Using a side_effect (rather than a fixed return_value) matters because
        the function ends by re-reading and saving each student — a stale
        snapshot would clobber the scores written moments earlier.
        """
        return mock.patch('athina.moss.return_all_students',
                          side_effect=lambda *a, **k: self._due_students())

    def test_returns_empty_when_no_users_are_due(self):
        configuration, logger = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        with mock.patch('athina.moss.return_all_students', return_value=[]):
            results = plagiarism_checks_on_users(logger, configuration, mock.Mock())
        self.assertEqual(results, [])

    def test_reports_error_when_copydetect_missing(self):
        configuration = self._config()
        _, logger = make_config()
        student = mock.Mock(user_id=555, plagiarism_to_grade=True,
                            last_plagiarism_check=datetime(2000, 1, 1))
        with mock.patch('athina.moss.return_all_students', return_value=[student]), \
                mock.patch('athina.moss._HAS_COPYDETECT', False), \
                mock.patch.object(logger.logger, 'error') as mock_err:
            results = plagiarism_checks_on_users(logger, configuration, mock.Mock())
        self.assertEqual(results, [])
        mock_err.assert_called_once()

    def test_records_scores_for_due_student(self):
        configuration = self._config()
        _, logger = make_config()
        Users.create(user_id=555, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.return_value = {555: [75]}
                results = plagiarism_checks_on_users(logger, configuration, mock.Mock())

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], 555)
            self.assertEqual(results[0][1], 75)

            refreshed = Users.get(Users.user_id == 555)
            self.assertEqual(refreshed.plagiarism_max, 75)
            self.assertFalse(refreshed.plagiarism_to_grade)
        finally:
            Users.delete().where(Users.user_id == 555).execute()

    def test_publishes_comment_when_configured(self):
        configuration = self._config()
        configuration.plagiarism_publish = True
        _, logger = make_config()
        Users.create(user_id=556, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        e_learning = mock.Mock()
        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.return_value = {556: [40]}
                plagiarism_checks_on_users(logger, configuration, e_learning)

            e_learning.submit_comment.assert_called_once()
            self.assertEqual(e_learning.submit_comment.call_args[0][0], 556)
        finally:
            Users.delete().where(Users.user_id == 556).execute()

    def test_does_not_publish_when_disabled(self):
        configuration = self._config()
        configuration.plagiarism_publish = False
        _, logger = make_config()
        Users.create(user_id=557, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        e_learning = mock.Mock()
        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.return_value = {557: [40]}
                plagiarism_checks_on_users(logger, configuration, e_learning)

            e_learning.submit_comment.assert_not_called()
        finally:
            Users.delete().where(Users.user_id == 557).execute()

    def test_student_not_due_is_skipped(self):
        """A student checked recently should not be re-processed."""
        configuration = self._config()
        _, logger = make_config()
        Users.create(user_id=558, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2099, 1, 1, 0, 0))
        try:
            with self._fresh_each_call():
                results = plagiarism_checks_on_users(logger, configuration, mock.Mock())
            self.assertEqual(results, [])
        finally:
            Users.delete().where(Users.user_id == 558).execute()
