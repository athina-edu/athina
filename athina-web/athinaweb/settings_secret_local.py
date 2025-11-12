SECRET_KEY = 'dev-local-secret'
DEBUG = True
ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': '/code/db.sqlite3',
    }
}

STATIC_URL = '/static/'
STATIC_ROOT = '/code/static_files'
