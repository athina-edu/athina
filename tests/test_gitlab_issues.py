"""Tests for athina.gitlab_issues — GitLab issue output adapter."""
from datetime import datetime
from unittest import TestCase, mock

from athina.gitlab_issues import GitLabIssues
from athina.users import Database, Users
from tests.helpers import make_config
from tests.test_athina import create_logger


def _adapter(**overrides):
    configuration, logger = make_config()
    configuration.git_url = "gitlab.example.com"
    configuration.git_password = "glpat-token"
    configuration.total_points = 100
    configuration.gitlab_issues_confidential = True
    configuration.gitlab_issues_title_prefix = "Grade Report"
    configuration.course_id = 1
    configuration.assignment_id = 1
    for key, value in overrides.items():
        setattr(configuration, key, value)
    return GitLabIssues(configuration, logger), configuration


class TestProjectPathParsing(TestCase):
    def test_extracts_url_encoded_path(self):
        self.assertEqual(
            GitLabIssues._repo_url_to_project_path("https://gitlab.com/group/repo.git"),
            "group%2Frepo")

    def test_strips_credentials(self):
        self.assertEqual(
            GitLabIssues._repo_url_to_project_path("https://user:pass@gitlab.com/g/r.git"),
            "g%2Fr")

    def test_handles_nested_groups(self):
        self.assertEqual(
            GitLabIssues._repo_url_to_project_path("https://gitlab.com/a/b/c.git"),
            "a%2Fb%2Fc")

    def test_handles_trailing_slash_without_git(self):
        self.assertEqual(
            GitLabIssues._repo_url_to_project_path("https://gitlab.com/group/repo/"),
            "group%2Frepo")

    def test_empty_url_returns_empty(self):
        self.assertEqual(GitLabIssues._repo_url_to_project_path(""), "")

    def test_none_url_returns_empty(self):
        self.assertEqual(GitLabIssues._repo_url_to_project_path(None), "")

    def test_url_without_path_returns_empty(self):
        self.assertEqual(GitLabIssues._repo_url_to_project_path("https://gitlab.com"), "")


class TestCanvasInterfaceParity(TestCase):
    def test_needs_update_is_false(self):
        adapter, _ = _adapter()
        self.assertFalse(adapter.needs_update)

    def test_update_last_update_is_noop(self):
        adapter, _ = _adapter()
        self.assertIsNone(adapter.update_last_update())

    def test_get_all_submissions_returns_true(self):
        adapter, _ = _adapter()
        self.assertTrue(adapter.get_all_submissions())

    def test_get_additional_user_info_passes_through(self):
        adapter, _ = _adapter()
        users = [1, 2, 3]
        self.assertIs(adapter.get_additional_user_info(users), users)

    def test_due_date_is_far_future(self):
        adapter, _ = _adapter()
        self.assertEqual(adapter.get_assignment_due_date(), datetime(2050, 1, 1, 0, 0))


class TestBuildIssueBody(TestCase):
    def test_body_contains_grade_and_report(self):
        adapter, _ = _adapter()
        body = adapter._build_issue_body(7, "Alice", 85, 100, "all tests passed")
        self.assertIn("Alice", body)
        self.assertIn("85 / 100", body)
        self.assertIn("all tests passed", body)
        self.assertIn("## Grade Report", body)

    def test_confidential_marker_added_when_enabled(self):
        adapter, _ = _adapter(gitlab_issues_confidential=True)
        self.assertIn("(confidential)", adapter._build_issue_body(1, "A", 1, 1, "r"))

    def test_confidential_marker_absent_when_disabled(self):
        adapter, _ = _adapter(gitlab_issues_confidential=False)
        self.assertNotIn("(confidential)", adapter._build_issue_body(1, "A", 1, 1, "r"))


class TestCreateIssue(TestCase):
    def test_returns_iid_on_success(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 42}) as mock_req:
            result = adapter._create_issue("T", "B", "group%2Frepo")
        self.assertEqual(result, 42)
        self.assertEqual(mock_req.call_args[0][0],
                         "https://gitlab.example.com/api/v4/projects/group%2Frepo/issues")
        self.assertEqual(mock_req.call_args[1]['method'], 'post')

    def test_returns_zero_on_missing_iid(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url', return_value={}):
            self.assertEqual(adapter._create_issue("T", "B", "p"), 0)

    def test_returns_zero_on_none_response(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url', return_value=None):
            self.assertEqual(adapter._create_issue("T", "B", "p"), 0)

    def test_uses_bearer_token_and_confidential_flag(self):
        adapter, _ = _adapter(gitlab_issues_confidential=True)
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 1}) as mock_req:
            adapter._create_issue("T", "B", "p")
        self.assertEqual(mock_req.call_args[1]['headers']['Authorization'],
                         "Bearer glpat-token")
        self.assertEqual(mock_req.call_args[1]['payload']['confidential'], "true")


