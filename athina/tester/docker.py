import hashlib
import subprocess


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
    run_statement = ["docker", "run", "-e", "TEST=%s" % test_script,
                     "--stop-timeout", "%d" % configuration.test_timeout,
                     "-e", "STUDENT_DIR=%s" % configuration.athina_student_code_dir,
                     "-e", "TEST_DIR=%s" % configuration.athina_test_tmp_dir,
                     "-e", "EXTRA_PARAMS=%s" % " ".join(configuration.extra_params),
                     "-v", "%s:%s" % (configuration.athina_student_code_dir, configuration.athina_student_code_dir),
                     "-v", "%s:%s" % (configuration.athina_test_tmp_dir, configuration.athina_test_tmp_dir),
                     "%s" % __generate_hash(configuration.config_dir)]

    logger.logger.debug(" ".join(run_statement))

    process = subprocess.Popen(run_statement, cwd="%s/" % configuration.config_dir,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    out, err = process.communicate()

    if process.returncode and err:
        pass
    else:
        err = b""  # removing any warnings since we do not care about these

    return out, err
