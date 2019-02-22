# ATHINA - Automated Testing Homework Interface for N Assignments

A formative assessment microservice for programming assignments.

The program is expected to run as a cron job.

# Here is what it does:
1. You build your tests like you would regularly using any scripting language of preference (bash, python etc.)
2. Your script can print anything you like and the last line is the grade from 0-100
3. You setup a configuration file (cfg) and place your files (tests and anything else you need) on a directory called tests 
inside the directory where your cfg file is.
4. You set Athina as a cron job. 
5. Students submit on Canvas their repo urls.
6. Athina, clones, looks for changes, checks for some safety, sandboxes code and then uses your tests.
7. Then submits the score given to it along with all text printed from the test back to the students Canvas
submission page (as a comment or file attachments containing the text).

# Security Features
* All tests are sandboxed (using firejail) so that student scripts cannot do damage to the OS or access existing files
* Only 1 student can submit the same git url, but can also permit more (for group projects)
* Moss implementation that also notifies student of the average similarity scores and the max for each student (planned to restrict this to instructor only)
* Git authentication only happens if git url is gitlab.cs.wwu.edu
* Git credentials and configuration cannot be obtained through student code execution
* Tests are forcefully timed out after a certain period of time (in case of infinite loops)

# Requirements:
* python 3.x
* git
* timeout
* firejail

# Recommended
These are optional depending on your test scripts:
* bats (used for testing)
* bc (for math in bash, used for testing)
* nc (for quickly looking at network packets, used for testing)
* pwgen (for quick random string generation in bash, used for testing)

# Installation
### Dependencies (Ubuntu):
`sudo apt install python3 git timeout firejail`
### Cloning and Installing
`git clone https://gitlab.cs.wwu.edu/athina/athina.git`

`pip install .`

or 

`pip install -e .` # Easier for live updating using git pull

# Usage
1. Build your tests as you would normally. Print as many things that you want students to see and make sure the last 
item(line) you print is their grade from 0-100. Decimals are accepted. The current student (being tested) files are 
always at `/tmp/athina` so change the current working directory for your scripts.
![test-script](https://gitlab.cs.wwu.edu/athina/athina/raw/master/docs/img/test-script.png "Test-Script")
![test-script-result](https://gitlab.cs.wwu.edu/athina/athina/raw/master/docs/img/test-script-result.png "Test-Script-Result")

2. Copy the config-example folder and setup the configuration file for athina with your settings. Canvas' access token
can be retrieved from your canvas' personal settings.
![config](https://gitlab.cs.wwu.edu/athina/athina/raw/master/docs/img/config.png "Config")
![canvas-access](https://gitlab.cs.wwu.edu/athina/athina/raw/master/docs/img/canvas-access.png "Canvas-Access")

3. Copy your tests inside your new folder's tests directory.

4. Run athina manually or place it into a cron.
    * Testing your config assignment (this won't save or send anything to canvas): 
    `athina-cli --config /path/to/config/folder --verbose=True --simulate=True`
    * Testing your config assignment for a specific student:
    `athina-cli --config /path/to/config/folder --verbose=True --simulate=True forced_testing="Michail Tsikerdekis"`
    * Testing your config assignment for the test student on Canvas (it has an empty name):
    `athina-cli --config /path/to/config/folder --verbose=True --simulate=True forced_testing=`
    * Running your config assignment but still receiving the log message on terminal (this will send grades to canvas for assignments that have submitted URLs):
    `athina-cli --config /path/to/config/folder --verbose=True`
    * Running your config assignment and getting log file inside config directory:
    `athina-cli --config /path/to/config/folder`
    * If you use athina_web to manage numerous assignments:
    `athina-cli --json http://yourathinaweburl/assignments/api`

## Additional info
See `assignmentsample.cfg` for options and other guidelines. An example (`test-script.bats`) that utilizes bats for 
testing is also provided.
