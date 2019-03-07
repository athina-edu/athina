from setuptools import setup, find_packages

setup(
    name='athina',
    version='0.9',
    packages=find_packages(),
    scripts=['bin/athina-cli'],
    install_requires=['np', 'filelock', 'python-dateutil', 'requests', 'numpy', 'psutil'],
    url='https://github.com/athina-edu/athina',
    license='MIT',
    author='Michail Tsikerdekis',
    author_email='tsikerdekis@gmail.com',
    description='',
    test_suite="tests"
)
