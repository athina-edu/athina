# Module for handling git repositories. The functions in this modules are meant to be copied over in order to extend
# support to other version control programs, e.g., svn
import html
import os
import subprocess
import time
import abc
from datetime import datetime
from urllib.parse import urlparse

import dateutil.parser
import git
from dateutil.tz import tzlocal

from athina.git.gitlab import *
from athina.users import *

__all__ = ('get_repo_commit', 'make_proper_git_url', 'Repository',)


def get_repo_commit(folder):
    repo = git.Repo(folder)
    try:
        return repo.heads.master.commit.hexsha
    except AttributeError:
        # repo with no commits
        return None


def make_proper_git_url(url):
    if type(url) is str:
        if url[-4:] != ".git":
            return url + ".git"
        else:
            return url
    else:
        return url


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
    def _clone_git_repo(self, user_id, user_object):
        time.sleep(0.5)  # Delay so that requests won't be rejected by github or gitlab
        subprocess.run(["rm", "-r", "-f", "%s/repodata%s/u%s" % (self.configuration.config_dir,
                                                                 self.configuration.assignment_id, user_id)])
        subprocess.run(["mkdir", "-p", "%s/repodata%s/u%s" % (self.configuration.config_dir,
                                                              self.configuration.assignment_id, user_id)])
        parsed_url = urlparse(user_object.repository_url)
        # If the submitted URL is not gitlab then don't submit the password (avoiding a phishing attack)
        # Implementing rule to enforce git_url to comply with what the athina.yaml (ie, what the instructor requested)
        self.logger.logger.debug("Attempting to clone %s" % user_object.repository_url)
        if parsed_url.netloc.encode("ascii") == self.configuration.git_url.encode("ascii"):
            if self.configuration.git_username != "" and parsed_url.scheme == 'https':
                git_url = "%s://%s:%s@%s%s" % (parsed_url.scheme, html.escape(self.configuration.git_username),
                                               html.escape(self.configuration.git_password), parsed_url.netloc,
                                               parsed_url.path)
            else:
                git_url = user_object.repository_url
        else:
            self.logger.logger.error("Error: submitted url does not match git url domain in the configuration.")
            git_url = ""

        subprocess.run(["git", "clone", "%s" % git_url,
                        "%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                self.configuration.assignment_id, user_id)])
        if git_url != "" and self.configuration.use_webhook is True:
            gitlab_set_webhook(self.configuration, self.logger, user_object)

    def retrieve_last_commit_date(self, user_id):
        try:
            out, err = self._retrieve_git_log(user_id)
        except FileNotFoundError:
            self.logger.logger.debug("Error: file not found when obtaining commit date through git log.")
            return None

        if not self.check_error(err):
            # Retrieve, convert to utc and remove timezone info (needed for sqlite3 compatibility)
            return dateutil.parser.parse(out).astimezone(tzlocal()).replace(tzinfo=None)
        else:
            return None

    def _submit_will_not_process(self, user_values, msg=""):
        self.logger.logger.warning(msg)
        self.e_learning.submit_grade(user_values.user_id, user_values, 0,
                                     [msg.encode("utf-8")])
        user_values.new_url = False
        user_values.commit_date = datetime.now(tzlocal()).replace(tzinfo=None)
        user_values.save()

    def check_repository_changes(self, user_id):
        self.logger.logger.debug("Checking user %s" % user_id)
        user_values = return_a_student(self.configuration.course_id, self.configuration.assignment_id, user_id)
        changed_state = False

        # If nothing has been submitted no point in testing
        if user_values.repository_url in {None, ""}:
            self.logger.logger.debug("No url for %s" % user_id)
            changed_state = False
        elif user_values.new_url and user_values.same_url_flag and user_values.repository_url != "":  # Duplicate url
            self._submit_will_not_process(user_values, "The URL is being used by another student. Will not test.")
            changed_state = False  # do not process anything for this student
        elif user_values.new_url and \
                gitlab_check_if_repo_private(self.configuration, self.logger, user_values.repository_url) is False:
            self._submit_will_not_process(user_values, "The git repository is not private! Aborting checks.")
            changed_state = False  # do not process anything for this student
        elif user_values.new_url is True and user_values.same_url_flag is False:  # If record has changed -> new URL
            self.logger.logger.info("> New URL Submission: %s - %d" % (user_values.user_fullname, user_id))
            self._clone_git_repo(user_id, user_values)
            if os.path.isdir("%s/repodata%s/u%s/.git" % (self.configuration.config_dir,
                                                         self.configuration.assignment_id, user_id)):
                # valid copy cloned successfully, moving on assuming the rest of the checks clear
                changed_state = self._compare_commit_date_with_due_date(user_id, user_values)
            else:
                msg = """Your git url cannot be cloned. Verify that you have granted permissions
                      to the instructor of the course and that you have submitted the proper'
                      git url ending in .git (not ../tree/master). Check the assignment instructions
                      and make sure you use the right git hosting platform (%s).""" % self.configuration.git_url
                self._submit_will_not_process(user_values, msg)
                changed_state = False  # invalid copy, couldn't be cloned.
        elif user_values.use_webhook is True and user_values.webhook_event is False and\
                user_values.force_test is False and self.configuration.use_webhook is True:
            # This will prevent git pull unless and event has arrived or we do not use webhooks.
            self.logger.logger.debug("Hook is enabled and set for user %s" % user_id)
            changed_state = False
        else:
            # Pull and see if there is anything that changed,
            # then check date and compare with last date
            out, err = self._pull_git_repo(user_id)

            if b"unresolved conflict" in err:
                self.logger.logger.warning("Cannot pull due to unresolved conflicts...initiating git clone...")
                self._clone_git_repo(user_id, user_values)

            changed_state = self._compare_commit_date_with_due_date(user_id, user_values)

        user_values.webhook_event = False  # Either way after processing this should be set to false
        user_values.changed_state = changed_state
        user_values.save()

        return changed_state

    def _pull_git_repo(self, user_id):
        time.sleep(0.5)  # Delay so that requests won't be rejected by github or gitlab
        try:
            process = subprocess.Popen(["git", "pull"],
                                       cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                                   self.configuration.assignment_id,
                                                                   user_id),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate()
        except FileNotFoundError:
            # Attempt to git clone
            out = ""
            err = b"unresolved conflict"
        return out, err

    def _compare_commit_date_with_due_date(self, user_id, user_values):
        commit_date = self.retrieve_last_commit_date(user_id)
        self.logger.logger.debug("Retrieving latest git commit date from git log: %s" % commit_date)
        if commit_date is None:  # no repo or commit cannot be obtained
            commit_date = user_values.commit_date

        # If new commit date newer than old commit but smaller than due date
        self.logger.logger.debug("Checking due > git log commit > db commit: %s %s %s" % (self.configuration.due_date,
                                                                                          commit_date,
                                                                                          user_values.commit_date))
        if self.configuration.due_date > commit_date > user_values.commit_date:
            self.logger.logger.info(">> New commit on repo before due date")
            return True
        elif user_values.force_test is True:
            self.logger.logger.info(">> Force testing requested for user: %s." % user_id)
            return True
        else:
            return False

    def _retrieve_git_log(self, user_id):
        process = subprocess.Popen(["git", "log", "-1", "--format=%ci"],
                                   cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                               self.configuration.assignment_id, user_id),
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
        return out, err

    # Chain of Responsibility Pattern (below)

    # A sort of factory method to pass the outer class to the inner classes (so that functions and vars are available)
    def _create_handler(self, class_name, successor=None):
        return class_name(successor, self)

    # Abstract method for generating the if/else handlers
    class Handler(metaclass=abc.ABCMeta):
        def __init__(self, successor=None, repository_ref=None):
            self._successor = successor
            self._repository_ref = repository_ref

        @abc.abstractmethod
        def handle_request(self):
            pass

    # TODO: specify user_values, user_id variables in Repository
    class EmptyRepositoryHandler(Handler):
        def handle_request(self):
            if self._repository_ref.user_values.repository_url in {None, ""}:
                self._repository_ref.logger.logger.debug("No url for %s" % self._repository_ref.user_id)
                return False
            elif self._successor is not None:
                self._successor.handle_request()

