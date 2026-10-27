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
        # filelock / gitpython / urllib3 are pinned to their first versions that
        # include the security fixes reported by Dependabot; without a floor, an
        # older transitive release can be resolved again on a fresh install.
        'filelock>=4.2.1', 'python-dateutil', 'requests>=2.32.4', 'numpy', 'peewee',
        'copydetect', 'pyyaml', 'psutil', 'gitpython>=3.2.1', 'pymysql',
        'beautifulsoup4', 'lxml',
    ],
    extras_require={
        'web': [
            'Django>=5.2,<6.0',
            'djangorestframework>=3.15',
            'django-registration>=3.4',
            'gunicorn>=22.0',
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
