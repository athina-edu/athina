FROM ubuntu:24.04

# Runtimes and tools the example test scripts need.
#   python3 + pylint  test-python-clarity.bash (the package is 'pylint' now;
#                     Ubuntu 18.04's 'pylint3' no longer exists)
#   bc                used by the score arithmetic in the same script
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip pylint bc \
    && rm -rf /var/lib/apt/lists/*

## Docker specific cheat-sheet
# You can add files to your image to use, e.g., adding a sql file to a database:
# ADD empsmall.zip .

# If packages require interactive input, use this (leave it always enabled just in case):
ENV DEBIAN_FRONTEND=noninteractive

# Athina passes the following ENV parameters to Docker 
# $TEST (the test that is to run)
# $STUDENT_DIR (the directory with the student code)
# $TEST_DIR (the directory with the test code, i.e., tests)
# $EXTRA_PARAMS: secondary_id, assignment_due_date_isoformat (both retrieved from
# e-learning platform). Only non-empty when pass_extra_params is true in the
# assignment config.
# Activated for assignments that do not use a repository (no_repo: true)

# IMPORTANT: these are only accessible for the ENTRYPOINT below, not under RUN
# (this is how docker works)

ENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR $EXTRA_PARAMS
