from athina.firejail import *
from datetime import timedelta, datetime, timezone
import subprocess
import glob
import time
import shutil
import np
import hashlib
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

    def check_error(self, err):
        if err != b'':
            self.logger.vprint("Testing script returned error: %s" % err.decode("utf-8", "backslashreplace"))
            return True

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

    def process_student_assignment(self, user_id, forced_testing=False):
        # In the event of a parallel run, we need to reacquire a connection to the db
        if self.user_data is None:
            self.user_data = Database(self.configuration.db_filename)
        user_object = Users.get(Users.user_id == user_id)

        self.logger.vprint("> Checking %s - %d" % (user_object.user_fullname, user_id), debug=True)

        # TODO: Need to split the code below since repo testing has additional steps when just testing doesn't

        # Code has changed and due date for submissions does not exceed submitted dates
        self.logger.vprint("Commit Due Date Comparison: %s < %s ?" % (
            user_object.commit_date.strftime("%Y-%m-%d %H:%M:%S"),
            self.configuration.due_date.strftime("%Y-%m-%d %H:%M:%S")), debug=True)

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
            self.logger.vprint(">>> Testing")

            # Run tests
            test_grades = []
            test_reports = []

            for x in range(0, len(self.configuration.test_scripts)):
                test_reports.append(("Test %d with weight %.2f" %
                                     (x + 1, self.configuration.test_weights[x])).encode("utf-8"))
                test_script = self.configuration.test_scripts[x]

                if self.configuration.processes < 2:  # If we are not running things in parallel, default will do
                    # (ensures backwards compatibility) with non parallelized tests
                    athina_student_code_dir = "/tmp/athina"
                    athina_test_tmp_dir = "/tmp/athina-test"
                else:
                    athina_student_code_dir = "/tmp/athina%s" % time.time()
                    athina_test_tmp_dir = "/tmp/athina-test%s" % time.time()

                # Copy student repo to tmp directory for testing (omit hidden files for security purposes, e.g., .git)
                try:
                    shutil.rmtree(athina_student_code_dir)
                except PermissionError:
                    self.logger.vprint("Cannot delete %s. Likely permissions error." % athina_student_code_dir)
                    raise PermissionError(athina_student_code_dir)
                except FileNotFoundError:
                    pass
                if self.configuration.no_repo is False:
                    try:
                        shutil.copytree('%s/repodata%s/u%s' % (self.configuration.config_dir,
                                                               self.configuration.assignment_id,
                                                               user_id),
                                        '%s' % athina_student_code_dir)
                    except FileNotFoundError:
                        self.logger.vprint("Could not copy student directory at %s/repodata%s/u%s/*" %
                                           (self.configuration.config_dir, self.configuration.assignment_id, user_id))
                # Copy tests in tmp folder
                try:
                    shutil.rmtree(athina_test_tmp_dir)
                except PermissionError:
                    self.logger.vprint("Cannot delete %s. Likely permissions error." % athina_test_tmp_dir)
                    raise PermissionError(athina_test_tmp_dir)
                except FileNotFoundError:
                    pass
                try:
                    shutil.copytree('%s/tests' % self.configuration.config_dir, '%s' % athina_test_tmp_dir)
                except FileNotFoundError:
                    self.logger.vprint("Could not copy test directory at %s/tests" % self.configuration.config_dir)

                if self.configuration.pass_extra_params is True:
                    extra_params = [user_object.secondary_id, self.configuration.due_date.isoformat()]
                else:
                    extra_params = [athina_student_code_dir, athina_test_tmp_dir]

                # Execute using docker or firejail (depending on what the settings are)
                if self.configuration.use_docker is True \
                        and os.path.isfile("%s/%s" % (self.configuration.config_dir, "Dockerfile")):
                    out, err = self.execute_with_docker(athina_student_code_dir, athina_test_tmp_dir,
                                                        extra_params, test_script)
                else:
                    out, err = self.execute_with_firejail(athina_student_code_dir, athina_test_tmp_dir,
                                                          extra_params, test_script)

                # TODO: convert these to cmmands using shutils and os
                # Clear temp directories
                subprocess.run("rm -rf '%s'" % athina_test_tmp_dir, shell=True)
                subprocess.run("rm -rf '%s'" % athina_student_code_dir, shell=True)

                # If we cannot find a number returned at the end of this list
                try:
                    score = float(out.decode("utf-8", "backslashreplace").split("\n")[-2])
                except IndexError:
                    score = None
                except UnicodeDecodeError:
                    score = None
                except ValueError:  # generated when errors exist in the test script
                    score = None

                if self.check_error(err) and score is None:
                    test_grades.append(0.0)
                    out = self.trim_test_output(out)
                    test_reports.append(out)
                    test_reports.append("Tests failed:\n {0}".format(err.decode("utf-8", "backslashreplace")).
                                        encode("utf-8"))
                elif score is None:
                    test_grades.append(0.0)
                    out = self.trim_test_output(out)
                    test_reports.append(out)
                    test_reports.append(
                        """Tests failed:
                        Test likely timed out.
                        This can happen if you have an infinite loop or non terminating loop,
                        A missing library that the system doesn't have (contact the instructor)
                        Non legible script grade due to some other unforeseen circumstance.""".encode("utf-8"))
                else:
                    # scaling between 0 and 1
                    test_grades.append(score / 100 * self.configuration.test_weights[x])
                    # Check the size of the output and trim if larger than max allowed
                    out = self.trim_test_output(out)
                    test_reports.append(out)

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

            user_object_list = []  # Return object in case we update multiple user_objects (important for parallel)
            # Submitting grades for as many students that share the repository url (depending on how many are permitted)
            for current_user_id, current_user_object in user_list:
                # Submit grade
                if not self.configuration.simulate:
                    self.e_learning.submit_grade(user_id=current_user_id, user_values=current_user_object, grade=grade,
                                                 test_reports=test_reports)
                else:  # print instead
                    for text in test_reports:
                        self.logger.vprint(text.decode("utf-8", "backslashreplace"))
                self.logger.vprint(">>> Submitting new grade for %s: %s" % (current_user_id, grade))

                # Update the user db that grades have been submitted
                self.update_user_db(current_user_object)
                current_user_object.changed_state = False
                current_user_object.last_grade = grade
                if not self.configuration.simulate:
                    current_user_object.save()
                user_object_list.append(current_user_object)

            return user_object_list  # return the list of all updated objects
        else:
            self.logger.vprint(">> No changes or past due date", debug=True)
            user_object.changed_state = False

            if not self.configuration.simulate:
                user_object.save()
            return [user_object]  # return list of the current object

    def execute_with_firejail(self, athina_student_code_dir, athina_test_tmp_dir, extra_params, test_script):
        # Custom firejail profile that allows to sandbox suid processes (so that athina wont run as root)
        generate_firejail_profile("%s/server.profile" % athina_test_tmp_dir)

        # Run the test
        test_timeout = ["timeout", "--kill-after=1", str(self.configuration.test_timeout)]
        test_command = test_timeout + ["firejail", "--quiet", "--private", "--profile=server.profile",
                                       "--whitelist=%s/" % athina_student_code_dir,
                                       "--whitelist=%s/" % athina_test_tmp_dir] + test_script.split(
            " ") + extra_params
        self.logger.vprint(" ".join(test_command), debug=True)
        process = subprocess.Popen(test_command,
                                   cwd="%s/" % athina_test_tmp_dir,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
        return out, err

    def execute_with_docker(self, athina_student_code_dir, athina_test_tmp_dir, extra_params, test_script):
        hashed_name = hashlib.md5(self.configuration.config_filename.encode("ascii")).hexdigest()

        # Build with empty context
        os.makedirs("/tmp/athina_empty", exist_ok=True)

        build_statement = ["docker", "build", "/tmp/athina_empty", "-t",
                           "%s" % hashed_name,
                           "-f", "Dockerfile"]
        run_statement = ["docker", "run", "-e", "TEST=%s" % test_script,
                         "--stop-timeout", "%d" % self.configuration.test_timeout,
                         "-e", "STUDENT_DIR=%s" % athina_student_code_dir,
                         "-e", "TEST_DIR=%s" % athina_test_tmp_dir,
                         "-e", "EXTRA_PARAMS=%s" % extra_params,
                         "-v", "%s:%s" % (athina_student_code_dir, athina_student_code_dir),
                         "-v", "%s:%s" % (athina_test_tmp_dir, athina_test_tmp_dir),
                         "%s" % hashed_name]

        # Building the image. This is built once and then things are much faster but the check needs to happen
        self.logger.vprint(" ".join(build_statement), debug=True)
        process = subprocess.Popen(build_statement, cwd="%s/" % self.configuration.config_dir, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()

        # If build was successful, run the image
        if not self.check_error(err):
            self.logger.vprint(" ".join(run_statement), debug=True)
            process = subprocess.Popen(run_statement, cwd="%s/" % self.configuration.config_dir,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            run_out, run_err = process.communicate()
            out += run_out
            err += run_err

        return out, err

    def plagiarism_checks_on_users(self):
        """
        Report plagiarism to any newly submitted grades (currently uses only MOSS)
        """
        results = []
        users_graded = [user_id for user_id, user_object in self.user_data.db.items()
                        if user_object.plagiarism_to_grade is True and
                        user_object.last_plagiarism_check + timedelta(hours=23) <=
                        datetime.now(timezone.utc).replace(tzinfo=None)]
        self.logger.vprint("Checking for plagiarism...")
        self.logger.vprint(users_graded)

        # Debugging line
        # for user_id, user_object in USER_DATA.db.items():
        #     print([user_id, user_object.plagiarism_to_grade,
        #            user_object.last_plagiarism_check + datetime.timedelta(hours=23),
        #            datetime.datetime.now()])

        if len(users_graded) != 0:
            plagiarism = Plagiarism(service_type="moss",
                                    moss_id=self.configuration.moss_id,
                                    moss_lang=self.configuration.moss_lang)
            directory_list = []
            for key, value in self.user_data.db.items():
                if os.path.isdir("%s/repodata%s/u%s/" % (self.configuration.config_dir,
                                                         self.configuration.assignment_id,
                                                         key)):
                    if glob.glob("%s/repodata%s/u%s/%s" %
                                 (self.configuration.config_dir, self.configuration.assignment_id, key,
                                  self.configuration.moss_pattern)):
                        directory_list.append("%s/repodata%s/u%s/%s" %
                                              (self.configuration.config_dir, self.configuration.assignment_id, key,
                                               self.configuration.moss_pattern))
            comparison_data = plagiarism.check_plagiarism(directory_list)
            values = []
            [[values.append(v) for v in val] for key, val in comparison_data.items()]
            # values does not include users that were found to not have any similar code
            # we add these to get the proper mean similarity scores
            for i in range(0, len(self.user_data.db) - len(values)):
                values.append(0)
            if len(values) != 0:  # this if is probably useless but kept here just in case
                mean_similarity = np.mean(np.array(values).astype(np.float))
            else:
                mean_similarity = 0

            for user_id in users_graded:
                try:
                    user_max_value = [np.max(np.array(val)) for key, val in
                                      comparison_data.items() if key == int(user_id)][0]
                except RuntimeWarning:
                    user_max_value = 0
                except IndexError:
                    user_max_value = 0
                if not self.configuration.simulate:
                    self.e_learning.submit_comment(user_id,
                                                   """Your highest similarity score with another student: %s
                                                   The mean similarity score is: %s""" %
                                                   (user_max_value, mean_similarity))
                results.append([user_id, user_max_value, mean_similarity])
                self.logger.vprint("> Submitted similarity results for %s: %s/%s" % (
                    user_id, user_max_value, mean_similarity))
                self.user_data.db[user_id].last_plagiarism_check = datetime.now(timezone.utc).replace(tzinfo=None)

            for user_id, user_object in self.user_data.db.items():
                user_object.plagiarism_to_grade = False

        return results

    def start_testing_db(self):
        self.logger.vprint("Pre-fetching all user repositories")
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
                # When no repo is involved it is 1 to 1 check for assignments
                reverse_repository_index[user.user_id] = user.user_id
        processing_list = [[user.user_id, user] for user in Users.select() if
                           user.user_id in reverse_repository_index.values()]
        del reverse_repository_index

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
        compute_pool = multiprocessing.Pool(processes=self.configuration.processes)

        # For parallel runs database objects have to be dropped (they cannot be pickled)
        self.configuration.db_filename = self.user_data.db_filename
        del self.user_data

        # FIXME: Alternatively process could become a staticmethod but a lot of parameters have to be passed to it then.
        user_object_results = compute_pool.map(self.process_student_assignment, user_ids)

        # Restoring the objects
        self.user_data = Database(self.configuration.db_filename)

        return user_object_results
