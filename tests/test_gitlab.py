# Tests for athina.git.gitlab (GitLab webhook + private-repo checks).
from unittest import mock, TestCase

from athina.git.gitlab import gitlab_return_encoded_url, gitlab_set_webhook, gitlab_check_if_repo_private
from athina.users import Database, Users, AssignmentData
from tests.helpers import make_config


class TestGitlab(TestCase):
    def setUp(self):
        user_data = Database()
        user_data.db.drop_tables([Users, AssignmentData])
        user_data.db.create_tables([Users, AssignmentData])

    def test_gitlab_return_encoded_url_with_git(self):
        result = gitlab_return_encoded_url("https://gitlab.com/group/project.git")
        self.assertEqual(result, "group%2Fproject")

    def test_gitlab_return_encoded_url_without_git(self):
        result = gitlab_return_encoded_url("https://gitlab.com/group/project")
        self.assertEqual(result, "group%2Fproject")

    def test_gitlab_set_webhook_new(self):
        configuration, logger = make_config()
        configuration.athina_web_url = "http://web"
        configuration.git_url = "gitlab.com"
        configuration.git_password = "pass"
        user = Users.create(user_id=999, course_id=1, assignment_id=1,
                            repository_url="https://gitlab.com/group/project.git")
        with mock.patch('athina.git.gitlab.request_url',
                        side_effect=[[], {"created_at": "2020-01-01"}]) as mock_req:
            gitlab_set_webhook(configuration, logger, user)
            self.assertTrue(mock_req.called)
            self.assertTrue(user.use_webhook)
        user.delete_instance()

    def test_gitlab_set_webhook_existing(self):
        configuration, logger = make_config()
        configuration.athina_web_url = "http://web"
        configuration.git_url = "gitlab.com"
        configuration.git_password = "pass"
        user = Users.create(user_id=998, course_id=1, assignment_id=1,
                            repository_url="https://gitlab.com/group/project.git")
        with mock.patch('athina.git.gitlab.request_url',
                        side_effect=[[{"id": 5, "url": "http://web/assignments/webhook/"}],
                                     {"created_at": "2020-01-01"}]) as mock_req:
            gitlab_set_webhook(configuration, logger, user)
            self.assertTrue(mock_req.called)
            self.assertTrue(user.use_webhook)
        user.delete_instance()

    def test_gitlab_set_webhook_attribute_error(self):
        configuration, logger = make_config()
        configuration.athina_web_url = "http://web"
        configuration.git_url = "gitlab.com"
        configuration.git_password = "pass"
        user = Users.create(user_id=997, course_id=1, assignment_id=1,
                            repository_url="https://gitlab.com/group/project.git")
        with mock.patch('athina.git.gitlab.request_url', return_value=[None]):
            result = gitlab_set_webhook(configuration, logger, user)
            self.assertIsNone(result)
        user.delete_instance()

    def test_gitlab_set_webhook_no_web_url(self):
        configuration, logger = make_config()
        configuration.athina_web_url = None
        user = Users.create(user_id=996, course_id=1, assignment_id=1,
                            repository_url="https://gitlab.com/group/project.git")
        gitlab_set_webhook(configuration, logger, user)
        user.delete_instance()

    def test_gitlab_check_if_repo_private_private(self):
        configuration, logger = make_config()
        configuration.gitlab_check_repo_is_private = True
        configuration.git_url = "gitlab.com"
        configuration.git_password = "pass"
        with mock.patch('athina.git.gitlab.request_url', return_value={"visibility": "private"}):
            self.assertTrue(gitlab_check_if_repo_private(configuration, logger, "https://gitlab.com/g/p.git"))

    def test_gitlab_check_if_repo_private_public(self):
        configuration, logger = make_config()
        configuration.gitlab_check_repo_is_private = True
        configuration.git_url = "gitlab.com"
        configuration.git_password = "pass"
        with mock.patch('athina.git.gitlab.request_url', return_value={"visibility": "public"}):
            self.assertFalse(gitlab_check_if_repo_private(configuration, logger, "https://gitlab.com/g/p.git"))

    def test_gitlab_check_if_repo_private_none(self):
        configuration, logger = make_config()
        configuration.gitlab_check_repo_is_private = True
        configuration.git_url = "gitlab.com"
        configuration.git_password = "pass"
        with mock.patch('athina.git.gitlab.request_url', return_value={}):
            self.assertTrue(gitlab_check_if_repo_private(configuration, logger, "https://gitlab.com/g/p.git"))

    def test_gitlab_check_if_repo_private_disabled(self):
        configuration, logger = make_config()
        configuration.gitlab_check_repo_is_private = False
        self.assertTrue(gitlab_check_if_repo_private(configuration, logger, "https://gitlab.com/g/p.git"))
