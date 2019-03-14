# Hyper object that retains all execution parameters shared between modules
from datetime import datetime, timezone
import os
import glob
import configparser
import json
import shutil


class Configuration:
    logger = None
    config_dir = "/tmp"
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

    # Set on the fly
    db_filename = ""

    def __init__(self, logger):
        self.logger = logger

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

        # Load arguments from config
        try:
            self.logger.print_debug_messages = config.getboolean('main', 'print_debug_msgs')
        except configparser.NoOptionError:
            self.logger.print_debug_messages = False
        self.logger.vprint("Loading configuration", debug=True)
        self.logger.vprint("Reading %s in %s" % (self.config_filename, self.config_dir), debug=True)

        try:
            self.auth_token = config.get('main', 'auth_token')
        except configparser.NoSectionError:
            self.logger.vprint("Unable to read config file!")
            return False
        try:
            self.course_id = config.getint('main', 'course_id')
        except ValueError:
            self.course_id = 0
        try:
            self.assignment_id = config.getint('main', 'assignment_id')
        except ValueError:
            self.assignment_id = 0

        self.total_points = config.getint('main', 'total_points')
        self.enforce_due_date = config.getboolean('main', 'enforce_due_date')
        self.test_scripts = json.loads(config.get('main', 'test_scripts'))
        self.test_weights = json.loads(config.get('main', 'test_weights'))
        try:
            self.moss_id = config.getint('main', 'moss_id')
        except ValueError:
            self.moss_id = 0
        self.moss_lang = config.get('main', 'moss_lang')
        self.moss_pattern = config.get('main', 'moss_pattern')
        self.git_username = config.get('main', 'git_username')
        self.git_password = config.get('main', 'git_password')
        self.same_url_limit = config.getint('main', 'same_url_limit')
        self.check_plagiarism_hour = config.getint('main', 'check_plagiarism_hour')
        self.submit_results_as_file = config.getboolean('main', 'submit_results_as_file')
        self.max_file_size = config.getint('main', 'max_file_size')
        self.max_file_size = self.max_file_size * 1024  # Convert KB to bytes
        try:
            self.test_timeout = config.getint('main', 'test_timeout')
        except configparser.NoOptionError:
            self.test_timeout = 120
        try:
            self.no_repo = config.getboolean('main', 'no_repo')
        except configparser.NoOptionError:
            self.no_repo = False
        try:
            self.pass_extra_params = config.getboolean('main', 'pass_extra_params')
        except configparser.NoOptionError:
            self.pass_extra_params = False
        try:
            self.grade_update_frequency = config.getint('main', 'grade_update_frequency') - 1
        except configparser.NoOptionError:
            self.grade_update_frequency = 24 - 1
        try:
            self.git_url = config.get('main', 'git_url')
        except configparser.NoOptionError:
            self.git_url = 'gitlab.cs.wwu.edu'
        try:
            self.processes = config.getint('main', 'processes')
        except configparser.NoOptionError:
            self.processes = 1
        try:
            self.canvas_url = config.get('main', 'canvas_url')
        except configparser.NoOptionError:
            self.canvas_url = "www.instructure.com"

        try:
            self.use_docker = config.getboolean('main', 'use_docker')
        except configparser.NoOptionError:
            self.use_docker = False

        # Verify software dependencies
        packages = ["timeout", "git"]
        if self.use_docker is True:
            packages.append("docker")
        else:
            packages.append("firejail")
        self.check_dependencies(packages)
