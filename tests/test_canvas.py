# TODO: get_additional_user_info() test

import json
from datetime import datetime
from unittest import mock, TestCase

from athina.canvas import *
from athina.configuration import *
from athina.logger import *
from athina.users import *
from tests.test_athina import create_test_config, create_fake_user_db
from tests.helpers import make_config


class TestFunctions(TestCase):
    def test_canvas_user_list_processing(self):
        canvas_return = """[{
	"id": 101417508,
	"body": null,
	"url": "https://github.com/athina-edu/testing",
	"grade": "84",
	"score": 84.0,
	"submitted_at": "2019-01-29T00:50:32Z",
	"assignment_id": 4521476,
	"user_id": 3406592,
	"submission_type": "online_url",
	"workflow_state": "graded",
	"grade_matches_current_submission": true,
	"graded_at": "2019-02-01T22:59:52Z",
	"grader_id": 3709282,
	"attempt": 1,
	"cached_due_date": "2019-01-26T07:59:59Z",
	"excused": false,
	"late_policy_status": null,
	"points_deducted": 0.0,
	"grading_period_id": null,
	"extra_attempts": null,
	"late": true,
	"missing": false,
	"seconds_late": 233433,
	"entered_grade": "84",
	"entered_score": 84.0,
	"preview_url": "",
	"attachments": [{
		"id": 53661222,
		"uuid": "9v872IqMbYrbAJ5wU4AhQ6LWgK4DTKlnVeVJIM7F",
		"folder_id": null,
		"display_name": "websnappr20190129-14302-jw7xoi.png",
		"filename": "websnappr20180922-20119-19s5wdl.png",
		"workflow_state": "processed",
		"content-type": "image/png",
		"url": "",
		"size": 56251,
		"created_at": "2019-01-29T00:50:42Z",
		"updated_at": "2019-01-29T00:50:42Z",
		"unlock_at": null,
		"locked": false,
		"hidden": false,
		"lock_at": null,
		"hidden_for_user": false,
		"thumbnail_url": "",
		"modified_at": "2019-01-29T00:50:42Z",
		"mime_class": "image",
		"media_entry_id": null,
		"locked_for_user": false,
		"preview_url": null
	}]
}, {
	"id": 101417510,
	"body": null,
	"url": "https://github.com/athina-edu/testing_config",
	"grade": "100",
	"score": 100.0,
	"submitted_at": "2019-01-15T20:21:28Z",
	"assignment_id": 4521476,
	"user_id": 3476374,
	"submission_type": "online_url",
	"workflow_state": "graded",
	"grade_matches_current_submission": true,
	"graded_at": "2019-01-26T23:30:41Z",
	"grader_id": 3709282,
	"attempt": 1,
	"cached_due_date": "2019-01-26T07:59:59Z",
	"excused": false,
	"late_policy_status": null,
	"points_deducted": null,
	"grading_period_id": null,
	"extra_attempts": null,
	"late": false,
	"missing": false,
	"seconds_late": 0,
	"entered_grade": "100",
	"entered_score": 100.0,
	"preview_url": "",
	"attachments": [{
		"id": 53338318,
		"uuid": "TlcpttxUkcGiOObG2QRSxkEYCtiCogjZhsueqQ2T",
		"folder_id": null,
		"display_name": "websnappr20190115-5120-1ntr23q.png",
		"filename": "websnappr20180922-20119-19s5wdl.png",
		"workflow_state": "processed",
		"content-type": "image/png",
		"url": "",
		"size": 56251,
		"created_at": "2019-01-15T20:21:37Z",
		"updated_at": "2019-01-15T20:21:37Z",
		"unlock_at": null,
		"locked": false,
		"hidden": false,
		"lock_at": null,
		"hidden_for_user": false,
		"thumbnail_url": "",
		"modified_at": "2019-01-15T20:21:37Z",
		"mime_class": "image",
		"media_entry_id": null,
		"locked_for_user": false,
		"preview_url": null
	}]
}]"""

        create_test_config()
        user_data = create_fake_user_db()

        logger = Logger()
        configuration = Configuration(logger=logger)

        canvas_return = json.loads(canvas_return)
        e_learning = Canvas(logger=logger, configuration=configuration)
        for record in canvas_return:
            e_learning.parse_canvas_submissions(record)
        obj = Users.get(Users.user_id == 3476374)
        # The .git is added by Athina in order to standardize git links
        self.assertEqual("https://github.com/athina-edu/testing_config.git", obj.repository_url)

        return True


