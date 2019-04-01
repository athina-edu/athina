# Hyper object that retains all execution parameters shared between modules
from datetime import datetime, timezone
import os
import glob
import configparser
import json
import shutil
import logging
import logging.handlers


class Configuration:
    logger = None
    config_dir = "/tmp/athina_empty"
    config_filename = "test_assignment"
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
    git_username = ""
    git_password = ""
    same_url_limit = 1
    submit_results_as_file = True
    max_file_size = 1024
    test_timeout = 120
    no_repo = False
    pass_extra_params = False
    grade_update_frequency = 23
    git_url = 'github.com'
    processes = 1
    due_date = datetime(2100, 1, 1, 0, 0)
    use_docker = False
    canvas_url = "www.instructure.com"
    send_grade_to_canvas = True

    # Set on the fly
    db_filename = ""
    athina_student_code_dir = ""
    athina_test_tmp_dir = ""
    extra_params = ""

    def __init__(self, logger):
        self.logger = logger
        self.default_dir()

    @staticmethod
    def find_cfg(directory):
        if os.path.isdir(directory):
            # Find a cfg file in the directory
            try:
                cfg_file = glob.glob('%s*.cfg' % directory)[0]
            except IndexError:
                cfg_file = directory  # this will fail later on but we have done all that we can
        else:
            cfg_file = directory
        return cfg_file

    @staticmethod
    def default_dir():
        # mainly used for testing
        os.makedirs("/tmp/athina_empty", exist_ok=True)
        os.chmod("/tmp/athina_empty", 0o777)

    @staticmethod
    def in_docker():
        """ Returns: True if running in a Docker container, else False """
        with open('/proc/1/cgroup', 'rt') as ifh:
            return 'docker' in ifh.read()

    @staticmethod
    def check_dependencies(packages: list):
        # Verify requirements are available on OS
        for software in packages:
            if shutil.which(software) is None:
                raise FileNotFoundError("%s is not available on the host system." % software)
        return True

    def load_configuration(self, directory):
        # Load Configuration file
        config = configparser.ConfigParser()
        config.read(self.find_cfg(directory))

        # Read Configuration file
        self.config_dir = os.path.dirname(directory)
        self.config_filename = os.path.split(self.find_cfg(directory))[1]  # cfg filename or dir name

        # Set new log file
        self.logger.set_assignment_log_file("%s/%s.log" % (self.config_dir, self.config_filename))

        # Load arguments from config
        if config.getboolean('main', 'print_debug_msgs', fallback=False):
            self.logger.set_debug(True)
        self.logger.logger.info("Reading %s in %s" % (self.config_filename, self.config_dir))

        self.auth_token = config.get('main', 'auth_token', fallback=False)
        self.course_id = config.getint('main', 'course_id', fallback=1)
        self.assignment_id = config.getint('main', 'assignment_id', fallback=1)

        self.total_points = config.getint('main', 'total_points', fallback=100)
        self.enforce_due_date = config.getboolean('main', 'enforce_due_date', fallback=True)
        self.test_scripts = json.loads(config.get('main', 'test_scripts', fallback={}))
        self.test_weights = json.loads(config.get('main', 'test_weights', fallback={}))
        self.moss_id = config.getint('main', 'moss_id', fallback=1)
        self.moss_lang = config.get('main', 'moss_lang', fallback="C")
        self.moss_pattern = config.get('main', 'moss_pattern', fallback=".c")
        self.git_username = config.get('main', 'git_username', fallback="test")
        self.git_password = config.get('main', 'git_password', fallback="test")
        self.same_url_limit = config.getint('main', 'same_url_limit', fallback=1)
        self.check_plagiarism_hour = config.getint('main', 'check_plagiarism_hour', fallback=1)
        self.submit_results_as_file = config.getboolean('main', 'submit_results_as_file', fallback=True)
        self.max_file_size = config.getint('main', 'max_file_size', fallback=1)
        self.max_file_size = self.max_file_size * 1024  # Convert KB to bytes
        self.test_timeout = config.getint('main', 'test_timeout', fallback=120)
        self.no_repo = config.getboolean('main', 'no_repo', fallback=False)
        self.pass_extra_params = config.getboolean('main', 'pass_extra_params', fallback=False)
        self.grade_update_frequency = config.getint('main', 'grade_update_frequency', fallback=24-1) - 1
        self.git_url = config.get('main', 'git_url', fallback='www.github.com')
        self.processes = config.getint('main', 'processes', fallback=1)
        self.canvas_url = config.get('main', 'canvas_url', fallback="www.instructure.com")
        self.send_grade_to_canvas = config.getboolean('main', 'send_grade_to_canvas', fallback=True)
        self.use_docker = config.getboolean('main', 'use_docker', fallback=False)

        # If no repo then definitely pass extra params
        if self.no_repo:
            self.pass_extra_params = True

        # If running from within a container then firejail is meaningless
        if self.in_docker():
            self.use_docker = True

        # Verify software dependencies
        packages = ["timeout", "git"]
        if self.use_docker is True:
            packages.append("docker")
        else:
            packages.append("firejail")
        self.check_dependencies(packages)
