# All functions relating to data retrieval and submission in relation to Canvas e-learning platform
# There are built in a general fashion so that e-learning platforms can be easily switched (as long as func names
# remain the same)
from datetime import datetime, timedelta
import dateutil.parser
from athina.url import *
from athina.users import *


class Canvas:
    configuration = None
    logger = None  # athina's logger object for event logging and debugging
    last_update = datetime(1, 1, 1, 0, 0).replace(tzinfo=None)

    def __init__(self, configuration, logger):
        self.configuration = configuration
        self.logger = logger
        self.get_last_updated()

    def get_last_updated(self):
        last_update = load_key_from_assignment_data("last_update")
        if last_update is not None:
            self.last_update = dateutil.parser.parse(last_update)
        else:
            self.last_update = datetime(1, 1, 1, 0, 0).replace(tzinfo=None)

        due_date = load_key_from_assignment_data("due_date")
        if due_date is not None:
            self.configuration.due_date = dateutil.parser.parse(due_date)
        else:
            self.configuration.due_date = datetime(2050, 1, 1, 0, 0).replace(tzinfo=None)

    def update_last_update(self):
        update_key_in_assignment_data("last_update", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        update_key_in_assignment_data("due_date", self.configuration.due_date.isoformat())

    @property
    def needs_update(self):
        return self.last_update + timedelta(hours=1) <= datetime.now(timezone.utc).replace(tzinfo=None)

    @property
    def base_url(self):
        return "https://%s/api/v1/courses/%d" % (self.configuration.canvas_url, self.configuration.course_id)

    @property
    def authorization_token(self):
        return {"Authorization": "Bearer %s" % self.configuration.auth_token}

    def get_all_submissions(self):
        """
        Get all users (except test student)
        """
        data = request_url("%s/assignments/%d/submissions/?per_page=150" %
                           (self.base_url, self.configuration.assignment_id), self.authorization_token, method="get")
        if not self.validate_response(data):
            return False
        for record in data:
            self.parse_canvas_submissions(record)
        return True

    def submit_comment(self, user_id, comment):
        request_url("%s/assignments/%d/submissions/%d" % (self.base_url, self.configuration.assignment_id, user_id),
                    self.authorization_token, payload={'comment[text_comment]': comment}, method="put")

    def submit_grade(self, user_id, user_values, grade, test_reports):
        if self.configuration.submit_results_as_file:
            # Uploading athina.py results as a report file (less spam on comments)
            file_contents = "\n".join([line.decode("utf-8", "backslashreplace") for line in test_reports])
            upload_result = self.upload_file_to_canvas(filename="athina_%s%s.txt" % (user_id, user_values.commit_date),
                                                       user_id=user_id, file_contents=file_contents)
            if upload_result["fileid"] != 0:
                if upload_result["public"] is False:
                    # Submit grade and comment referencing the file that was just uploaded
                    self.submit_grade_canvas(user_id=user_id, grade=grade, comment_file=upload_result["fileid"])
                else:
                    comment_text = "See file:\nhttps://%s/files/%d/download?download_frd=1" % \
                                   (self.configuration.canvas_url, upload_result["fileid"])
                    self.submit_grade_canvas(user_id=user_id, grade=grade, comment_text=comment_text)
            else:
                comment_text = "An error has occurred when uploading comment file to Canvas.\n" \
                               "Please contact the instructor."
                self.submit_grade_canvas(user_id=user_id, grade=grade, comment_text=comment_text)
        else:
            comment_text = "\n".join([line.decode("utf-8", "backslashreplace") for line in test_reports])
            self.submit_grade_canvas(user_id=user_id, grade=grade, comment_text=comment_text)

    def validate_response(self, data):
        if not isinstance(data, list) and isinstance(data, dict) and data.get('status', 0) == 'unauthenticated':
            self.logger.logger.error("Incorrect response data from Canvas (bad authentication).")
            self.logger.logger.debug(data)
            return False
        else:
            self.logger.logger.debug("Canvas Response: %s" % data)
            return True

    def get_assignment_due_date(self):
        """
        Get assignment due date
        """
        data = request_url("%s/assignments/%d" % (self.base_url, self.configuration.assignment_id),
                           self.authorization_token, method="get")
        if not self.validate_response(data):
            return dateutil.parser.parse("2050-01-01 00:00:00")  # a day in the future
        try:
            due_date = dateutil.parser.parse(data["due_at"]).astimezone(dateutil.tz.UTC).replace(tzinfo=None)
        except (TypeError, KeyError):
            self.logger.logger.error(
                "Type/Key error for due date (probably not specified on elearning platform), using no due date.")
            return dateutil.parser.parse("2050-01-01 00:00:00")  # a day in the future

        return due_date

    @staticmethod
    def parse_canvas_submissions(data):
        if data["submitted_at"] is None:
            submitted_date = datetime(1, 1, 1, 0, 0)
        else:
            submitted_date = dateutil.parser.parse(data["submitted_at"]).astimezone(dateutil.tz.UTC). \
                replace(tzinfo=None)
        try:
            obj = Users.get(Users.user_id == data["user_id"])
        except Users.DoesNotExist:
            obj = 0
        if obj == 0:
            Users.create(user_id=data["user_id"],
                         repository_url=data["url"],
                         url_date=submitted_date,
                         new_url=True,
                         commit_date=submitted_date)
        else:
            # New submission will always happen on Canvas on a chronological order
            if obj.url_date < submitted_date:
                obj.repository_url = data["url"]
                obj.url_date = submitted_date
                obj.new_url = True
                obj.commit_date = submitted_date
                obj.save()

    def upload_params_for_comment_upload(self, filename, user_id):
        link_url = request_url("%s/assignments/%d/submissions/%d/comments/files" %
                               (self.base_url, self.configuration.assignment_id, user_id), self.authorization_token,
                               payload={'name': filename}, method="post")
        return link_url

    def upload_file_to_canvas(self, filename, user_id, file_contents):
        public = False
        self.logger.logger.info("Attempting to upload file size: %d" % len(file_contents))
        link_url = self.upload_params_for_comment_upload(filename, user_id)
        return_url = self.upload(link_url, file_contents)
        if return_url.get("message", 0) == 'file size exceeds quota limits':
            # Try uploading to canvas generic folder instead (public)
            public = True
            link_url = self.upload_params_for_folder_upload(filename)
            return_url = self.upload(link_url, file_contents)
        # if we get the same error a second time then return 0
        fileid = return_url.get("id", 0)
        return {'public': public, 'fileid': fileid}

    def submit_grade_canvas(self, user_id, grade, comment_text=None, comment_file=None):
        payload = {'submission[posted_grade]': grade}
        if comment_text is not None:
            payload['comment[text_comment]'] = comment_text
        if comment_file is not None:
            payload['comment[file_ids][]'] = comment_file
        request_url("%s/assignments/%d/submissions/%d" % (self.base_url, self.configuration.assignment_id, user_id),
                    self.authorization_token, payload=payload, method="put", return_type="json")

    def upload_params_for_folder_upload(self, filename):
        link_url = request_url("%s/files" % self.base_url, self.authorization_token,
                               payload={'name': filename, "parent_folder_path": "athina.py"}, method="post")
        return link_url

    def upload(self, link_url, file_contents):
        # Validate information received
        try:
            link_url["upload_params"]
        except KeyError:
            self.logger.logger.error("Attempted upload failed to return expected output.")
            self.logger.logger.error("This is typically associated with a wrong canvas url or canvas is down.")
            return {}

        payload = dict()
        for param, content in link_url["upload_params"].items():
            payload[param] = content

        return_url = request_url(
            link_url["upload_url"],
            payload=payload,
            files={'file': file_contents},
            method="post",
            return_type="json")
        return return_url

    def get_additional_user_info(self, users):
        """
        Obtain additional user information
        """
        data = request_url("%s/users/?per_page=150" % self.base_url, self.authorization_token, method="get")
        if not self.validate_response(data):
            return users
        for record in data:
            try:
                obj = Users.get(Users.user_id == record["id"])
            except (KeyError, Users.DoesNotExist):
                continue
            if obj.secondary_id != record["login_id"] or obj.user_fullname != record["name"]:
                obj.secondary_id = record["login_id"]
                obj.user_fullname = record["name"]
                obj.save()
        return users
