from athina.firejail import *
from datetime import timedelta, datetime, timezone
from athina.tester.docker import *
import subprocess
import glob
import time
import shutil
import np
import multiprocessing

# Modifiable loading
from athina.moss import Plagiarism
from athina.users import Database, Users


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

    def trim_test_output(self, out):
        if len(out) > self.configuration.max_file_size:
            # Adding the last three lines just in case (the score should be in one of these)
            lines = out.split(b"\n")[-3] + b"\n" + out.split(b"\n")[-2] + b"\n" + out.split(b"\n")[-1]
            out = out[0:self.configuration.max_file_size]  # keep only a limited amount
            out += b"\n...Rest of the text truncated as it reached max size limit...\n"
            out += lines
            return out
        else:
            return out

    def rm_dir(self, folder):
        try:
            shutil.rmtree(folder)
        except PermissionError:
            self.logger.logger.error("Cannot delete %s. Likely permissions error." % folder)
            raise PermissionError(folder)
        except FileNotFoundError:
            pass

    def update_user_db(self, user_object):
        user_object.plagiarism_to_grade = True
        user_object.last_graded = datetime.now(timezone.utc).replace(tzinfo=None)

        # Update commit date on record
        if self.configuration.no_repo is False:
            last_commit_date = self.repository.retrieve_last_commit_date(user_object.user_id)
            if last_commit_date is not None:
                user_object.new_url = False
                user_object.commit_date = last_commit_date
            else:
                user_object.new_url = False

        return user_object  # returning the user object

    def copy_dir(self, source, destination):
        try:
            shutil.copytree(source, destination)
        except FileNotFoundError:
            self.logger.logger.error("Could not copy %s to %s" % (source, destination))

    def process_student_assignment(self, user_id, forced_testing=False):
        # Reconnect logger if missing (e.g., parallel run)
        if self.logger.logger is None:
            self.logger.create_logger()

        # Acquire DB connection if missing (e.g., parallel run)
        self.user_data = Database(self.configuration.db_filename) if self.user_data is None else self.user_data
        user_object = Users.get(Users.user_id == user_id)

        self.logger.logger.info("> Checking %s - %d" % (user_object.user_fullname, user_id))

        # Code has changed and due date for submissions does not exceed submitted dates
        self.logger.logger.debug("Commit Due Date Comparison: %s < %s ?" % (
            user_object.commit_date.strftime("%Y-%m-%d %H:%M:%S"),
            self.configuration.due_date.strftime("%Y-%m-%d %H:%M:%S")))

        # Boolean arguments broken up into logical components to reduce the size of if statement below
        repo_mode_conditions = True if (user_object.changed_state and
                                        user_object.url_date < self.configuration.due_date and
                                        user_object.commit_date < self.configuration.due_date and
                                        user_object.same_url_flag is not True) else False
        no_repo_mode_conditions = True if (self.configuration.no_repo is True and
                                           user_object.last_graded +
                                           timedelta(hours=self.configuration.grade_update_frequency) <=
                                           datetime.now(timezone.utc).replace(tzinfo=None)) else False

        if repo_mode_conditions or forced_testing or no_repo_mode_conditions:
            self.logger.logger.info(">> Testing")

            # Run tests
            test_grades = []
            test_reports = []

            for x in range(0, len(self.configuration.test_scripts)):
                # Run individual test, add results in test_reports and test_grades
                self.run_test(x, test_reports, test_grades, user_object)

            # sum and scale grades from tests
            grade = round((sum(test_grades)) * self.configuration.total_points)
            test_reports.append(
                b"\nNote: Maximum possible grade is %d, the rest is graded manually by the instructor.\n" %
                self.configuration.total_points)

            # Check if the user that was just graded had a url (Participation assignments won't have this
            if user_object.repository_url is None:
                user_list = [(user_id, user_object)]
            else:
                # Get group members whose repository url is the same (for group assignments)
                user_list = [(current_user_object.user_id, current_user_object) for current_user_object
                             in Users.select()
                             if current_user_object.repository_url == user_object.repository_url]

            user_object_list = []  # Return object for multiple user_objects (important for parallel - testing)

            # Submitting grades for as many students that share the repository url (depending on how many are permitted)
            for current_user_id, current_user_object in user_list:
                # Submit grade
                if not self.configuration.simulate and self.configuration.grade_publish:
                    self.e_learning.submit_grade(user_id=current_user_id, user_values=current_user_object, grade=grade,
                                                 test_reports=test_reports)
                else:  # print instead
                    for text in test_reports:
                        self.logger.logger.info(text.decode("utf-8", "backslashreplace"))
                self.logger.logger.info(">>> Submitting new grade for %s: %s" % (current_user_id, grade))

                # Update the user db that grades have been submitted
                self.update_user_db(current_user_object)
                current_user_object.changed_state = False
                current_user_object.last_grade = grade
                current_user_object.last_report = "\n".join([test.decode("utf-8", "backslashreplace") for test
                                                             in test_reports])
                if not self.configuration.simulate:
                    current_user_object.save()
                user_object_list.append(current_user_object)

            return user_object_list  # return the list of all updated objects
        else:
            self.logger.logger.info(">> No changes or past due date")
            user_object.changed_state = False

            if not self.configuration.simulate:
                user_object.save()
            return [user_object]  # return list of the current object

    def run_test(self, x, test_reports, test_grades, user_object):
        test_reports.append(("Test %d with weight %.2f" %
                             (x + 1, self.configuration.test_weights[x])).encode("utf-8"))
        test_script = self.configuration.test_scripts[x]

        # If we are not running things in parallel, default will do
        # (ensures backwards compatibility) with non parallelized tests
        time_field = "" if self.configuration.processes < 2 else "%s-%s" % (time.time(), os.getpid())
        self.configuration.athina_student_code_dir = "/tmp/athina%s" % time_field
        self.configuration.athina_test_tmp_dir = "/tmp/athina-test%s" % time_field

        # Copy student repo to tmp directory for testing (omit hidden files for security purposes, e.g., .git)
        self.rm_dir(self.configuration.athina_student_code_dir)
        if self.configuration.no_repo is False:
            self.copy_dir('%s/repodata%s/u%s' % (self.configuration.config_dir, self.configuration.assignment_id,
                                                 user_object.user_id),
                          '%s' % self.configuration.athina_student_code_dir)
        else:
            # Docker tends to create mount points which result in root folders that then cannot be deleted
            # The code below solves this problem.
            os.mkdir('%s' % self.configuration.athina_student_code_dir, mode=0o777)

        # Copy tests in tmp folder
        self.rm_dir(self.configuration.athina_test_tmp_dir)
        self.copy_dir('%s/tests' % self.configuration.config_dir, '%s' % self.configuration.athina_test_tmp_dir)

        if self.configuration.pass_extra_params is True:
            self.configuration.extra_params = [user_object.secondary_id,
                                               self.configuration.due_date.astimezone(timezone.utc).isoformat()]
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
            out, err = self.execute_with_firejail(self.configuration.athina_student_code_dir,
                                                  self.configuration.athina_test_tmp_dir,
                                                  self.configuration.extra_params,
                                                  test_script)

        # Clear temp directories
        self.rm_dir(self.configuration.athina_test_tmp_dir)
        self.rm_dir(self.configuration.athina_student_code_dir)

        # If we cannot find a number returned at the end of this list
        try:
            score = float(out.decode("utf-8", "backslashreplace").split("\n")[-2])
        except (IndexError, UnicodeDecodeError, ValueError):  # generated when errors exist in the test script
            score = None

        out = self.trim_test_output(out)
        test_reports.append(out)
        if self.repository.check_error(err) and score is None:
            test_grades.append(0.0)
            test_reports.append("Tests failed:\n {0}".format(err.decode("utf-8", "backslashreplace")).
                                encode("utf-8"))
        elif score is None:
            test_grades.append(0.0)
            test_reports.append(
                """Tests failed:
                Test likely timed out.
                This can happen if you have an infinite loop or non terminating loop,
                A missing library that the system doesn't have (contact the instructor)
                Non legible script grade due to some other unforeseen circumstance.""".encode("utf-8"))
        else:
            # scaling between 0 and 1
            test_grades.append(score / 100 * self.configuration.test_weights[x])

    def execute_with_firejail(self, athina_student_code_dir, athina_test_tmp_dir, extra_params, test_script):
        # Custom firejail profile that allows to sandbox suid processes (so that athina wont run as root)
        generate_firejail_profile("%s/server.profile" % athina_test_tmp_dir)

        # Run the test
        test_timeout = ["timeout", "--kill-after=1", str(self.configuration.test_timeout)]
        test_command = test_timeout + ["firejail", "--quiet", "--private", "--profile=server.profile",
                                       "--whitelist=%s/" % athina_student_code_dir,
                                       "--whitelist=%s/" % athina_test_tmp_dir] + test_script.split(
            " ") + extra_params
        self.logger.logger.debug(" ".join(test_command))
        process = subprocess.Popen(test_command,
                                   cwd="%s/" % athina_test_tmp_dir,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
        return out, err

    def plagiarism_checks_on_users(self):
        # Report plagiarism to any newly submitted grades (currently uses only MOSS)
        results = []
        users_graded = [user_object.user_id for user_object in Users.select()
                        if user_object.plagiarism_to_grade is True and
                        user_object.last_plagiarism_check + timedelta(hours=23) <=
                        datetime.now(timezone.utc).replace(tzinfo=None)]
        self.logger.logger.info("Checking for plagiarism...")
        self.logger.logger.debug(users_graded)

        # Check if the user requested a plagiarism check (in the cfg if the settings exist)
        if len(users_graded) != 0 and self.configuration.moss_id != 1:
            plagiarism = Plagiarism(service_type="moss",
                                    moss_id=self.configuration.moss_id,
                                    moss_lang=self.configuration.moss_lang)
            directory_list = []
            for value in Users.select():
                base_dir = "%s/repodata%s/u%s/" % (self.configuration.config_dir, self.configuration.assignment_id,
                                                   value.user_id)
                if os.path.isdir(base_dir) and glob.glob("%s%s" % (base_dir, self.configuration.moss_pattern)):
                    directory_list.append("%s%s" % (base_dir, self.configuration.moss_pattern))

            # Execute plagiarism check for the directories
            comparison_data = plagiarism.check_plagiarism(directory_list)

            values = []
            [[values.append(v) for v in val] for key, val in comparison_data.items()]
            # values does not include users that were found to not have any similar code
            # we add these to get the proper mean similarity scores
            for i in range(0, len(Users.select()) - len(values)):
                values.append(0)
            if len(values) != 0:  # this if is probably useless but kept here just in case
                mean_similarity = np.mean(np.array(values).astype(np.float))
            else:
                mean_similarity = 0

            for user_id in users_graded:
                try:
                    user_max_value = [np.max(np.array(val)) for key, val in
                                      comparison_data.items() if key == int(user_id)][0]
                except (RuntimeWarning, IndexError):
                    user_max_value = 0

                if not self.configuration.simulate and self.configuration.moss_publish:
                    self.e_learning.submit_comment(user_id,
                                                   """Your highest similarity score with another student: %s
                                                   The mean similarity score is: %s""" %
                                                   (user_max_value, mean_similarity))
                results.append([user_id, user_max_value, mean_similarity])
                self.logger.logger.info("> Submitted similarity results for %s: %s/%s" % (
                    user_id, user_max_value, mean_similarity))
                obj = Users.get(Users.user_id == user_id)
                obj.last_plagiarism_check = datetime.now(timezone.utc).replace(tzinfo=None)
                obj.moss_max = user_max_value
                obj.moss_average = mean_similarity
                obj.save()

            for user_object in Users.select():
                user_object.plagiarism_to_grade = False
                user_object.save()

        return results

    def start_testing_db(self):
        self.logger.logger.info("Pre-fetching all user repositories")
        # Pre-fetching is important for group assignments where grade is submitted to multiple members and all
        # user dbs need to be updated later on
        reverse_repository_index = dict()
        for user in Users.select():
            if self.configuration.no_repo is not True:
                if user.repository_url is not None:
                    self.repository.check_repository_changes(user.user_id)
                    # Create a reverse dictionary and obtain one name from a group (in case of group assignments)
                    # Process group assignment will test once and then it identifies and submits a grade for both
                    # groups
                    reverse_repository_index[user.repository_url] = user.user_id
            else:
                # When no repo is involved it is 1 to 1 testing (individual assignment)
                reverse_repository_index[user.user_id] = user.user_id
        processing_list = [[user.user_id, user] for user in Users.select() if
                           user.user_id in reverse_repository_index.values()]
        del reverse_repository_index

        # If we utilize docker we need to pre-build the docker container
        if self.configuration.use_docker:
            docker_build(configuration=self.configuration, logger=self.logger)

        # Whether we should run processes in parallel or not
        user_object_results = []
        if self.configuration.processes < 2:
            for key, value in processing_list:
                user_objects = self.process_student_assignment(key)  # because what is returned is a list
                user_object_results.append(user_objects)
        else:
            user_ids = [key for key, value in processing_list]
            user_object_results = self.parallel_map(user_ids)

        return user_object_results  # This is not necessary but for sake of testing is left here

    def parallel_map(self, user_ids):
        # For parallel runs database objects have to be dropped (they cannot be pickled)
        self.configuration.db_filename = self.user_data.db_filename
        del self.user_data
        self.logger.delete_logger()

        # FIXME: Alternatively process could become a staticmethod but a lot of parameters have to be passed to it then.
        compute_pool = multiprocessing.Pool(processes=self.configuration.processes)
        user_object_results = compute_pool.map(self.process_student_assignment, user_ids)

        # Restoring the objects
        self.logger.create_logger()
        self.user_data = Database(self.configuration.db_filename)

        return user_object_results
