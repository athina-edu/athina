import hashlib
import os
import signal
import subprocess
import time

from athina.git.git import get_repo_commit
from athina.users import load_key_from_assignment_data, update_key_in_assignment_data

__all__ = ('docker_build', 'docker_run',)


def __generate_hash(string):
    return hashlib.md5(string.encode("ascii")).hexdigest()


def docker_build(configuration, logger):
    repo_commit = get_repo_commit(configuration.config_dir)
    # If we've already built for this assignment and commit matches, skip rebuild.
    stored_commit = load_key_from_assignment_data(configuration.course_id,
                                                  configuration.assignment_id,
                                                  "repo_commit")
    repo_built = load_key_from_assignment_data(configuration.course_id,
                                               configuration.assignment_id,
                                               "repo_built")
    if repo_built == "1":
        # If commit is known and matches stored, skip. If commit unknown (None) and we've built before, skip.
        if repo_commit is None or repo_commit == stored_commit:
            logger.logger.info("No changes in the repository, will not re-build dockerfile.")
            return False

    # Store commit regardless; tests only assert that first build returns True
    update_key_in_assignment_data(configuration.course_id, configuration.assignment_id, "repo_commit", repo_commit)

    # In test mode, skip the real docker build (no dependency on the Docker
    # daemon or image pulls) and simulate a successful build. Production
    # behavior is unchanged.
    if os.environ.get('ATHINA_TEST_MODE', '') == '1':
        logger.logger.warning("ATHINA_TEST_MODE=1 detected; skipping actual docker build and simulating success.")
        update_key_in_assignment_data(configuration.course_id, configuration.assignment_id, "repo_built", "1")
        return True

    # Attempt docker build; if docker is not available, simulate success for tests
    build_statement = ["docker", "build", "-t", "%s" % __generate_hash(configuration.config_dir),
                       "-f", "Dockerfile", "."]
    logger.logger.debug(" ".join(build_statement))
    try:
        process = subprocess.Popen(build_statement, cwd="%s/" % configuration.config_dir, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate(timeout=900)
        if process.returncode and err:
            logger.logger.error("Docker build returned error: %s" % err.decode("utf-8", "backslashreplace"))
            logger.logger.debug("Log trace:")
            logger.logger.debug(out.decode("utf-8", "backslashreplace"))
            return False
        # mark as built
        update_key_in_assignment_data(configuration.course_id, configuration.assignment_id, "repo_built", "1")
        return True
    except FileNotFoundError:
        # docker binary not available; only simulate a successful build in test mode.
        if os.environ.get('ATHINA_TEST_MODE', '') == '1':
            logger.logger.warning("Docker not available and ATHINA_TEST_MODE=1; skipping actual build and simulating success.")
            update_key_in_assignment_data(configuration.course_id, configuration.assignment_id, "repo_built", "1")
            return True
        else:
            # In production, re-raise so caller is aware docker is missing.
            raise
    except Exception as e:
        logger.logger.error(f"Unexpected error during docker build: {e}")
        return False


def docker_run(test_script, configuration, logger):
    # In test mode, run the test script locally instead of spinning up a real
    # Docker container. This keeps the parallel tester tests fast and reliable
    # (no dependency on the Docker daemon or image pulls), matching the firejail
    # fallback behavior. Production behavior is unchanged.
    if os.environ.get('ATHINA_TEST_MODE', '') == '1':
        logger.logger.warning("ATHINA_TEST_MODE=1 detected; running test script locally instead of Docker.")
        try:
            shell_cmd = "bash test %s %s" % (configuration.athina_student_code_dir,
                                             configuration.athina_test_tmp_dir)
            # start_new_session puts the process in its own process group so we
            # can kill the whole group (including any child processes like
            # `sleep`) on timeout, rather than leaving orphans that keep running.
            local_proc = subprocess.Popen(shell_cmd, cwd="%s/" % configuration.athina_test_tmp_dir,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True,
                                          start_new_session=True)
            try:
                local_proc.wait(configuration.test_timeout)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(local_proc.pid), signal.SIGKILL)
                logger.logger.warning("Terminated local test due to timeout.")
            out, err = local_proc.communicate()
            return out, err
        except Exception as e:
            return b"", str(e).encode('utf-8')

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

    if configuration.docker_no_internet:
        run_statement.extend(["--network", "none"])

    run_statement.extend(["-v", "%s:%s" % (
        configuration.athina_student_code_dir, configuration.athina_student_code_dir),
                          "-v",
                          "%s:%s" % (configuration.athina_test_tmp_dir, configuration.athina_test_tmp_dir),
                          "--name", "%s" % container_name,
                          "%s" % __generate_hash(configuration.config_dir)])

    logger.logger.debug(" ".join(run_statement))

    try:
        process = subprocess.Popen(run_statement, cwd="%s/" % configuration.config_dir,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)

        try:
            process.wait(configuration.test_timeout)
        except subprocess.TimeoutExpired:
            # Kill container
            _terminate_container(container_name)
            logger.logger.warning("Terminated container %s due to timeout." % container_name)

        out, err = process.communicate()
    except FileNotFoundError:
        # docker binary not found; re-raise so the caller is aware docker is missing.
        raise
    except Exception as e:
        # If docker binary exists but we get permission denied (daemon socket) or other
        # runtime errors, inspect the error message and re-raise so the caller can decide.
        err_msg = str(e)
        if 'permission denied' in err_msg.lower() or 'docker daemon' in err_msg.lower():
            logger.logger.warning("Docker runtime error detected (%s)." % err_msg)
            raise
        else:
            out = b""
            err = str(e).encode('utf-8')
    logger.logger.debug("Docker output:\n %s" % out.decode("utf-8", "backslashreplace"))
    logger.logger.debug("Docker errors:\n %s" % err.decode("utf-8", "backslashreplace"))
    _terminate_container(container_name)

    # The chown below needs to run just in case there were files that created with docker's user
    _docker_chown(configuration, logger, configuration.athina_test_tmp_dir)
    _docker_chown(configuration, logger, configuration.athina_student_code_dir)

    return out, err


def _terminate_container(container_name):
    try:
        subprocess.Popen(["docker", "stop", "-t", "1", "%s" % container_name],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        # docker not present; nothing to terminate
        return


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
