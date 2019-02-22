import os


def generate_firejail_profile(filename):
    file = open(os.path.join(os.path.dirname(__file__), "server.profile"), "r")
    profile_text = file.read()
    with open(filename, 'w') as out:
        out.write(profile_text)
