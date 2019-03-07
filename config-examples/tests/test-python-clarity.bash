#!/bin/bash
student_dir=$1
test_dir=$2
cd $student_dir # Default directory where student code is copied for testing

touch ~/.pylintrc # Supress pylint warning message, use default config
IFS=

echo "Use pylint3 on your code to clean it up and increase your score for this test"
echo "Partial pylint3's output"
output=$(pylint3 *.py)
echo $output|head -n15
echo "--Score--"
score=$(echo $output | sed -n 's/.*rated at \(.\{1,5\}\)\/10.*/\1/p')
if [ -z "$score" ]; then
    echo "No *.py files found or no readable python code found"
    echo 0
    exit
fi
if [[ $(echo $score'<='0 | bc -l) -eq 1 ]]; then
      	echo "Score lower than -5 in pylint3: 0%"
        echo 0
fi
if [[ $(echo $score'>'0 | bc -l) -eq 1 ]]; then
      	echo "pylint3 score is: $score"
        echo $score*10 | bc -l
fi
