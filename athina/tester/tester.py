import os
import shutil
import signal
import time
from datetime import timedelta, datetime
from random import uniform

import psutil
from dateutil.tz import tzlocal

from athina.file_functions import *
from athina.tester.docker import *
from athina.tester.firejail import *
from athina.users import *
from athina.users import Database, Users

__all__ = ('Tester',)


# There is extensive forking that occurs for each student testing from the tester
# We set an async signal handling for terminating zombie processes
def _cleanup_zombie(signum, frame):
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        pass


# Set the signal for children to call the function
signal.signal(signal.SIGCHLD, _cleanup_zombie)


class Tester:
    user_data = None
    logger = None
    configuration = None
    e_learning = None
    repository = None

    def __init__(self, user_data, logger, configuration, e_learning, repository):
        self.user_data = user_data
        self.logger = logger
        self.configuration = configuration
        self.e_learning = e_learning
        self.repository = repository

    def _trim_test_output(self, out):
        if len(out) > self.configuration.max_file_size:
            # Adding the last three lines just in case (the score should be in one of these)
            lines = out.split(b"\n")[-3] + b"\n" + out.split(b"\n")[-2] + b"\n" + out.split(b"\n")[-1]
            out = out[0:self.configuration.max_file_size]  # keep only a limited amount
            out += b"\n...Rest of the text truncated as it reached max size limit...\n"
            out += lines
            return out
        else:
            return out

    def _update_user_db(self, user_object, commit_date_being_tested):
        user_object.plagiarism_to_grade = True
        user_object.last_graded = datetime.now(tzlocal()).replace(tzinfo=None).replace(microsecond=0)

        # Update commit date on record
        if self.configuration.no_repo is False:
            if commit_date_being_tested is not None:
                user_object.new_url = False
                user_object.commit_date = commit_date_being_tested
            else:
                user_object.new_url = False

        return user_object  # returning the user object

    def _tester_is_inactive(self, user_id):
        user_object = return_a_student(self.configuration.course_id, self.configuration.assignment_id, user_id)
        return user_object.tester_active is False or (
                user_object.tester_date + timedelta(hours=1) <= datetime.now(tzlocal()).replace(tzinfo=None))

    def _tester_lock_unlock(self, user_id, lock=True):
        user_object = return_a_student(self.configuration.course_id, self.configuration.assignment_id, user_id)
        if user_object.repository_url in {"", None}:
            Users.update(tester_active=lock, tester_date=datetime.now(tzlocal()).replace(tzinfo=None)).where(
                Users.user_id == user_id,
                Users.course_id == user_object.course_id,
                Users.assignment_id == user_object.assignment_id
            ).execute()
        else:
            Users.update(tester_active=lock, tester_date=datetime.now(tzlocal()).replace(tzinfo=None)).where(
                Users.repository_url == user_object.repository_url,
                Users.course_id == user_object.course_id,
                Users.assignment_id == user_object.assignment_id
            ).execute()
        return user_id

    @staticmethod
    def _get_group_user_list(user_object):
        # Check if the user that was just graded had a url (Participation assignments won't have this
        if user_object.repository_url is None:
            user_list = [(user_object.user_id, user_object)]
        else:
            # Get group members whose repository url is the same (for group assignments)
            user_list = [(current_user_object.user_id, current_user_object) for current_user_object
                         in return_all_students(user_object.course_id, user_object.assignment_id)
                         if current_user_object.repository_url == user_object.repository_url]
        return user_list

    def process_student_assignment(self, user_id, forced_testing=False):
        # Reconnect logger if missing (e.g., parallel run)
        if self.logger.logger is None:
            self.logger.create_logger()

        # Acquire DB connection if missing (e.g., parallel run)
        self.user_data = Database() if self.user_data is None else self.user_data
        user_object = return_a_student(self.configuration.course_id, self.configuration.assignment_id, user_id)

        # Wait until CPU is available (expand this to check RAM and disk IO availability)
        while psutil.cpu_percent() > 85 or psutil.virtual_memory()[2] > 80:
            time.sleep(uniform(0.5, 1))

        self.logger.logger.info("> Checking %s - %d" % (user_object.user_fullname, user_id))

        # Boolean arguments broken up into logical components to reduce the size of if statement below
        commit_date_being_tested = self.repository.retrieve_last_commit_date(user_object.user_id)
        if commit_date_being_tested is not None:
            self.logger.logger.debug("Commit Due Date Comparison: %s < %s ?" % (
                commit_date_being_tested.strftime("%Y-%m-%d %H:%M:%S"),
                self.configuration.due_date.strftime("%Y-%m-%d %H:%M:%S")))
        else:
            self.logger.logger.debug("Commit Due Date Comparison: no commit date on repo")
            # If we cannot read a git log time then something is wrong with the repo or it is a nonrepo assignment
            commit_date_being_tested = datetime(1, 1, 1, 0, 0)

        self.logger.logger.debug("Verifying: changed_state - %r, url_date - %s" % (user_object.changed_state,
                                                                                   user_object.url_date))
        repo_mode_conditions = (user_object.changed_state and
                                user_object.url_date < self.configuration.due_date and
                                commit_date_being_tested < self.configuration.due_date and
                                user_object.same_url_flag is not True)
        no_repo_mode_conditions = (self.configuration.no_repo is True and user_object.last_graded +
                                   timedelta(hours=self.configuration.grade_update_frequency) <=
                                   datetime.now(tzlocal()).replace(tzinfo=None))

        if user_object.force_test or repo_mode_conditions or forced_testing or no_repo_mode_conditions:
            self.logger.logger.info(">> Testing")

            # Disable force testing just in case it was active
            if user_object.force_test:
                user_object.force_test = False
                user_object.save()

            # Run tests
            test_grades = []
            test_reports = []

            for x in range(0, len(self.configuration.test_scripts)):
                # Run individual test, add results in test_reports and test_grades
                self._run_test(x, test_reports, test_grades, user_object)

            # sum and scale grades from tests
            grade = round((sum(test_grades)) * self.configuration.total_points)
            test_reports.append(
                b"\nNote: Maximum possible grade is %d, the rest is graded manually by the instructor.\n" %
                self.configuration.total_points)

            user_list = self._get_group_user_list(user_object)

            user_object_list = []  # Return object for multiple user_objects (important for parallel - testing)

            # Submitting grades for as many students that share the repository url (depending on how many are permitted)
            submitted_once = False
            for current_user_id, current_user_object in user_list:
                # Submit grade
                if self.configuration.grade_publish and\
                        ((self.configuration.group_assignment is True and submitted_once is False) or
                         self.configuration.group_assignment is False):
                    self.e_learning.submit_grade(user_id=current_user_id, user_values=current_user_object, grade=grade,
                                                 test_reports=test_reports)
                    submitted_once = True
                else:  # print instead
                    for text in test_reports:
                        self.logger.logger.info(text.decode("utf-8", "backslashreplace"))
                self.logger.logger.info(">>> Submitting new grade for %s: %s" % (current_user_id, grade))

                # Update the user db that grades have been submitted
                self._update_user_db(current_user_object, commit_date_being_tested)
                current_user_object.changed_state = False
                current_user_object.last_grade = grade
                current_user_object.last_report = "\n".join([test.decode("utf-8", "backslashreplace") for test
                                                             in test_reports])
                current_user_object.save()
                user_object_list.append(current_user_object)
        else:
            self.logger.logger.info(">> No changes or past due date")
            user_object.changed_state = False

            user_object.save()
            user_object_list = [user_object]  # return list of the current object

        self.logger.logger.info("Testing Completed for %d" % user_object.user_id)

        return user_object_list  # return the list of all updated objects

    def _run_test(self, x, test_reports, test_grades, user_object):
        test_reports.append(("Test %d with weight %.2f" %
                             (x + 1, self.configuration.test_weights[x])).encode("utf-8"))
        test_script = self.configuration.test_scripts[x]

        # Create student and tmp dir
        time_field = "%s-%s" % (time.time(), os.getpid())
        self.configuration.athina_student_code_dir = "/tmp/athina%s" % time_field
        self.configuration.athina_test_tmp_dir = "/tmp/athina-test%s" % time_field

        # Copy student repo to tmp directory for testing (omit hidden files for security purposes, e.g., .git)
        rm_dir(self.configuration.athina_student_code_dir)
        if self.configuration.no_repo is False:
            copy_dir('%s/repodata%s/u%s' % (self.configuration.config_dir, self.configuration.assignment_id,
                                            user_object.user_id),
                     '%s' % self.configuration.athina_student_code_dir)
        else:
            # Docker tends to create mount points which result in root folders that then cannot be deleted
            # The code below solves this problem.
            os.mkdir('%s' % self.configuration.athina_student_code_dir, mode=0o777)

        # Copy tests in tmp folder
        rm_dir(self.configuration.athina_test_tmp_dir)
        copy_dir('%s/tests' % self.configuration.config_dir, '%s' % self.configuration.athina_test_tmp_dir)

        if self.configuration.pass_extra_params is True:
            self.configuration.extra_params = [user_object.secondary_id,
                                               self.configuration.due_date.astimezone(tzlocal()).isoformat()]
        else:
            self.configuration.extra_params = [self.configuration.athina_student_code_dir,
                                               self.configuration.athina_test_tmp_dir]

        # Execute using docker or firejail (depending on what the settings are)
        if self.configuration.use_docker is True:
            if os.path.isfile("%s/%s" % (self.configuration.config_dir, "Dockerfile")):
                out, err = docker_run(test_script, configuration=self.configuration, logger=self.logger)
            else:
                out, err = b"0", b"Missing Dockerfile (contact instructor)"
        else:
            out, err = execute_with_firejail(self.configuration, test_script, self.logger)

        # Clear temp directories
        rm_dir(self.configuration.athina_test_tmp_dir)
        rm_dir(self.configuration.athina_student_code_dir)

        # If we cannot find a number returned at the end of this list
        try:
            score = float(out.decode("utf-8", "backslashreplace").split("\n")[-2])
        except (IndexError, UnicodeDecodeError, ValueError):  # generated when errors exist in the test script
            score = None

        out = self._trim_test_output(out)
        test_reports.append(out)
        if self.repository.check_error(err) and score is None:
            test_grades.append(0.0)
            test_reports.append("Errors & Warnings:\n {0}".format(err.decode("utf-8", "backslashreplace")).
                                encode("utf-8"))
        elif score is None:
            test_grades.append(0.0)
            test_reports.append(
                """Test failed:
                This can happen if you have an infinite loop or non terminating loop,
                a missing library that the system doesn't have (contact the instructor)
                non-legible script grade due to some other unforeseen circumstance.""".encode("utf-8"))
        else:
            # scaling between 0 and 1
            test_grades.append(score / 100 * self.configuration.test_weights[x])

    def start_testing_db(self):
        self.logger.logger.info("Pre-fetching all user repositories")
        # Pre-fetching is important for group assignments where grade is submitted to multiple members and all
        # user dbs need to be updated later on
        reverse_repository_index = dict()

        for user in return_all_students(self.configuration.course_id, self.configuration.assignment_id):
            if self.configuration.no_repo is not True:
                if user.repository_url is not None:
                    if self._tester_is_inactive(user.user_id):
                        self.repository.check_repository_changes(user.user_id)
                        # Create a reverse dictionary and obtain one name from a group (in case of group assignments)
                        # Process group assignment will test once and then it identifies and submits a grade for both
                        # groups
                        reverse_repository_index[user.repository_url] = user.user_id
                    else:
                        self.logger.logger.warning("Tester active for user %s" % user.user_id)
            else:
                # When no repo is involved it is 1 to 1 testing (individual assignment)
                reverse_repository_index[user.user_id] = user.user_id

        processing_list = [[user.user_id, user] for user in
                           return_all_students(self.configuration.course_id, self.configuration.assignment_id)
                           if user.user_id in reverse_repository_index.values()]
        del reverse_repository_index

        # If we utilize docker we need to pre-build the docker container
        if self.configuration.use_docker:
            # Build if something has changed (i.e., new commit)
            docker_build(configuration=self.configuration, logger=self.logger)

        user_ids = [key for key, value in processing_list]
        self._spawn_worker(user_ids)

    def _spawn_worker(self, user_ids):
        # Lock all userids. Mark all users that use the same repository (i.e., groups) as being actively tested
        self.logger.logger.debug("Processing the following users: %s" % user_ids)
        user_ids = [self._tester_lock_unlock(student_id, lock=True) for student_id in user_ids
                    if self._tester_is_inactive(student_id)]
        self.logger.logger.debug("The following users do not have active testers: %s" % user_ids)

        # For parallel/threaded runs database objects have to be dropped. Same for logger
        del self.user_data
        self.logger.delete_logger()

        # fork(). Loop through each student in list and fork (main routine continues)
        for student_id in user_ids:
            new_pid = os.fork()
            if new_pid == 0:
                # Child becomes operational
                self.process_student_assignment(student_id)
                self._tester_lock_unlock(student_id, lock=False)  # Release the lock for user
                del self.user_data  # Delete in child process the db connection
                os._exit(0)  # Terminating the child (pytest compatible)

        self.logger.create_logger()
        self.user_data = Database()
