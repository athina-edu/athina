# TODO: get_additional_user_info() test

from tests.test_athina import create_test_config, create_logger, create_fake_user_db
import unittest
from athina.canvas import *
from athina.logger import *
from athina.configuration import *
from athina.users import *
import json

class TestFunctions(unittest.TestCase):
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
