FROM ubuntu:18.04

# Things that our test scripts use and need to have installed
RUN apt update && apt install -y pylint3 bc

## Docker specific cheat-sheet
# You can add files to your image to use, e.g., adding a sql file to a database:
# ADD empsmall.zip .

# If packages require interactive input, use this (leave it always enabled just in case):
ENV DEBIAN_FRONTEND=noninteractive

# Athina passes the following ENV parameters to Docker 
# $TEST (the test that is to run)
# $STUDENT_DIR (the directory with the student code)
# $TEST_DIR (the directory with the test code, i.e., tests)
# $EXTRA_PARAMS: secondary_id, assignment_due_date_isoformat (both retrieved from e-learning platform)
# Activated for assignments that do not use a repository (in cfg norepo=true)

# IMPORTANT: these are only accessible for the ENTRYPOINT below not under RUN (this is how docker works)

ENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR $EXTRA_PARAMS
