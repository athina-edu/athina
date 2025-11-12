from setuptools import setup, find_packages

# This is the main versioning and package dependency file. Pipeenv and requirements.txt are there for providing
# compatibility with other components (e.g., pyup.io tracks only using requirements and check when a vulnerability
# exists for a package that may have become outdated in some dockerized build).

setup(
    name='athina',
    version='0.97',
    packages=find_packages(),
    scripts=['bin/athina-cli'],
    # 'np' is an unrelated, broken package on PyPI and conflicts with the project's use of numpy as 'np'
    # remove it from install_requires; numpy remains required.
    install_requires=['filelock', 'python-dateutil', 'requests', 'numpy', 'peewee', 'mosspy', 'pyyaml',
                      'psutil', 'gitpython', 'pymysql', 'beautifulsoup4', 'lxml'],
    extras_require={
        'test': ['pytest>=7.0', 'docker>=6.0', 'pytest-timeout>=2.0', 'pytest-cov>=4.0'],
    },
    url='https://github.com/athina-edu/athina',
    license='MIT',
    author='Michail Tsikerdekis',
    author_email='tsikerdekis@gmail.com',
    description='',
    test_suite="tests",
    include_package_data=True
)
