# Hyper object that retains all execution parameters shared between modules
from datetime import datetime, timezone


class Configuration:
    logger = None
    config_dir = "/tmp"
    config_filename = None
    simulate = True
    auth_token = None
    course_id = 1
    assignment_id = 1
    total_points = 100
    enforce_due_date = True
    test_scripts = ["bash test", "bash test"]  # this is defined as such for testing only
    test_weights = [0.8, 0.2]
    moss_id = 20181579  # Registered by Michael Tsikerdekis - Michael.Tsikerdekis@wwu.edu, should be indivdly. changed
    moss_lang = "Python"
    moss_pattern = "*.py"
    check_plagiarism_hour = 1
    git_username = "test"
    git_password = "test"
    same_url_limit = 1
    submit_results_as_file = True
    max_file_size = 1024
    test_timeout = 120
    no_repo = False
    pass_extra_params = False
    grade_update_frequency = 23
    git_url = 'gitlab.cs.wwu.edu'
    processes = 1
    due_date = datetime(2100, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
