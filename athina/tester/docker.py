import hashlib
import subprocess
import os
import time


def __generate_hash(string):
    return hashlib.md5(string.encode("ascii")).hexdigest()


def docker_build(configuration, logger):
    build_statement = ["docker", "build", "-t", "%s" % __generate_hash(configuration.config_dir), "-f", "Dockerfile", "."]
    # Building the image. This is built once and then things are much faster but the check needs to happen
    logger.logger.debug(" ".join(build_statement))
    process = subprocess.Popen(build_statement, cwd="%s/" % configuration.config_dir, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    out, err = process.communicate()

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
                     "--stop-timeout", "1",
                     "-e", "STUDENT_DIR=%s" % configuration.athina_student_code_dir,
                     "-e", "TEST_DIR=%s" % configuration.athina_test_tmp_dir,
                     "-e", "EXTRA_PARAMS=%s" % " ".join(configuration.extra_params),
                     "-v", "%s:%s" % (
                         configuration.athina_student_code_dir, configuration.athina_student_code_dir),
                     "-v",
                     "%s:%s" % (configuration.athina_test_tmp_dir, configuration.athina_test_tmp_dir),
                     "--name", "%s" % container_name,
                     "%s" % __generate_hash(configuration.config_dir)]

    logger.logger.debug(" ".join(run_statement))

    process = subprocess.Popen(run_statement, cwd="%s/" % configuration.config_dir,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)

    try:
        process.wait(configuration.test_timeout)
    except subprocess.TimeoutExpired:
        # Kill container
        terminate_container(container_name)

    out, err = process.communicate()
    terminate_container(container_name)

    return out, err


def terminate_container(container_name):
    subprocess.Popen(["docker", "stop", "-t", "1", "%s" % container_name],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
