# Shared test helpers for the athina test suite.
from athina.configuration import Configuration
from tests.test_athina import create_logger


def make_config():
    """Build a Configuration with a verbose/debug logger."""
    logger = create_logger()
    configuration = Configuration(logger=logger)
    return configuration, logger
