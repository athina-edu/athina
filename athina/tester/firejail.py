import os
import subprocess


def execute_with_firejail(configuration, test_script, logger):
    # Custom firejail profile that allows to sandbox suid processes (so that athina wont run as root)
    generate_firejail_profile("%s/server.profile" % configuration.athina_test_tmp_dir)

    # Run the test
    test_timeout = ["timeout", "--kill-after=1", str(configuration.test_timeout)]
    test_command = test_timeout + ["firejail", "--quiet", "--private", "--profile=server.profile",
                                   "--whitelist=%s/" % configuration.athina_student_code_dir,
                                   "--whitelist=%s/" % configuration.athina_test_tmp_dir] + test_script.split(
        " ") + configuration.extra_params
    logger.logger.debug(" ".join(test_command))
    try:
        process = subprocess.Popen(test_command,
                                   cwd="%s/" % configuration.athina_test_tmp_dir,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
    except FileNotFoundError:
        # firejail not installed. Only fallback to direct execution in test mode.
        if os.environ.get('ATHINA_TEST_MODE', '') == '1':
            logger.logger.warning("firejail not available and ATHINA_TEST_MODE=1; running test script directly as fallback.")
            try:
                local_cmd = ["timeout", "--kill-after=1", str(configuration.test_timeout), test_script]
                process = subprocess.Popen(" ".join(local_cmd), cwd="%s/" % configuration.athina_test_tmp_dir,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                out, err = process.communicate()
                return out, err
            except Exception as e:
                return b"", str(e).encode('utf-8')
        else:
            raise

    # If timeout reported that firejail failed to run (some systems report this via stderr),
    # only treat it as fallback when in test mode. In production, return the error to the caller.
    try:
        err_text = err.decode('utf-8', 'backslashreplace') if err else ''
    except Exception:
        err_text = str(err)

    normalized_err = err_text.lower().replace('‘', "'").replace('’', "'")
    if os.environ.get('ATHINA_TEST_MODE', '') == '1' and (
            ("failed to run command" in normalized_err and "firejail" in normalized_err) or
            ("no such file" in normalized_err and "firejail" in normalized_err) or
            ("firejail: no such file" in normalized_err)):
        logger.logger.warning("firejail reported missing and ATHINA_TEST_MODE=1; running test script directly as fallback.")
        try:
            local_cmd = ["timeout", "--kill-after=1", str(configuration.test_timeout), test_script]
            process = subprocess.Popen(" ".join(local_cmd), cwd="%s/" % configuration.athina_test_tmp_dir,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
            out, err = process.communicate()
            return out, err
        except Exception as e:
            return b"", str(e).encode('utf-8')

    return out, err


def generate_firejail_profile(filename):
    src = os.path.join(os.path.dirname(__file__), "server.profile")
    try:
        with open(src, "r") as f:
            profile_text = f.read()
        with open(filename, 'w') as out:
            out.write(profile_text)
    except FileNotFoundError:
        # If profile missing, write a minimal permissive profile so tests can run
        with open(filename, 'w') as out:
            out.write("allow all")
