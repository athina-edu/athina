FROM ubuntu:18.04

# Things that our test scripts use and need to have installed
RUN apt update && apt install -y pylint3 bc

# $EXTRA_PARAMS: secondary_id, assignment_due_date_isoformat (both retrieved from e-learning platform)
# Activated for assignments that do not use a repository (in cfg norepo=true)

ENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR $EXTRA_PARAMS