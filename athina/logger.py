import logging
import logging.handlers


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
        self.logger = None
        logging_state = logging.DEBUG if self._debug else logging.INFO
        self.logger = logging.getLogger('athina')
        self.logger.setLevel(logging_state)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # create file handler which logs info messages
        fh = logging.handlers.RotatingFileHandler('athina.log', maxBytes=1000, backupCount=3)
        fh.setLevel(logging_state)
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        if self._verbose:
            # create console handler
            ch = logging.StreamHandler()
            ch.setLevel(logging_state)
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

        if self._logfile is not None:
            fc = logging.handlers.RotatingFileHandler(self._logfile, maxBytes=500, backupCount=3)
            fc.setLevel(logging_state)
            fc.setFormatter(formatter)
            self.logger.addHandler(fc)

    def set_verbose(self, state):
        self._verbose = state
        self.create_logger()

    def set_assignment_log_file(self, logfile):
        self._logfile = logfile
        self.create_logger()

    def set_debug(self, state):
        self._debug = state
        self.create_logger()