class TestCanvas(TestCase):
    def setUp(self):
        self.configuration, self.logger = make_config()
        self.configuration.auth_token = "token"
        self.configuration.canvas_url = "canvas.example.com"
        self.configuration.course_id = 1
        self.configuration.assignment_id = 1

    def test_get_last_updated_default(self):
        with mock.patch('athina.canvas.load_key_from_assignment_data', return_value=None):
            canvas = Canvas(self.configuration, self.logger)
            self.assertEqual(canvas.last_update, datetime(1, 1, 1, 0, 0))
            self.assertEqual(self.configuration.due_date, datetime(2050, 1, 1, 0, 0))

    def test_get_last_updated_with_values(self):
        with mock.patch('athina.canvas.load_key_from_assignment_data',
                        side_effect=["2019-01-01T00:00:00", "2020-01-01T00:00:00"]):
            canvas = Canvas(self.configuration, self.logger)
            self.assertEqual(canvas.last_update.year, 2019)
            self.assertEqual(self.configuration.due_date.year, 2020)

    def test_update_last_update(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.update_key_in_assignment_data') as mock_upd:
            canvas.update_last_update()
            self.assertEqual(mock_upd.call_count, 2)

    def test_needs_update(self):
        canvas = Canvas(self.configuration, self.logger)
        canvas.last_update = datetime(2000, 1, 1, 0, 0)
        self.assertTrue(canvas.needs_update)

    def test_base_url(self):
        canvas = Canvas(self.configuration, self.logger)
        self.assertIn("canvas.example.com", canvas.base_url)

    def test_authorization_token(self):
        canvas = Canvas(self.configuration, self.logger)
        self.assertEqual(canvas.authorization_token, {"Authorization": "Bearer token"})

    def test_authorization_token_empty(self):
        self.configuration.auth_token = ""
        canvas = Canvas(self.configuration, self.logger)
        self.assertIsNone(canvas.authorization_token)

    def test_validate_response_bad_auth(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch.object(self.logger.logger, 'error') as mock_err:
            self.assertFalse(canvas.validate_response({"status": "unauthenticated"}))
            mock_err.assert_called()

    def test_validate_response_good(self):
        canvas = Canvas(self.configuration, self.logger)
        self.assertTrue(canvas.validate_response([{"a": 1}]))

    def test_get_all_submissions(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value=[{"user_id": 900,
                                                                    "submitted_at": "2019-01-01T00:00:00Z",
                                                                    "url": "https://github.com/x/y"}]):
            self.assertTrue(canvas.get_all_submissions())

    def test_get_all_submissions_bad_auth(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value={"status": "unauthenticated"}):
            self.assertFalse(canvas.get_all_submissions())

    def test_submit_comment(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url') as mock_req:
            canvas.submit_comment(1, "hello")
            mock_req.assert_called_once()

    def test_submit_grade_not_as_file(self):
        canvas = Canvas(self.configuration, self.logger)
        self.configuration.submit_results_as_file = False
        user = Users.create(user_id=901, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        with mock.patch.object(canvas, 'submit_grade_canvas') as mock_sgc:
            canvas.submit_grade(901, user, 80, [b"report"])
            mock_sgc.assert_called_once()
        user.delete_instance()

    def test_submit_grade_as_file(self):
        canvas = Canvas(self.configuration, self.logger)
        self.configuration.submit_results_as_file = True
        user = Users.create(user_id=902, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        with mock.patch.object(canvas, 'upload_file_to_canvas',
                               return_value={"fileid": 5, "public": False}), \
                mock.patch.object(canvas, 'submit_grade_canvas') as mock_sgc:
            canvas.submit_grade(902, user, 80, [b"report"])
            mock_sgc.assert_called_once()
        user.delete_instance()

    def test_submit_grade_as_file_upload_error(self):
        canvas = Canvas(self.configuration, self.logger)
        self.configuration.submit_results_as_file = True
        user = Users.create(user_id=903, course_id=1, assignment_id=1,
                            repository_url="https://github.com/x/y.git")
        with mock.patch.object(canvas, 'upload_file_to_canvas',
                               return_value={"fileid": 0, "public": False}), \
                mock.patch.object(canvas, 'submit_grade_canvas') as mock_sgc:
            canvas.submit_grade(903, user, 80, [b"report"])
            mock_sgc.assert_called_once()
        user.delete_instance()

    def test_get_assignment_due_date(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url',
                        return_value={"due_at": "2020-06-15T00:00:00Z"}):
            result = canvas.get_assignment_due_date()
            self.assertEqual(result.year, 2020)

    def test_get_assignment_due_date_bad_auth(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value={"status": "unauthenticated"}):
            result = canvas.get_assignment_due_date()
            self.assertEqual(result.year, 2050)

    def test_get_assignment_due_date_key_error(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value={}):
            result = canvas.get_assignment_due_date()
            self.assertEqual(result.year, 2050)

    def test_parse_canvas_submissions_new_user(self):
        canvas = Canvas(self.configuration, self.logger)
        canvas.parse_canvas_submissions({"user_id": 910, "submitted_at": "2019-01-01T00:00:00Z",
                                         "url": "https://github.com/x/y"})
        obj = return_a_student(1, 1, 910)
        self.assertEqual(obj.repository_url, "https://github.com/x/y.git")

    def test_parse_canvas_submissions_no_submitted_at(self):
        canvas = Canvas(self.configuration, self.logger)
        canvas.parse_canvas_submissions({"user_id": 911, "submitted_at": None,
                                         "url": "https://github.com/x/y"})
        obj = return_a_student(1, 1, 911)
        self.assertEqual(obj.url_date, datetime(1, 1, 1, 0, 0))

    def test_parse_canvas_submissions_existing_newer(self):
        canvas = Canvas(self.configuration, self.logger)
        Users.create(user_id=912, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/old.git",
                     url_date=datetime(2000, 1, 1, 0, 0))
        canvas.parse_canvas_submissions({"user_id": 912, "submitted_at": "2019-01-01T00:00:00Z",
                                         "url": "https://github.com/x/new"})
        obj = return_a_student(1, 1, 912)
        self.assertEqual(obj.repository_url, "https://github.com/x/new.git")

    def test_parse_canvas_submissions_existing_older(self):
        canvas = Canvas(self.configuration, self.logger)
        Users.create(user_id=913, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/old.git",
                     url_date=datetime(2020, 1, 1, 0, 0))
        canvas.parse_canvas_submissions({"user_id": 913, "submitted_at": "2019-01-01T00:00:00Z",
                                         "url": "https://github.com/x/new"})
        obj = return_a_student(1, 1, 913)
        self.assertEqual(obj.repository_url, "https://github.com/x/old.git")

    def test_upload_params_for_comment_upload(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value={"ok": True}) as mock_req:
            result = canvas.upload_params_for_comment_upload("f.txt", 1)
            self.assertEqual(result, {"ok": True})
            mock_req.assert_called_once()

    def test_upload_file_to_canvas(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch.object(canvas, 'upload_params_for_folder_upload', return_value={"x": 1}), \
                mock.patch.object(canvas, 'upload', return_value={"id": 7}):
            result = canvas.upload_file_to_canvas("f.txt", 1, b"contents")
            self.assertEqual(result, {"public": True, "fileid": 7})

    def test_submit_grade_canvas(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url') as mock_req:
            canvas.submit_grade_canvas(1, 80, comment_text="nice")
            mock_req.assert_called_once()

    def test_submit_grade_canvas_comment_file(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url') as mock_req:
            canvas.submit_grade_canvas(1, 80, comment_file=5)
            mock_req.assert_called_once()

    def test_upload_params_for_folder_upload(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value={"ok": True}) as mock_req:
            result = canvas.upload_params_for_folder_upload("f.txt")
            self.assertEqual(result, {"ok": True})
            mock_req.assert_called_once()

    def test_upload_missing_params(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch.object(self.logger.logger, 'error') as mock_err:
            result = canvas.upload({}, b"contents")
            self.assertEqual(result, {})
            mock_err.assert_called()

    def test_upload_success(self):
        canvas = Canvas(self.configuration, self.logger)
        link_url = {"upload_params": {"a": "b"}, "upload_url": "http://upload"}
        with mock.patch('athina.canvas.request_url', return_value={"id": 3}):
            result = canvas.upload(link_url, b"contents")
            self.assertEqual(result, {"id": 3})

    def test_get_additional_user_info(self):
        canvas = Canvas(self.configuration, self.logger)
        Users.create(user_id=920, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git")
        with mock.patch('athina.canvas.request_url',
                        return_value=[{"id": 920, "email": "a@b.com", "name": "Alice"}]):
            canvas.get_additional_user_info([])
        obj = return_a_student(1, 1, 920)
        self.assertEqual(obj.secondary_id, "a@b.com")
        self.assertEqual(obj.user_fullname, "Alice")

    def test_get_additional_user_info_bad_auth(self):
        canvas = Canvas(self.configuration, self.logger)
        with mock.patch('athina.canvas.request_url', return_value={"status": "unauthenticated"}):
            result = canvas.get_additional_user_info([1])
            self.assertEqual(result, [1])
