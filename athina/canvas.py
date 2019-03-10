# All functions relating to data retrieval and submission in relation to Canvas e-learning platform
# There are built in a general fashion so that e-learning platforms can be easily switched (as long as func names
# remain the same)
from datetime import datetime, timezone
import dateutil.parser
from athina.url import *


class Canvas:
    configuration = None
    logger = None  # athina's logger object for event logging and debugging

    def __init__(self, configuration, logger):
        self.configuration = configuration
        self.logger = logger

    def get_all_submissions(self, users):
        """
        Get all users (except test student)
        """
        data = request_url(
            "https://%s/api/v1/courses/%d/assignments/%d/submissions/?per_page=150" %
            (self.configuration.canvas_url, self.configuration.course_id, self.configuration.assignment_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token}, method="get")
        if not self.validate_response(data):
            return users
        for record in data:
            users = self.parse_canvas_submissions(record, users)
        return users

    def submit_comment(self, user_id, comment):
        request_url(
            "https://%s/api/v1/courses/%d/assignments/%d/submissions/%d" %
            (self.configuration.canvas_url, self.configuration.course_id, self.configuration.assignment_id, user_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token},
            payload={'comment[text_comment]': comment},
            method="put")

    def submit_grade(self, user_id, user_values, grade, test_reports):
        if self.configuration.submit_results_as_file:
            # Uploading athina.py results as a report file (less spam on comments)
            file_contents = "\n".join([line.decode("utf-8", "backslashreplace") for line in test_reports])
            upload_result = self.upload_file_to_canvas(filename="athina_%s%s.txt" % (user_id, user_values.commit_date),
                                                       user_id=user_id,
                                                       file_contents=file_contents)
            if upload_result["fileid"] != 0:
                if upload_result["public"] is False:
                    # Submit grade and comment referencing the file that was just uploaded
                    self.submit_grade_canvas(user_id=user_id, grade=grade, comment_file=upload_result["fileid"])
                else:
                    comment_text = "See file:\nhttps://wwu.instructure.com/files/%d/download?download_frd=1" %\
                                   upload_result["fileid"]
                    self.submit_grade_canvas(user_id=user_id,
                                             grade=grade,
                                             comment_text=comment_text)
            else:
                self.submit_grade_canvas(user_id=user_id,
                                         grade=grade,
                                         comment_text="An error has occurred with uploading comment file to Canvas.\n"
                                                      "Please contact the instructor.")
        else:
            self.submit_grade_canvas(user_id=user_id, grade=grade,
                                     comment_text="\n".join(
                                         [line.decode("utf-8", "backslashreplace") for line in test_reports]))

    def validate_response(self, data):
        if not isinstance(data, list) and isinstance(data, dict) and data.get('status', 0) == 'unauthenticated':
            self.logger.vprint("Incorrect response data from Canvas (bad authentication).")
            self.logger.vprint(data, debug=True)
            return False
        else:
            return True

    def get_assignment_due_date(self):
        """
        Get assignment due date
        """
        data = request_url(
            "https://%s/api/v1/courses/%d/assignments/%d" %
            (self.configuration.canvas_url, self.configuration.course_id, self.configuration.assignment_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token}, method="get")
        if not self.validate_response(data):
            return dateutil.parser.parse("2050-01-01 00:00:00 +00:00")  # a day in the future
        try:
            due_date = dateutil.parser.parse(data["due_at"])
        except TypeError:
            self.logger.vprint(
                "Type error for due date (probably not specified on elearning platform), using no due date.",
                debug=True)
            return dateutil.parser.parse("2050-01-01 00:00:00 +00:00")  # a day in the future

        return due_date

    @staticmethod
    def parse_canvas_submissions(data, user_data):
        if data["submitted_at"] is None:
            submitted_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        else:
            submitted_date = dateutil.parser.parse(data["submitted_at"])
        if user_data.db.get(data["user_id"], 0) == 0:
            user_data.db[data["user_id"]] = user_data.User(user_id=data["user_id"])
            user_data.db[data["user_id"]].repository_url = data["url"]
            user_data.db[data["user_id"]].url_date = submitted_date
            user_data.db[data["user_id"]].new_url = True
            user_data.db[data["user_id"]].commit_date = submitted_date
        else:
            # New submission will always happen on Canvas on a chronological order
            if user_data.db[data["user_id"]].url_date < submitted_date:
                user_data.db[data["user_id"]].repository_url = data["url"]
                user_data.db[data["user_id"]].url_date = submitted_date
                user_data.db[data["user_id"]].new_url = True
                user_data.db[data["user_id"]].commit_date = submitted_date
        return user_data

    def upload_params_for_comment_upload(self, filename, user_id):
        link_url = request_url(
            "https://%s/api/v1/courses/%d/assignments/%d/submissions/%d/comments/files" %
            (self.configuration.canvas_url, self.configuration.course_id, self.configuration.assignment_id, user_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token},
            payload={'name': filename},
            method="post")
        return link_url

    def upload_file_to_canvas(self, filename, user_id, file_contents):
        public = False
        self.logger.vprint("Attempting to upload file size: %d" % len(file_contents))
        link_url = self.upload_params_for_comment_upload(filename, user_id)
        return_url = self.upload(link_url, file_contents)
        if return_url.get("message", 0) == 'file size exceeds quota limits':
            # Try uploading to canvas generic folder instead (public)
            public = True
            link_url = self.upload_params_for_folder_upload(filename)
            return_url = self.upload(link_url, file_contents)
        # if we get the same error a second time then return 0
        if return_url.get("message", 0) != 'file size exceeds quota limits':
            return {'public': public, 'fileid': return_url["id"]}
        else:
            return {'public': public, 'fileid': 0}

    def submit_grade_canvas(self, user_id, grade, comment_text=None, comment_file=None):
        payload = {'submission[posted_grade]': grade}
        if comment_text is not None:
            payload['comment[text_comment]'] = comment_text
        if comment_file is not None:
            payload['comment[file_ids][]'] = comment_file
        request_url(
            "https://%s/api/v1/courses/%d/assignments/%d/submissions/%d" %
            (self.configuration.canvas_url, self.configuration.course_id, self.configuration.assignment_id, user_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token},
            payload=payload,
            method="put",
            return_type="json")

    def upload_params_for_folder_upload(self, filename):
        link_url = request_url(
            "https://%s/api/v1/courses/%d/files" % (self.configuration.canvas_url, self.configuration.course_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token},
            payload={'name': filename,
                     "parent_folder_path": "athina.py"},
            method="post")
        return link_url

    @staticmethod
    def upload(link_url, file_contents):
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
        data = request_url(
            "https://%s/api/v1/courses/%d/users/?per_page=150" % (self.configuration.canvas_url,
                                                                  self.configuration.course_id),
            {"Authorization": "Bearer %s" % self.configuration.auth_token}, method="get")
        if not self.validate_response(data):
            return users
        for record in data:
            try:
                users.db[record["id"]].secondary_id = record["login_id"]
                users.db[record["id"]].user_fullname = record["name"]
            except KeyError:
                continue
        return users
