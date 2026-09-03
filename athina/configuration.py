# Hyper object that retains all execution parameters shared between modules
import glob
import multiprocessing
import os
import shutil
from datetime import datetime

import yaml

__all__ = ('Configuration',)


class Configuration:
    logger = None
    # Allow tests to override the temp directory to avoid permission issues on some hosts
    config_dir = os.environ.get('ATHINA_TEST_TMPDIR', f"/tmp/athina_empty_{os.getuid()}")
    config_filename = "test_assignment"
    auth_token = ""
    course_id = 1
    assignment_id = 1
    total_points = 100
    enforce_due_date = True
    test_scripts = ["bash test", "bash test"]  # this is defined as such for testing only
    test_weights = [0.8, 0.2]
    plagiarism_service = "copydetect"  # 'copydetect' (local, no server needed)
    plagiarism_pattern = "*.py"  # file glob pattern for plagiarism detection
    plagiarism_publish = False  # whether to publish similarity results to e-learning
    copydetect_threshold = 0.33  # similarity threshold for display (0.0-1.0)
    check_plagiarism_hour = 1
    git_username = "test"
    git_password = "test"
    group_assignment = False
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
    canvas_url = "www.instructure.com"
    grade_publish = True
    print_debug_msgs = False
    docker_use_seccomp = True
    docker_use_net_admin = False
    docker_no_internet = False
    use_webhook = False
    gitlab_check_repo_is_private = False

    # LLM feedback settings (OpenAI-compatible endpoint)
    llm_enabled = False
    llm_endpoint_url = ""
    llm_api_key = ""
    llm_model = "gpt-4o-mini"

    # Non-Canvas mode settings
    # input_method: 'canvas' (fetch from Canvas API) or 'db' (read from local database)
    input_method = "canvas"
    # output_method: 'canvas' (submit to Canvas) or 'gitlab_issues' (create GitLab issues)
    output_method = "canvas"
    # GitLab project where grade issues are created (numeric project ID)
    gitlab_project_id = 0
    # Whether grade issues are confidential (only visible to project members)
    gitlab_issues_confidential = True
    # Prefix prepended to issue titles, e.g., "Grade: John Doe"
    gitlab_issues_title_prefix = "Grade Report"

    # Set on the fly
    db_filename = ""
    athina_student_code_dir = ""
    athina_test_tmp_dir = ""
    extra_params = ""
    athina_web_url = None

    # global configs read through environment vars
    global_memory_limit = 80
    docker_memory_limit = "2g"

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
        # 0o700: owner rwx only. Set at creation time to avoid a separate chmod
        # call (which SonarCloud flags as S2612 / CWE-732). The sandboxed test
        # process runs as the same user, so owner-only access is sufficient.
        os.makedirs(Configuration.config_dir, exist_ok=True, mode=0o700)
        # Ensure old .git from previous tests is removed to allow copytree
        try:
            git_dir = f"{Configuration.config_dir}/.git"
            if os.path.isdir(git_dir):
                import shutil as _sh
                _sh.rmtree(git_dir)
        except Exception:
            pass

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

    def _load_assignment_env(self):
        """
        Load the assignment-specific .env file (written by athina-web) into the
        process environment.  This file carries LLM credentials (LLM_API_KEY,
        LLM_ENDPOINT_URL, LLM_MODEL) and git credentials.  Existing environment
        variables are NOT overridden so that explicit env vars (e.g. set by the
        operator or docker-compose) take precedence.
        """
        env_path = os.path.join(self.config_dir, '.env')
        if not os.path.isfile(env_path):
            return
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            # Best-effort; a missing/unreadable .env should not break grading.
            pass

    def load_configuration(self, directory):
        # Load Configuration file
        try:
            with open(self.find_yaml(directory), 'r') as stream:
                config = yaml.safe_load(stream)
        except (yaml.YAMLError, IsADirectoryError) as exc:
            self.logger.logger.error(exc)
            raise yaml.YAMLError(exc)

        # Global variables through environment
        # Global memory limit in percentage that forked processes must obey.
        self.global_memory_limit = int(os.environ.get('GLOBAL_MEMORY_LIMIT', 80))

        # Max memory that can be used by docker in docker notation, 1m, 2g etc.
        self.docker_memory_limit = os.environ.get('DOCKER_MEMORY_LIMIT', "2g")

        # Read Configuration file
        self.config_dir = os.path.dirname(directory)
        self.config_filename = os.path.split(self.find_yaml(directory))[1]  # cfg filename or dir name

        # Load assignment-specific .env (written by athina-web with LLM credentials,
        # git credentials, etc.) into the process environment so the LLM section below
        # can pick up LLM_API_KEY / LLM_ENDPOINT_URL / LLM_MODEL.
        self._load_assignment_env()

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

        self.load_value(config, 'plagiarism_service', self.plagiarism_service)
        self.load_value(config, 'plagiarism_pattern', self.plagiarism_pattern)
        self.load_value(config, 'plagiarism_publish', self.plagiarism_publish)
        self.load_value(config, 'copydetect_threshold', self.copydetect_threshold)

        self.load_value(config, 'git_username', self.git_username)
        self.load_value(config, 'git_password', self.git_password)
        self.load_value(config, 'group_assignment', self.group_assignment)
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
        # Git credentials can also come from environment (written by athina-web .env)
        self.git_url = os.environ.get('GIT_URL', self.git_url)
        self.git_username = os.environ.get('GIT_USERNAME', self.git_username)
        self.git_password = os.environ.get('GIT_PASSWORD', self.git_password)
        self.load_value(config, 'grade_publish', self.grade_publish)
        self.load_value(config, 'docker_use_seccomp', self.docker_use_seccomp)
        self.load_value(config, 'docker_use_net_admin', self.docker_use_net_admin)
        self.load_value(config, 'docker_no_internet', self.docker_no_internet)
        self.load_value(config, 'use_webhook', self.use_webhook)
        self.load_value(config, 'gitlab_check_repo_is_private', self.gitlab_check_repo_is_private)

        # LLM feedback — load from YAML config, then override from .env if present
        self.load_value(config, 'llm_enabled', self.llm_enabled)
        self.load_value(config, 'llm_endpoint_url', self.llm_endpoint_url)
        self.load_value(config, 'llm_model', self.llm_model)
        # API key and endpoint can also come from environment (written by athina-web .env)
        self.llm_endpoint_url = os.environ.get('LLM_ENDPOINT_URL', self.llm_endpoint_url)
        self.llm_api_key = os.environ.get('LLM_API_KEY', self.llm_api_key)
        self.llm_model = os.environ.get('LLM_MODEL', self.llm_model)
        if self.llm_api_key:
            self.llm_enabled = True

        # Non-Canvas mode settings — load from YAML, then override from .env env vars
        self.load_value(config, 'input_method', self.input_method)
        self.load_value(config, 'output_method', self.output_method)
        self.load_value(config, 'gitlab_project_id', self.gitlab_project_id)
        self.load_value(config, 'gitlab_issues_confidential', self.gitlab_issues_confidential)
        self.load_value(config, 'gitlab_issues_title_prefix', self.gitlab_issues_title_prefix)
        # Env var overrides (written by athina-web .env)
        self.output_method = os.environ.get('OUTPUT_METHOD', self.output_method)
        self.gitlab_project_id = int(os.environ.get('GITLAB_PROJECT_ID', self.gitlab_project_id))
        self.gitlab_issues_confidential = os.environ.get('GITLAB_ISSUES_CONFIDENTIAL',
                                                          str(self.gitlab_issues_confidential)).lower() == 'true'
        self.gitlab_issues_title_prefix = os.environ.get('GITLAB_ISSUES_TITLE_PREFIX',
                                                          self.gitlab_issues_title_prefix)

        self.processes = multiprocessing.cpu_count()

        # If no repo then definitely pass extra params
        if self.no_repo:
            self.pass_extra_params = True

        # Verify software dependencies — Docker is always required for test sandboxing
        packages = ["timeout", "git", "docker"]
        self.check_dependencies(packages)

        # Validate non-Canvas mode settings
        if self.input_method not in ("canvas", "db"):
            self.logger.logger.warning(
                "Unknown input_method '%s', defaulting to 'canvas'." % self.input_method)
            self.input_method = "canvas"
        if self.output_method not in ("canvas", "gitlab_issues"):
            self.logger.logger.warning(
                "Unknown output_method '%s', defaulting to 'canvas'." % self.output_method)
            self.output_method = "canvas"
        if self.output_method == "gitlab_issues" and self.gitlab_project_id == 0:
            self.logger.logger.warning(
                "output_method is 'gitlab_issues' but gitlab_project_id is not set. "
                "GitLab issues will not be created.")
