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
    process = subprocess.Popen(test_command,
                               cwd="%s/" % configuration.athina_test_tmp_dir,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    out, err = process.communicate()
    return out, err


def generate_firejail_profile(filename):
    file = open(os.path.join(os.path.dirname(__file__), "server.profile"), "r")
    profile_text = file.read()
    with open(filename, 'w') as out:
        out.write(profile_text)
    file.close()
