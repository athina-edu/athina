import logging
import logging.handlers


class WatchedRotatingFileHandler(logging.handlers.RotatingFileHandler, logging.handlers.WatchedFileHandler):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self.dev, self.ino = -1, -1
        self._statstream()

    def emit(self, record):
        self.reopenIfNeeded()
        super().emit(record)


class Logger:
    """
    The Logger scripts uses set function and recreates a logger's state based on its configuration.
    The logger object (self.logger) can be deleted and recreated using create_logger.
    """
    logger = None  # object can be released and recreated (workaround for pickling using multiprocessing)
    _verbose = True
    _debug = False
    _logfile = None

    def create_logger(self):
        self.delete_logger()
        logging_state = logging.DEBUG if self._debug else logging.INFO
        self.logger = logging.getLogger('athina')
        self.logger.setLevel(logging_state)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # create file handler which logs info messages
        fh = WatchedRotatingFileHandler('athina.log', maxBytes=100000, backupCount=3)
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
            fc = WatchedRotatingFileHandler(self._logfile, maxBytes=100000, backupCount=3)
            fc.setLevel(logging_state)
            fc.setFormatter(formatter)
            self.__safe_add_handler(fc)

    def __safe_add_handler(self, handler):
        handler_found = False
        self.logger.debug(self.logger.handlers)  # debug line
        for single_handler in self.logger.handlers:
            if single_handler.stream.name == handler.stream.name:
                handler_found = True
        if not handler_found:
            self.logger.addHandler(handler)

    def delete_logger(self):
        if self.logger is not None:
            self.logger.handlers = []
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
