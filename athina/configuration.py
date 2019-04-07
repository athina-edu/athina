# Hyper object that retains all execution parameters shared between modules
from datetime import datetime
import os
import glob
import shutil
import yaml


class Configuration:
    logger = None
    config_dir = "/tmp/athina_empty"
    config_filename = "test_assignment"
    simulate = True
    auth_token = ""
    course_id = 1
    assignment_id = 1
    total_points = 100
    enforce_due_date = True
    test_scripts = ["bash test", "bash test"]  # this is defined as such for testing only
    test_weights = [0.8, 0.2]
    moss_id = 1
    moss_lang = "C"
    moss_pattern = "*.c"
    moss_publish = False
    check_plagiarism_hour = 1
    git_username = "test"
    git_password = "test"
    same_url_limit = 1
    submit_results_as_file = True
    max_file_size = 1024
    test_timeout = 90
    no_repo = False
    pass_extra_params = False
    grade_update_frequency = 24
    git_url = 'github.com'
    processes = 1
    due_date = datetime(2100, 1, 1, 0, 0)
    use_docker = False
    canvas_url = "www.instructure.com"
    grade_publish = True
    print_debug_msgs = False

    # Set on the fly
    db_filename = ""
    athina_student_code_dir = ""
    athina_test_tmp_dir = ""
    extra_params = ""

    def __init__(self, logger):
        self.logger = logger
        self.default_dir()

    @staticmethod
    def find_yaml(directory):
        if os.path.isdir(directory):
            # Find a cfg file in the directory
            try:
                cfg_file = glob.glob('%s*.yaml' % directory)[0]
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

    # This is not a static function since it accesses class items passed as parameters: configvar
    def load_value(self, config, key, configvar):
        value = config.get(key, None)
        if value is not None:
            setattr(self, key, value)
        else:
            pass  # The default value as set in this configuration.py file remains

    def load_configuration(self, directory):
        # Load Configuration file
        try:
            with open(self.find_yaml(directory), 'r') as stream:
                config = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            self.logger.logger.error(exc)
            raise yaml.YAMLError(exc)

        # Read Configuration file
        self.config_dir = os.path.dirname(directory)
        self.config_filename = os.path.split(self.find_yaml(directory))[1]  # cfg filename or dir name

        # Set new log file
        self.logger.set_assignment_log_file("%s/%s.log" % (self.config_dir, self.config_filename))

        # Load arguments from config
        self.load_value(config, 'print_debug_msgs', self.print_debug_msgs)
        if self.print_debug_msgs:
            self.logger.set_debug(True)
        self.logger.logger.info("Reading %s in %s" % (self.config_filename, self.config_dir))

        self.load_value(config, 'auth_token', self.auth_token)
        self.load_value(config, 'course_id', self.course_id)
        self.load_value(config, 'assignment_id', self.assignment_id)

        self.load_value(config, 'total_points', self.total_points)
        self.load_value(config, 'enforce_due_date', self.enforce_due_date)
        self.load_value(config, 'test_scripts', self.test_scripts)
        self.load_value(config, 'test_weights', self.test_weights)

        self.load_value(config, 'moss_id', self.moss_id)
        self.load_value(config, 'moss_lang', self.moss_lang)
        self.load_value(config, 'moss_pattern', self.moss_pattern)
        self.load_value(config, 'moss_publish', self.moss_publish)

        self.load_value(config, 'git_username', self.git_username)
        self.load_value(config, 'git_password', self.git_password)
        self.load_value(config, 'same_url_limit', self.same_url_limit)
        self.load_value(config, 'check_plagiarism_hour', self.check_plagiarism_hour)
        self.load_value(config, 'submit_results_as_file', self.submit_results_as_file)
        self.load_value(config, 'max_file_size', self.max_file_size)
        self.max_file_size = self.max_file_size * 1024  # Convert KB to bytes
        self.load_value(config, 'test_timeout', self.test_timeout)

        self.load_value(config, 'no_repo', self.no_repo)
        self.load_value(config, 'pass_extra_params', self.pass_extra_params)
        self.load_value(config, 'grade_update_frequency', self.grade_update_frequency)
        self.grade_update_frequency -= 1

        self.load_value(config, 'git_url', self.git_url)
        self.load_value(config, 'canvas_url', self.canvas_url)
        self.load_value(config, 'processes', self.processes)
        self.load_value(config, 'grade_publish', self.grade_publish)
        self.load_value(config, 'use_docker', self.use_docker)

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
