# athina_cli_tests.py

import unittest
from athina.users import *
from athina.git import *
from athina.configuration import *
from athina.logger import *
from athina.tester import *
from athina.canvas import *
import os
import shutil
import multiprocessing


class TestFunctions(unittest.TestCase):
    def test_create_user_object(self):
        filename = "tests/user_data.pkl"
        if os.path.isfile(filename):
            os.remove(filename)
        user_data = self.create_fake_user_db()  # This otherwise creates a new object
        user_data.save(filename)
        user_data2 = Users()
        user_data2 = user_data2.load(filename)
        self.assertEqual(type(user_data), type(user_data2))

    @staticmethod
    def create_fake_user_db():
        """
        This method includes several static user scenarios and should not be changed since multiple tests may depend
        on it. If you want to generate a new scenario just add a new user into the database that this method returns.

        :return: user_data object from users.py
        """
        filename = "tests/user_data.pkl"
        if os.path.isfile(filename):
            os.remove(filename)
        user_data = Users()

        # Normal student
        user_data.db[1] = user_data.User(user_id=1)
        user_data.db[1].repository_url = "https://github.com/athina-edu/testing.git"
        user_data.db[1].url_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_data.db[1].new_url = True
        user_data.db[1].commit_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)

        # Student with wrong url
        user_data.db[2] = user_data.User(user_id=2)
        user_data.db[2].repository_url = "https://github.com/athina-edu/testin"
        user_data.db[2].url_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_data.db[2].new_url = True
        user_data.db[2].commit_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)

        # Students 3 and 4 with same url (note this is different from user 1 by a backslash)
        user_data.db[3] = user_data.User(user_id=3)
        user_data.db[3].repository_url = "https://github.com/athina-edu/testing.git/"
        user_data.db[3].url_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_data.db[3].new_url = True
        user_data.db[3].commit_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_data.db[4] = user_data.User(user_id=4)
        user_data.db[4].repository_url = "https://github.com/athina-edu/testing.git/"
        user_data.db[4].url_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_data.db[4].new_url = True
        user_data.db[4].commit_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)

        # No URL user
        user_data.db[5] = user_data.User(user_id=5)

        # Student submitting after the due date (default is set 2100 in configuration module)
        user_data.db[6] = user_data.User(user_id=6)
        user_data.db[6].repository_url = "https://github.com/git-persistence/git-persistence"
        user_data.db[6].url_date = datetime(2101, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_data.db[6].new_url = True
        user_data.db[6].commit_date = datetime(2101, 1, 1, 0, 0).replace(tzinfo=timezone.utc)

        # No repo user
        user_data.db[7] = user_data.User(user_id=7)

        return user_data

    def test_git_tester(self):
        results = []
        configuration = Configuration()
        # Create fake directories
        shutil.rmtree("/tmp/tests", ignore_errors=True)
        os.makedirs("/tmp/tests", exist_ok=True)
        f = open("/tmp/tests/test", 'a')
        f.write("#!/bin/bash\necho 80\n")
        f.close()

        logger = Logger()
        logger.verbose = True
        e_learning = Canvas(configuration.auth_token,
                            configuration.course_id,
                            configuration.assignment_id,
                            logger,
                            configuration.submit_results_as_file)
        user_data = self.create_fake_user_db()
        repository = Repository(user_data, logger, configuration, e_learning)
        results.append(repository.check_repository_changes(1))
        results.append(repository.check_repository_changes(2))
        results.append(repository.check_repository_changes(3))
        results.append(repository.check_repository_changes(4))
        results.append(repository.check_repository_changes(5))
        results.append(repository.check_repository_changes(6))
        self.assertEqual(results, [True, False, True, True, False, False])

        tester = Tester(user_data, logger, configuration, e_learning, repository)

        # First time assignment evaluation
        user_object = tester.process_student_assignment(1)
        self.assertEqual(user_object[0].new_url, False)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[0].last_grade, 80)
        last_graded = user_object[0].last_graded

        # Second time of athina iterating through but no new commit (grading should not change)
        user_object = tester.process_student_assignment(1)
        self.assertEqual(last_graded, user_object[0].last_graded)

        # New repo check and then attempting to test again, no change
        repository.check_repository_changes(1)
        user_object = tester.process_student_assignment(1)
        self.assertEqual(last_graded, user_object[0].last_graded)

        # Second student incorrect url, run double to verify that behavior won't change due to variable flip
        user_object = tester.process_student_assignment(2)
        self.assertEqual(user_object[0].plagiarism_to_grade, False)
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        user_object = tester.process_student_assignment(2)
        self.assertEqual(user_object[0].plagiarism_to_grade, False)
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))

        # Third student submitted the same url like the 4th. Test the case that this is not allowed.
        user_data.check_duplicate_url(same_url_limit=1)
        user_object = tester.process_student_assignment(3)
        self.assertEqual(user_object[0].plagiarism_to_grade, False)
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))

        # Third and fourth student but now groups of 2 are allowed
        user_data.check_duplicate_url(same_url_limit=2)
        repository.check_repository_changes(3)
        user_object = tester.process_student_assignment(3)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[0].last_grade, 80)
        self.assertGreater(user_object[1].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[1].last_grade, 80)

        # User with no url, no grading
        user_object = tester.process_student_assignment(5)
        print(user_object)
        self.assertEqual(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))

        # Previous user corrects and submits and actual good url
        user_data.db[5].repository_url = "https://github.com/git-persistence/git-persistence/"
        user_data.db[5].url_date = datetime.now(timezone.utc)
        user_data.db[5].new_url = True
        repository.check_repository_changes(5)
        user_object = tester.process_student_assignment(5)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[0].last_grade, 80)
        last_graded = user_object[0].last_graded

        # User 5 submit commit after the due date
        configuration.due_date = user_data.db[5].commit_date - timedelta(hours=24)
        user_data.db[5].changed_state = True
        user_object = tester.process_student_assignment(5)
        self.assertEqual(user_object[0].last_graded, last_graded)

        # Briefly remove due date enforcement
        # Technically enforce_due_date only moves the due date to some extreme future date
        configuration.due_date = datetime.now(timezone.utc)
        user_data.db[5].changed_state = True
        user_object = tester.process_student_assignment(5)
        self.assertGreater(user_object[0].last_graded, last_graded)

        # User 6 submits late
        configuration.due_date = datetime(2100, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        user_object = tester.process_student_assignment(6)
        self.assertEqual(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))

        # NO REPO operations, first run of script
        configuration.no_repo = True
        configuration.grade_update_frequency = 24
        user_object = tester.process_student_assignment(7)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[0].last_grade, 80)
        last_graded = user_object[0].last_graded

        # Time doesn't go by, checking to see if script can run again, it shouldn't
        user_object = tester.process_student_assignment(7)
        self.assertEqual(user_object[0].last_graded, last_graded)

        # Enough time goes buy
        user_data.db[7].last_graded = user_data.db[7].last_graded - timedelta(hours=24)
        user_object = tester.process_student_assignment(7)
        self.assertGreater(user_object[0].last_graded, last_graded)

    @unittest.skip("Moss service hangs for too long. Implement timeouts in moss.py")
    def test_tester_plagiarism(self):
        configuration = Configuration()
        # Create fake directories
        shutil.rmtree("/tmp/tests", ignore_errors=True)
        os.makedirs("/tmp/tests", exist_ok=True)
        f = open("/tmp/tests/test", 'a')
        f.write("#!/bin/bash\necho 80\n")
        f.close()

        logger = Logger()
        logger.verbose = True
        e_learning = Canvas(configuration.auth_token,
                            configuration.course_id,
                            configuration.assignment_id,
                            logger,
                            configuration.submit_results_as_file)
        user_data = self.create_fake_user_db()
        repository = Repository(user_data, logger, configuration, e_learning)
        repository.check_repository_changes(1)
        repository.check_repository_changes(2)
        repository.check_repository_changes(3)
        repository.check_repository_changes(4)
        repository.check_repository_changes(5)
        repository.check_repository_changes(6)

        tester = Tester(user_data, logger, configuration, e_learning, repository)

        # First time assignment evaluation
        tester.process_student_assignment(1)
        tester.process_student_assignment(2)
        tester.process_student_assignment(3)
        tester.process_student_assignment(4)
        tester.process_student_assignment(5)
        tester.process_student_assignment(6)

        user_data.db[1].last_plagiarism_check = user_data.db[1].last_plagiarism_check - timedelta(hours=48)
        user_data.db[2].last_plagiarism_check = user_data.db[2].last_plagiarism_check - timedelta(hours=48)
        user_data.db[3].last_plagiarism_check = user_data.db[3].last_plagiarism_check - timedelta(hours=48)
        user_data.db[4].last_plagiarism_check = user_data.db[4].last_plagiarism_check - timedelta(hours=48)
        user_data.db[5].last_plagiarism_check = user_data.db[5].last_plagiarism_check - timedelta(hours=48)
        user_data.db[6].last_plagiarism_check = user_data.db[6].last_plagiarism_check - timedelta(hours=48)

        results = tester.plagiarism_checks_on_users()
        self.assertEqual(len(results), 3)

    def test_tester_docker(self):
        results = []
        configuration = Configuration()

        configuration.use_docker = True
        # Create fake directories
        shutil.rmtree("/tmp/tests", ignore_errors=True)
        os.makedirs("/tmp/tests", exist_ok=True)
        f = open("/tmp/tests/test", 'w')
        f.write("#!/bin/bash\necho 80\n")
        f.close()
        f = open("/tmp/Dockerfile", 'w')
        f.write("FROM ubuntu:18.04\nENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR")
        f.close()

        logger = Logger()
        logger.verbose = True
        logger.print_debug_messages = True
        e_learning = Canvas(configuration.auth_token,
                            configuration.course_id,
                            configuration.assignment_id,
                            logger,
                            configuration.submit_results_as_file)
        user_data = self.create_fake_user_db()
        repository = Repository(user_data, logger, configuration, e_learning)
        results.append(repository.check_repository_changes(1))
        results.append(repository.check_repository_changes(2))
        results.append(repository.check_repository_changes(3))
        results.append(repository.check_repository_changes(4))
        results.append(repository.check_repository_changes(5))
        results.append(repository.check_repository_changes(6))
        self.assertEqual(results, [True, False, True, True, False, False])

        tester = Tester(user_data, logger, configuration, e_learning, repository)

        # First time assignment evaluation
        user_object = tester.process_student_assignment(1)
        self.assertEqual(user_object[0].new_url, False)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object[0].last_grade, 80)

        # Parallel process
        configuration.processes = 2
        compute_pool = multiprocessing.Pool(processes=configuration.processes)
        user_object_results = compute_pool.map(tester.process_student_assignment, [1, 2, 3, 4, 5])
        self.assertEqual(user_object_results[0][0].new_url, False)
        self.assertGreater(user_object_results[0][0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object_results[0][0].last_grade, 80)
        self.assertEqual(user_object_results[1][0].plagiarism_to_grade, False)
        self.assertEqual(user_object_results[1][0].commit_date, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        # Group assignment
        self.assertGreater(user_object_results[2][0].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object_results[2][0].last_grade, 80)
        self.assertGreater(user_object_results[2][1].last_graded, datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc))
        self.assertEqual(user_object_results[2][1].last_grade, 80)


if __name__ == '__main__':
    unittest.main()