class TestUpdateIssue(TestCase):
    def test_returns_true_on_success(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 42}) as mock_req:
            self.assertTrue(adapter.update_issue(42, "T", "B", "group%2Frepo"))
        self.assertEqual(mock_req.call_args[1]['method'], 'put')
        self.assertIn("/issues/42", mock_req.call_args[0][0])

    def test_returns_false_on_failure(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url', return_value=None):
            self.assertFalse(adapter.update_issue(42, "T", "B", "p"))

    def test_missing_iid_short_circuits(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url') as mock_req:
            self.assertFalse(adapter.update_issue(0, "T", "B", "p"))
        mock_req.assert_not_called()

    def test_missing_project_path_short_circuits(self):
        adapter, _ = _adapter()
        with mock.patch('athina.gitlab_issues.request_url') as mock_req:
            self.assertFalse(adapter.update_issue(42, "T", "B", ""))
        mock_req.assert_not_called()


class TestSubmitGrade(TestCase):
    def setUp(self):
        Database().connect_to_db()

    def test_returns_zero_when_repo_url_missing(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(repository_url="")
        self.assertEqual(adapter.submit_grade(1, user_values, 50, ["r"]), 0)

    def test_returns_zero_when_repo_url_unparseable(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(repository_url="https://gitlab.com")
        self.assertEqual(adapter.submit_grade(1, user_values, 50, ["r"]), 0)

    def test_creates_issue_for_student_repo(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(repository_url="https://gitlab.com/g/r.git",
                                user_fullname="Alice")
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 9}) as mock_req:
            result = adapter.submit_grade(1, user_values, 88, ["PASS"])
        self.assertEqual(result, 9)
        payload = mock_req.call_args[1]['payload']
        self.assertIn("Alice", payload['title'])
        self.assertIn("88 / 100", payload['description'])

    def test_decodes_bytes_in_report(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(repository_url="https://gitlab.com/g/r.git",
                                user_fullname="Bob")
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 9}) as mock_req:
            adapter.submit_grade(1, user_values, 10, [b"raw bytes \xe2\x9c\x93"])
        self.assertIn("raw bytes", mock_req.call_args[1]['payload']['description'])

    def test_falls_back_to_secondary_id_then_user_id(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(repository_url="https://gitlab.com/g/r.git",
                                user_fullname="", secondary_id="a@b.c")
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 1}) as mock_req:
            adapter.submit_grade(77, user_values, 10, ["r"])
        self.assertIn("a@b.c", mock_req.call_args[1]['payload']['title'])

    def test_falls_back_to_user_id_when_no_name(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(repository_url="https://gitlab.com/g/r.git",
                                user_fullname="", secondary_id="")
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 1}) as mock_req:
            adapter.submit_grade(77, user_values, 10, ["r"])
        self.assertIn("77", mock_req.call_args[1]['payload']['title'])


class TestSubmitComment(TestCase):
    def setUp(self):
        Database().connect_to_db()

    def test_creates_issue_from_stored_student(self):
        adapter, _ = _adapter()
        Users.create(user_id=601, course_id=1, assignment_id=1,
                     repository_url="https://gitlab.com/g/r.git",
                     user_fullname="Carol")
        try:
            with mock.patch('athina.gitlab_issues.request_url',
                            return_value={"iid": 5}) as mock_req:
                result = adapter.submit_comment(601, "Great work")
            self.assertEqual(result, 5)
            self.assertEqual(mock_req.call_args[1]['payload']['description'], "Great work")
        finally:
            Users.delete().where(Users.user_id == 601).execute()

    def test_returns_zero_when_student_has_no_repo(self):
        adapter, _ = _adapter()
        Users.create(user_id=602, course_id=1, assignment_id=1, repository_url="")
        try:
            self.assertEqual(adapter.submit_comment(602, "hi"), 0)
        finally:
            Users.delete().where(Users.user_id == 602).execute()


class TestCreateInitialIssue(TestCase):
    def test_creates_progress_issue(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(user_fullname="Dave", secondary_id="")
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 3}) as mock_req:
            result = adapter.create_initial_issue(1, user_values, "group%2Frepo")
        self.assertEqual(result, 3)
        self.assertIn("Test in Progress", mock_req.call_args[1]['payload']['description'])

    def test_missing_project_path_returns_zero(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(user_fullname="Dave")
        self.assertEqual(adapter.create_initial_issue(1, user_values, ""), 0)

    def test_falls_back_to_user_id(self):
        adapter, _ = _adapter()
        user_values = mock.Mock(user_fullname="", secondary_id="")
        with mock.patch('athina.gitlab_issues.request_url',
                        return_value={"iid": 3}) as mock_req:
            adapter.create_initial_issue(55, user_values, "p")
        self.assertIn("55", mock_req.call_args[1]['payload']['title'])
