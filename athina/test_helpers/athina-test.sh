#!/usr/bin/env bash
# athina-test: report individual test results to Athina.
#
# Athina normally parses only the single number a test script prints last. That is
# enough to grade, but not enough to show a per-test breakdown or let a TA adjust
# one test. Source this file and use the functions below; Athina then shows each
# named test separately, with pass/fail and a score, in the override UI.
#
# Usage in a test script (bash):
#
#   . "$(dirname "$0")/athina-test.sh"
#
#   athina_test_begin "q1_employees" "List Elvis's employees" weight=0.8
#   ... run the check ...
#   if [ "$diffs" -eq 0 ]; then
#       athina_test_pass "q1_employees"
#   else
#       athina_test_fail "q1_employees" "output differed on 2 lines"
#   fi
#
#   athina_test_begin "q2_count" "Count of Elvis's employees"
#   if [ "$ok" -eq 1 ]; then athina_test_pass "q2_count"; athina_test_score "q2_count" 100
#   else athina_test_fail "q2_count"; athina_test_score "q2_count" 40; fi
#
#   athina_test_end            # prints the final number Athina parses
#
# Records are written to stderr as "ATHINA_TEST: key|status|score|weight|name|detail"
# so they never contaminate the student output being compared. Anything that does
# not use this protocol is unaffected: Athina derives one row per script instead.

ATHINA_TEST_PREFIX="ATHINA_TEST:"
# Total weight of tests declared so far, used when a test is not given one.
_ATHINA_TEST_TOTAL_WEIGHT=0
_ATHINA_TEST_CURRENT=""
# key -> "score weight name passed detail"
declare -A _ATHINA_TEST_ROWS
declare -A _ATHINA_TEST_ORDER

_athina_escape() {
    # Newlines and pipes would break the record format.
    printf '%s' "$1" | tr '\n' ' ' | tr '|' '/'
}

athina_test_begin() {
    local key="$1" name="${2:-$1}" weight=""
    shift 2 2>/dev/null || shift $#
    for arg in "$@"; do
        case "$arg" in
            weight=*) weight="${arg#weight=}" ;;
        esac
    done
    if [ -z "$weight" ]; then
        weight=1
    fi
    _ATHINA_TEST_CURRENT="$key"
    _ATHINA_TEST_ORDER["$key"]="${#_ATHINA_TEST_ORDER[@]}"
    # status is unknown until pass/fail is called
    _ATHINA_TEST_ROWS["$key"]="0|$weight|$(_athina_escape "$name")|?|"
    _ATHINA_TEST_TOTAL_WEIGHT=$(echo "$_ATHINA_TEST_TOTAL_WEIGHT+$weight" | bc -l)
}

# athina_test_score <key> <0-100>   (can be combined with pass/fail)
athina_test_score() {
    local key="$1" score="$2"
    local row="${_ATHINA_TEST_ROWS[$key]}"
    [ -z "$row" ] && return 0
    local w n p d
    IFS='|' read -r _ w n p d <<< "$row"
    _ATHINA_TEST_ROWS["$key"]="$score|$w|$n|$p|$d"
}

_athina_record() {
    local key="$1" status="$2" detail="$3"
    local row="${_ATHINA_TEST_ROWS[$key]}"
    if [ -z "$row" ]; then
        # pass/fail without begin: declare it implicitly
        athina_test_begin "$key" "$key"
        row="${_ATHINA_TEST_ROWS[$key]}"
    fi
    local score w n _p _d
    IFS='|' read -r score w n _p _d <<< "$row"
    case "$status" in
        pass) [ "$score" = "0" ] && score=100 ;;
        fail) [ "$score" = "0" ] && score=0 ;;
    esac
    _ATHINA_TEST_ROWS["$key"]="$score|$w|$n|$status|$(_athina_escape "$detail")"
}

athina_test_pass() { _athina_record "$1" pass "${2:-}"; }
athina_test_fail() { _athina_record "$1" fail "${2:-}"; }

# athina_test_end: emit the records and the overall percentage Athina parses.
athina_test_end() {
    local key score w n p d
    local sum=0
    local total="${_ATHINA_TEST_TOTAL_WEIGHT:-0}"
    for key in "${!_ATHINA_TEST_ROWS[@]}"; do
        IFS='|' read -r score w n p d <<< "${_ATHINA_TEST_ROWS[$key]}"
        printf '%s%s|%s|%s|%s|%s|%s\n' "$ATHINA_TEST_PREFIX" "$key" "$p" "$score" "$w" "$n" "$d" >&2
        sum=$(echo "$sum+$score*$w" | bc -l)
    done
    if [ "$(echo "$total > 0" | bc -l)" = "1" ]; then
        echo "scale=2; $sum / $total" | bc -l
    else
        echo "0.00"
    fi
}
