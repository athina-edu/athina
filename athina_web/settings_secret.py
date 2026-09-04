import os

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'dev-secret-key-for-local-testing-only'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

# Database — SQLite for local development
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db.sqlite3'),
    }
}

# Grading engine database (MySQL — used for student data, grades, etc.)
if os.environ.get('ATHINA_MYSQL_HOST', None) is None:
    os.environ['ATHINA_MYSQL_HOST'] = 'localhost'
if os.environ.get('ATHINA_MYSQL_PORT', None) is None:
    os.environ['ATHINA_MYSQL_PORT'] = '3307'
if os.environ.get('ATHINA_MYSQL_USERNAME', None) is None:
    os.environ['ATHINA_MYSQL_USERNAME'] = 'athina'
if os.environ.get('ATHINA_MYSQL_PASSWORD', None) is None:
    os.environ['ATHINA_MYSQL_PASSWORD'] = 'athina_dev_pass'
