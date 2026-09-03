# GitLab Issues adapter for non-Canvas grade output.
# Provides the same interface as Canvas for submit_grade / submit_comment,
# but creates GitLab issues in the STUDENT's repository (not the faculty repo).
import json
import re
from datetime import datetime
from urllib.parse import quote as url_quote

from athina.url import request_url
from athina.users import return_a_student

__all__ = ('GitLabIssues',)


class GitLabIssues:
    """
    Output adapter that posts grades and feedback as GitLab issues.
    Compatible with the Canvas interface used by Tester and Repository.
    """

    def __init__(self, configuration, logger):
        self.configuration = configuration
        self.logger = logger

    # ------------------------------------------------------------------ #
    #  Properties matching the Canvas interface                            #
    # ------------------------------------------------------------------ #

    @property
    def needs_update(self):
        """No periodic sync needed when outputting to GitLab issues."""
        return False

    def update_last_update(self):
        """No-op for GitLab issue output mode."""
        pass

    # ------------------------------------------------------------------ #
    #  Submission input (no-op — submissions come from the DB)             #
    # ------------------------------------------------------------------ #

    def get_all_submissions(self):
        """No-op — submissions are loaded from the local DB."""
        self.logger.logger.debug("GitLabIssues mode: skipping Canvas submission fetch.")
        return True

    def get_additional_user_info(self, users):
        """No-op — user info is already in the DB."""
        return users

    def get_assignment_due_date(self):
        """Return a far-future date when no Canvas due date is available."""
        return datetime(2050, 1, 1, 0, 0)

    # ------------------------------------------------------------------ #
    #  Grade & comment output                                             #
    # ------------------------------------------------------------------ #

    def submit_grade(self, user_id, user_values, grade, test_reports):
        """
        Create a GitLab issue with the grade and test report in the STUDENT's repo.
        Returns the issue IID (or 0 on failure).
        """
        # Resolve the student's GitLab project from their repository_url
        student_repo_url = getattr(user_values, 'repository_url', '') or ''
        project_path = self._repo_url_to_project_path(student_repo_url)
        if not project_path:
            self.logger.logger.warning(
                "Cannot determine student GitLab project from repo URL '%s' for user %s"
                % (student_repo_url, user_id))
            return 0

        # Build student display name: prefer full name, fall back to secondary_id (email), then user_id
        student_name = getattr(user_values, 'user_fullname', '') or ''
        if not student_name:
            student_name = getattr(user_values, 'secondary_id', '') or ''
        if not student_name:
            student_name = str(user_id)

        title = "%s — %s" % (student_name, self.configuration.gitlab_issues_title_prefix)

        # Build issue body
        report_lines = []
        for line in test_reports:
            if isinstance(line, bytes):
                report_lines.append(line.decode("utf-8", "backslashreplace"))
            else:
                report_lines.append(str(line))

        body = self._build_issue_body(
            user_id=user_id,
            student_name=student_name,
            grade=grade,
            total_points=self.configuration.total_points,
            report="\n".join(report_lines),
        )

        return self._create_issue(title, body, project_path)

    def submit_comment(self, user_id, comment):
        """
        Create a GitLab issue for a standalone comment in the student's repo.
        """
        student_obj = return_a_student(self.configuration.course_id,
                                       self.configuration.assignment_id,
                                       user_id)
        student_repo_url = getattr(student_obj, 'repository_url', '') or ''
        project_path = self._repo_url_to_project_path(student_repo_url)
        if not project_path:
            return 0

        student_name = getattr(student_obj, 'user_fullname', '') or ''
        if not student_name:
            student_name = getattr(student_obj, 'secondary_id', '') or ''
        if not student_name:
            student_name = str(user_id)

        title = "%s — %s" % (student_name, self.configuration.gitlab_issues_title_prefix)
        return self._create_issue(title, str(comment), project_path)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _build_issue_body(self, user_id, student_name, grade, total_points, report):
        """Compose a Markdown-formatted issue body."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        confidential_note = ""
        if self.configuration.gitlab_issues_confidential:
            confidential_note = " *(confidential)*"

        body = (
            "## Grade Report%s\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| **Student** | %s |\n"
            "| **User ID** | %s |\n"
            "| **Grade** | %d / %d |\n"
            "| **Date** | %s |\n\n"
            "---\n\n"
            "### Test Output\n\n"
            "```\n"
            "%s\n"
            "```\n"
        ) % (confidential_note, student_name, user_id, grade, total_points, now, report)
        return body

    def _create_issue(self, title, body, project_path):
        """
        POST a new issue to a GitLab project via the v4 API.
        project_path: URL-encoded GitLab project path (e.g. 'group%2Frepo').
        Returns the issue IID (or 0 on failure).
        """
        git_url = self.configuration.git_url
        token = self.configuration.git_password

        url = "https://%s/api/v4/projects/%s/issues" % (git_url, project_path)
        headers = {"Authorization": "Bearer %s" % token}
        payload = {
            "title": title,
            "description": body,
            "confidential": str(self.configuration.gitlab_issues_confidential).lower(),
        }

        self.logger.logger.debug("Creating GitLab issue: %s (project: %s)" % (title, project_path))
        result = request_url(url, headers=headers, payload=payload,
                             method="post", return_type="json")

        if result and result.get("iid"):
            self.logger.logger.info("GitLab issue created: #%s" % result["iid"])
            return result["iid"]
        else:
            self.logger.logger.error(
                "Failed to create GitLab issue '%s'. Response: %s" % (title, result))
            return 0

    def create_initial_issue(self, user_id, user_values, project_path):
        """
        Create an initial 'Test in progress' issue in the student's repo.
        Returns the issue IID (or 0 on failure).
        """
        if not project_path:
            return 0

        student_name = getattr(user_values, 'user_fullname', '') or ''
        if not student_name:
            student_name = getattr(user_values, 'secondary_id', '') or ''
        if not student_name:
            student_name = str(user_id)

        title = "%s — %s" % (student_name, self.configuration.gitlab_issues_title_prefix)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        body = (
            "## ⏳ Test in Progress\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| **Student** | %s |\n"
            "| **User ID** | %s |\n"
            "| **Started** | %s |\n\n"
            "---\n\n"
            "Tests are running. This issue will be updated with results when complete.\n"
        ) % (student_name, user_id, now)

        return self._create_issue(title, body, project_path)

    def update_issue(self, issue_iid, title, body, project_path):
        """
        Update an existing GitLab issue via the v4 API (PATCH).
        """
        if not issue_iid or not project_path:
            return False

        git_url = self.configuration.git_url
        token = self.configuration.git_password

        url = "https://%s/api/v4/projects/%s/issues/%s" % (git_url, project_path, issue_iid)
        headers = {"Authorization": "Bearer %s" % token}
        payload = {
            "title": title,
            "description": body,
        }

        self.logger.logger.debug("Updating GitLab issue #%s (project: %s)" % (issue_iid, project_path))
        result = request_url(url, headers=headers, payload=payload,
                             method="put", return_type="json")

        if result and result.get("iid"):
            self.logger.logger.info("GitLab issue updated: #%s" % result["iid"])
            return True
        else:
            self.logger.logger.error(
                "Failed to update GitLab issue #%s. Response: %s" % (issue_iid, result))
            return False

    @staticmethod
    def _repo_url_to_project_path(repo_url):
        """
        Extract the URL-encoded project path from a GitLab repository URL.
        E.g. 'https://gitlab.cs.wwu.edu/group/repo.git' → 'group%2Frepo'
        Strips credentials, scheme, trailing .git, and trailing slashes.
        """
        if not repo_url:
            return ""
        # Strip credentials: https://user:pass@host/path → https://host/path
        cleaned = re.sub(r'://[^@]+@', '://', repo_url)
        # Remove trailing .git and slashes
        cleaned = re.sub(r'\.git/?$', '', cleaned)
        # Extract path after host: https://host/Group/Repo → Group/Repo
        match = re.search(r'://[^/]+/(.+)', cleaned)
        if not match:
            return ""
        path = match.group(1).strip('/')
        if not path:
            return ""
        return url_quote(path, safe='')
