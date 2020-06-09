import shutil
from athina.logger import logger, Logger

# highly unlikely but just in case
if logger is None:
    logger = Logger()
    logger.create_logger()

__all__ = ('copy_dir', 'rm_dir',)


def copy_dir(source, destination):
    try:
        shutil.copytree(source, destination, symlinks=True, ignore_dangling_symlinks=True)
    except FileNotFoundError:
        logger.logger.error("Could not copy %s to %s" % (source, destination))
    except shutil.Error:
        logger.logger.error("Could not copy %s to %s" % (source, destination))


def rm_dir(folder):
    try:
        shutil.rmtree(folder)
    except PermissionError:
        logger.logger.error("Cannot delete %s. Likely permissions error." % folder)
        raise PermissionError(folder)  # This is supposed to still be raised to alert of serious issues
    except FileNotFoundError:
        pass
