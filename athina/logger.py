import logging
import logging.handlers
import os
from multiprocessing import current_process


class Logger:
    """
    The Logger scripts uses set function and recreates a logger's state based on its configuration.
    The logger object (self.logger) can be deleted and recreated using create_logger.
    """
    logger = None  # object can be released and recreated (workaround for pickling using multiprocessing)
    _verbose = True
    _debug = False
    _logfile = None

    @staticmethod
    def _create_handler(logfile):
        # This solves race conditions with log rotation when multiple processes are running
        # We defer rotation after the children finish running
        if current_process().name == 'MainProcess':
            return logging.handlers.RotatingFileHandler(logfile, maxBytes=1000000, backupCount=3)
        else:
            return logging.handlers.RotatingFileHandler(logfile, maxBytes=0, backupCount=0)

    def create_logger(self):
        self.delete_logger()

        logging_state = logging.DEBUG if self._debug else logging.INFO
        self.logger = logging.getLogger('athina')
        self.logger.setLevel(logging_state)
        formatter = logging.Formatter('%(asctime)s - %(process)d - %(levelname)s - %(message)s')

        # create file handler which logs info messages
        if not os.path.isdir('logs'):
            os.mkdir('logs')
        fh = self._create_handler('logs/athina.log')
        fh.setLevel(logging_state)
        fh.setFormatter(formatter)
        self.__safe_add_handler(fh)

        if self._verbose:
            # create console handler
            ch = logging.StreamHandler()
            ch.setLevel(logging_state)
            ch.setFormatter(formatter)
            self.__safe_add_handler(ch)

        if self._logfile is not None:
            fc = self._create_handler(self._logfile)
            fc.setLevel(logging_state)
            fc.setFormatter(formatter)
            self.__safe_add_handler(fc)

    def __safe_add_handler(self, handler):
        handler_found = False
        for single_handler in self.logger.handlers:
            if single_handler.stream.name == handler.stream.name:
                handler_found = True
        if not handler_found:
            self.logger.addHandler(handler)

    def delete_logger(self):
        if self.logger is not None:
            handlers = self.logger.handlers[:]
            for handler in handlers:
                self.logger.removeHandler(handler)
                handler.close()
            self.logger = None

    def set_verbose(self, state):
        self._verbose = state
        self.create_logger()

    def set_assignment_log_file(self, logfile):
        self._logfile = logfile
        self.create_logger()

    def set_debug(self, state):
        self._debug = state
        self.create_logger()
