# Module for handling git repositories. The functions in this modules are meant to be copied over in order to extend
# support to other version control programs, e.g., svn
import subprocess
import re
import os
import html
from datetime import datetime, timezone
import dateutil.parser


class Repository:
    user_data = None
    logger = None
    configuration = None
    e_learning = None

    def __init__(self, user_data, logger, configuration, e_learning):
        self.user_data = user_data
        self.logger = logger
        self.configuration = configuration
        self.e_learning = e_learning

    def check_error(self, err):
        if err != b'':
            self.logger.vprint("Testing script returned error: %s" % err.decode("utf-8", "backslashreplace"))
            return True

    # TODO: change some of these commands to appropriate module commands, e.g., shutils, os etc.
    def clone_git_repo(self, user_id, user_object):
        subprocess.run(["rm", "-r", "-f", "%s/repodata%s/u%s" % (self.configuration.config_dir,
                                                                 self.configuration.assignment_id, user_id)])
        subprocess.run(["mkdir", "-p", "%s/repodata%s/u%s" % (self.configuration.config_dir,
                                                              self.configuration.assignment_id, user_id)])
        url_matches = re.findall("(.*?)://(.*?)$", user_object.repository_url)
        # If the submitted URL is not gitlab then don't submit the password (avoiding a phishing attack)
        if re.match(r"^" + re.escape(self.configuration.git_url + "/") + r".*", url_matches[0][1]) and \
           url_matches[0][0] == 'https':
            git_url = "%s://%s:%s@%s" % (url_matches[0][0],
                                         html.escape(self.configuration.git_username),
                                         html.escape(self.configuration.git_password),
                                         url_matches[0][1])
        else:
            git_url = user_object.repository_url
        subprocess.run(["git", "clone", "%s" % git_url,
                        "%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                self.configuration.assignment_id, user_id)])

    def retrieve_last_commit_date(self, user_id):
        process = subprocess.Popen(["git", "log", "-1", "--format=%ci"],
                                   cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                               self.configuration.assignment_id, user_id),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()

        if not self.check_error(err):
            return dateutil.parser.parse(out)
        else:
            return None

    def check_repository_changes(self, user_id):
        user_values = self.user_data.db[user_id]

        # If nothing has been submitted no point in testing
        if user_values.repository_url is None or user_values.repository_url == "":
            return False

        # Check for changes
        user_values.changed_state = False

        # If a previous commit has surpassed the due date then no future commit will work either (if date is enforced)
        if user_values.commit_date > self.configuration.due_date:
            return False

        if user_values.new_url and user_values.same_url_flag and user_values.repository_url != "":  # Duplicate url
            # Submit grade
            self.logger.vprint("The URL is being used by another student")
            # TODO: Command below has not been verified that it works. Needs to be verified (good for testing dev)
            self.e_learning.submit_grade(user_id, user_values, 0, 'The URL is being used by another student')
            self.user_data.db[user_id].new_url = False
            self.user_data.db[user_id].commit_date = datetime.now(timezone.utc)
            return False  # do not process anything for this student

        if user_values.new_url is True and user_values.same_url_flag is False:  # If record has changed -> new URL
            self.logger.vprint(">> New Submission: %s - %d" % (user_values.user_fullname, user_id))
            # Delete dir, create dir and clone repository
            self.clone_git_repo(user_id, user_values)
            if os.path.isdir("%s/repodata%s/u%s/.git" % (self.configuration.config_dir,
                                                          self.configuration.assignment_id, user_id)):
                user_values.changed_state = True  # valid copy cloned successfully, moving on
                return True
            else:
                user_values.changed_state = False  # invalid copy, couldn't be cloned.
                self.logger.vprint(">>> Could not clone the repository.")
                return False
                # TODO: make the user aware with solutions on how to get their git accessible
        else:
            # Pull and see if there is anything that changed,
            # then check date and compare with last  date
            process = subprocess.Popen(["git", "pull"], cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                                                    self.configuration.assignment_id,
                                                                                    user_id),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate()
            if b"unresolved conflict" in err:
                self.logger.vprint("Cannot pull due to unresolved conflicts...initiating git clone...")
                self.clone_git_repo(user_id, user_values)
            process = subprocess.Popen(["git", "log", "-1", "--format=%ci"],
                                       cwd="%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                                   self.configuration.assignment_id, user_id),
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            out, err = process.communicate()
            if self.check_error(err):  # no repo or commit cannot be obtained
                commit_date = user_values.commit_date
            else:
                commit_date = dateutil.parser.parse(out)

            if commit_date > user_values.commit_date:
                self.logger.vprint(">> New Commit on Repo")
                user_values.changed_state = True
                user_values.commit_date = commit_date  # This helps with the test that follows, value is updated later
                return True
            else:
                return False

