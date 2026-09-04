from setuptools import setup, find_packages

setup(
    name='athina',
    version='1.0.0',
    packages=find_packages(exclude=['tests', 'tests.*']),
    entry_points={
        'console_scripts': [
            'athina-cli=athina.cli:run',
        ],
    },
    install_requires=[
        # Core grading engine
        'filelock', 'python-dateutil', 'requests', 'numpy', 'peewee',
        'copydetect', 'pyyaml', 'psutil', 'gitpython', 'pymysql',
        'beautifulsoup4', 'lxml',
    ],
    extras_require={
        'web': [
            'Django>=3.2,<4.0',
            'djangorestframework>=3.12',
            'django-registration>=3.1',
            'gunicorn>=20.0',
        ],
        'test': [
            'pytest>=7.0', 'docker>=6.0',
            'pytest-timeout>=2.0', 'pytest-cov>=4.0',
        ],
    },
    url='https://github.com/athina-edu/athina',
    license='MIT',
    author='Michail Tsikerdekis',
    author_email='tsikerdekis@gmail.com',
    description='Automated grading platform with web dashboard',
    include_package_data=True,
)
