#!/bin/bash
student_dir=$1
test_dir=$2
cd $student_dir # Default directory where student code is copied for testing

touch ~/.pylintrc # Supress pylint warning message, use default config
IFS=

echo "Use pylint on your code to clean it up and increase your score for this test"
echo "Partial pylint's output"
# pylint 2.x writes the score to stdout; 3.x writes it to stderr, so capture both.
output=$(pylint *.py 2>&1)
echo "$output" | head -n15
echo "--Score--"
score=$(echo "$output" | sed -n 's/.*rated at \(.\{1,5\}\)\/10.*/\1/p')
if [ -z "$score" ]; then
    echo "No *.py files found or no readable python code found"
    echo 0
    exit
fi
# A pylint score is out of 10, and scores this low are treated as 0.
if [[ $(echo "$score<=0" | bc -l) -eq 1 ]]; then
    echo "Score lower than 0 in pylint: 0%"
    echo 0
    exit
fi
echo "pylint score is: $score"
# Scale the 0-10 pylint score to the 0-100 the engine expects.
echo "$score*10" | bc -l
