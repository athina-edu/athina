import hashlib
import os
import subprocess
import time

from athina.git.git import get_repo_commit
from athina.users import load_key_from_assignment_data, update_key_in_assignment_data

__all__ = ('docker_build', 'docker_run',)


def __generate_hash(string):
    return hashlib.md5(string.encode("ascii")).hexdigest()


def docker_build(configuration, logger):
    repo_commit = get_repo_commit(configuration.config_dir)
    if repo_commit == load_key_from_assignment_data(configuration.course_id,
                                                    configuration.assignment_id,
                                                    "repo_commit"):
        logger.logger.info("No changes in the repository, will not re-build dockerfile.")
        return False
    update_key_in_assignment_data(configuration.course_id, configuration.assignment_id, "repo_commit", repo_commit)

    build_statement = ["docker", "build", "-t", "%s" % __generate_hash(configuration.config_dir),
                       "-f", "Dockerfile", "."]
    # Building the image. This is built once and then things are much faster but the check needs to happen
    logger.logger.debug(" ".join(build_statement))
    process = subprocess.Popen(build_statement, cwd="%s/" % configuration.config_dir, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)

    try:
        out, err = process.communicate(timeout=900)  # A build should typically be ready after 15 minutes
    except subprocess.TimeoutExpired:
        # Kill container
        _terminate_all_containers()
        logger.logger.warning("Terminated build container and any others that may be hanging for more than 900 secs")
        err = True
        out = ""

    if process.returncode and err:
        logger.logger.error("Docker build returned error: %s" % err.decode("utf-8", "backslashreplace"))
        logger.logger.debug("Log trace:")
        logger.logger.debug(out.decode("utf-8", "backslashreplace"))
        return False
    else:
        return True


def docker_run(test_script, configuration, logger):
    container_name = __generate_hash("%s-%s" % (time.time(), os.getpid()))
    run_statement = ["docker", "run", "-e", "TEST=%s" % test_script,
                     "--stop-timeout", "1", "--rm",
                     "--memory-swap", configuration.docker_memory_limit,
                     "--memory", configuration.docker_memory_limit,
                     "-e", "STUDENT_DIR=%s" % configuration.athina_student_code_dir,
                     "-e", "TEST_DIR=%s" % configuration.athina_test_tmp_dir,
                     "-e", "EXTRA_PARAMS=%s" % " ".join(configuration.extra_params)]

    if not configuration.docker_use_seccomp:
        run_statement.extend(["--cap-add=SYS_PTRACE", "--security-opt", "seccomp=unconfined"])

    if configuration.docker_use_net_admin:
        run_statement.extend(["--cap-add=NET_ADMIN"])

    run_statement.extend(["-v", "%s:%s" % (
        configuration.athina_student_code_dir, configuration.athina_student_code_dir),
                          "-v",
                          "%s:%s" % (configuration.athina_test_tmp_dir, configuration.athina_test_tmp_dir),
                          "--name", "%s" % container_name,
                          "%s" % __generate_hash(configuration.config_dir)])

    logger.logger.debug(" ".join(run_statement))

    process = subprocess.Popen(run_statement, cwd="%s/" % configuration.config_dir,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)

    try:
        process.wait(configuration.test_timeout)
    except subprocess.TimeoutExpired:
        # Kill container
        _terminate_container(container_name)
        logger.logger.warning("Terminated container %s due to timeout." % container_name)

    out, err = process.communicate()
    logger.logger.debug("Docker output:\n %s" % out.decode("utf-8", "backslashreplace"))
    logger.logger.debug("Docker errors:\n %s" % err.decode("utf-8", "backslashreplace"))
    _terminate_container(container_name)

    # The chown below needs to run just in case there were files that created with docker's user
    _docker_chown(configuration, logger, configuration.athina_test_tmp_dir)
    _docker_chown(configuration, logger, configuration.athina_student_code_dir)

    return out, err


def _terminate_container(container_name):
    subprocess.Popen(["docker", "stop", "-t", "1", "%s" % container_name],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _terminate_all_containers():
    subprocess.Popen(["docker", "stop", "$(docker", "ps", "- a", "- q)"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)


# This function will change permissions for any files created by docker that are not owned by the user running athina
def _docker_chown(configuration, logger, directory):
    container_name = __generate_hash("%s-%s" % (time.time(), os.getpid()))
    run_statement = ["docker", "run", "--rm", "-v",
                     "%s:%s" % (directory, directory),
                     "ubuntu", "chown", "-R", "%s" % os.getuid(), directory]
    logger.logger.debug(" ".join(run_statement))
    process = subprocess.Popen(run_statement, cwd="%s/" % configuration.config_dir,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    out, err = process.communicate()
    logger.logger.debug("Docker output:\n %s" % out.decode("utf-8", "backslashreplace"))
    logger.logger.debug("Docker errors:\n %s" % err.decode("utf-8", "backslashreplace"))
    return out, err
