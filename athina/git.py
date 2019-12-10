# Module for handling git repositories. The functions in this modules are meant to be copied over in order to extend
# support to other version control programs, e.g., svn
import subprocess
import re
import os
import html
import git
from datetime import datetime, timezone
from dateutil.tz import tzlocal
from urllib.parse import urlparse
from urllib.parse import quote_plus as urlquote
import dateutil.parser
from athina.users import *
from athina.url import *


def get_repo_commit(folder):
    repo = git.Repo(folder)
    try:
        return repo.heads.master.commit.hexsha
    except AttributeError:
        # repo with no commits
        return None


class Repository:
    logger = None
    configuration = None
    e_learning = None

    def __init__(self, logger, configuration, e_learning):
        self.logger = logger
        self.configuration = configuration
        self.e_learning = e_learning

    def check_error(self, err):
        if err != b'':
            self.logger.logger.warning("Testing script returned warning/error: %s" % err.decode("utf-8", "backslashreplace"))
            return True

    # TODO: change some of these commands to appropriate module commands, e.g., shutils, os etc.
    def clone_git_repo(self, user_id, user_object):
        subprocess.run(["rm", "-r", "-f", "%s/repodata%s/u%s" % (self.configuration.config_dir,
                                                                 self.configuration.assignment_id, user_id)])
        subprocess.run(["mkdir", "-p", "%s/repodata%s/u%s" % (self.configuration.config_dir,
                                                              self.configuration.assignment_id, user_id)])
        url_matches = re.findall("(.*?)://(.*?)$", user_object.repository_url)
        # If the submitted URL is not gitlab then don't submit the password (avoiding a phishing attack)
        # Implementing rule to enforce git_url to comply with what the athina.yaml (ie, what the instructor requested)
        self.logger.logger.debug("Attempting to clone %s" % user_object.repository_url)
        if re.match(r"^" + re.escape(self.configuration.git_url + "/") + r".*", url_matches[0][1]) and \
           url_matches[0][0] == 'https':
            if self.configuration.git_username != "":
                git_url = "%s://%s:%s@%s" % (url_matches[0][0],
                                             html.escape(self.configuration.git_username),
                                             html.escape(self.configuration.git_password),
                                             url_matches[0][1])
            else:
                git_url = user_object.repository_url
        else:
            self.logger.logger.error("Error: submitted url does not match git url domain in the configuration.")
            git_url = ""

        subprocess.run(["git", "clone", "%s" % git_url,
                        "%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                self.configuration.assignment_id, user_id)])
        self.set_webhook(user_object)

    def retrieve_last_commit_date(self, user_id):
        try:
            out, err = self.retrieve_git_log(user_id)
        except FileNotFoundError:
            self.logger.logger.debug("Error: file not found when obtaining commit date through git log.")
            return None

        if not self.check_error(err):
            # Retrieve, convert to utc and remove timezone info (needed for sqlite3 compatibility)
            return dateutil.parser.parse(out).astimezone(tzlocal()).replace(tzinfo=None)
        else:
            return None

    def check_repository_changes(self, user_id):
        user_values = return_a_student(self.configuration.course_id, self.configuration.assignment_id, user_id)
        changed_state = False

        # If nothing has been submitted no point in testing
        if user_values.repository_url is None or user_values.repository_url == "":
            changed_state = False
        elif user_values.new_url and user_values.same_url_flag and user_values.repository_url != "":  # Duplicate url
            # Submit grade
            self.logger.logger.warning("The URL is being used by another student. Will not test.")
            self.e_learning.submit_grade(user_id, user_values, 0,
                                         'The URL is being used by another student'.encode("utf-8"))
            user_values.new_url = False
            user_values.commit_date = datetime.now(tzlocal()).replace(tzinfo=None)
            user_values.save()
            changed_state = False  # do not process anything for this student
        elif user_values.new_url is True and user_values.same_url_flag is False:  # If record has changed -> new URL
            self.logger.logger.info("> New URL Submission: %s - %d" % (user_values.user_fullname, user_id))
            # Delete dir, create dir and clone repository
            self.clone_git_repo(user_id, user_values)
            if os.path.isdir("%s/repodata%s/u%s/.git" % (self.configuration.config_dir,
                                                          self.configuration.assignment_id, user_id)):
                # valid copy cloned successfully, moving on assuming the rest of the checks clear
                changed_state = self.compare_commit_date_with_due_date(user_id, user_values)
            else:
                self.logger.logger.error(">>> Could not clone the repository.")
                changed_state = False  # invalid copy, couldn't be cloned.
                # TODO: make the user aware with solutions on how to get their git accessible
        elif user_values.use_webhook is True and user_values.webhook_event is False:
            # This will prevent git pull unless and event has arrived or we do not use webhooks.
            changed_state = False
        else:
            # Pull and see if there is anything that changed,
            # then check date and compare with last  date
            try:
                process = subprocess.Popen(["git", "pull"],
                                           cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                                       self.configuration.assignment_id,
                                                                       user_id),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = process.communicate()
            except FileNotFoundError:
                # Attempt to git clone
                err = b"unresolved conflict"

            if b"unresolved conflict" in err:
                self.logger.logger.warning("Cannot pull due to unresolved conflicts...initiating git clone...")
                self.clone_git_repo(user_id, user_values)

            changed_state = self.compare_commit_date_with_due_date(user_id, user_values)

        user_values.changed_state = changed_state
        user_values.save()

        return changed_state

    def compare_commit_date_with_due_date(self, user_id, user_values):
        commit_date = self.retrieve_last_commit_date(user_id)
        self.logger.logger.debug("Retrieving latest git commit date from git log: %s" % commit_date)
        if commit_date is None:  # no repo or commit cannot be obtained
            commit_date = user_values.commit_date

        # If new commit date newer than old commit but smaller than due date
        self.logger.logger.debug("Checking due > git log commit > db commit: %s %s %s" % (self.configuration.due_date, commit_date, user_values.commit_date))
        if self.configuration.due_date > commit_date > user_values.commit_date:
            self.logger.logger.info(">> New commit on repo before due date")
            return True
        elif user_values.force_test is True:
            self.logger.logger.info(">> Force testing requested for user: %s." % user_id)
            return True
        else:
            return False

    def retrieve_git_log(self, user_id):
        process = subprocess.Popen(["git", "log", "-1", "--format=%ci"],
                                   cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                               self.configuration.assignment_id, user_id),
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
        return out, err

    def set_webhook(self, user_values):
        if self.configuration.athina_web_url is not None:
            self.logger.logger.info("Attempting to set webhook for user %s" % user_values.user_id)
            encoded_url = urlquote(re.sub('\.git$', '', urlparse(user_values.repository_url).path[1:]))
            data = request_url("https://%s/api/v4/projects/%s/hooks" % (self.configuration.git_url, encoded_url),
                               headers={"Authorization": "Bearer %s" % self.configuration.git_password},
                               payload={"id": encoded_url,
                                        "url": "https://%s/json" % self.configuration.athina_web_url,
                                        "push_events": "yes",
                                        "enable_ssl_verification": "no"
                                        }, method="post", return_type="json")
            if data.get("created_at", None) is not None:
                user_values.use_webhook = True
                self.logger.logger.info("Webhook was set, will expect event to verify changes in repository.")
            else:
                self.logger.logger.warning("Attempt to send webhook failed!")
                self.logger.logger.debug(data)
