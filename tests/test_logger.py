import unittest
import athina.logger
import os


class TestFunctions(unittest.TestCase):
    def check_text_in_log(self, text):
        self.assertEqual("abc" in text, True, "info text")
        self.assertEqual("cde" in text, True, "warning text")
        self.assertEqual("efg" in text, True, "error text")

    @staticmethod
    def read_file_into_text(logfile):
        f = open(logfile, "r")
        lines = f.readlines()
        text = "\n".join(lines)
        f.close()
        return text

    def test_logger_create(self):
        for file_name in {'logs/athina.log', 'logs/tests.log'}:
            if os.path.exists(file_name):
                os.remove(file_name)

        logger = athina.logger.Logger()
        logger.set_verbose(True)
        logger.set_debug(True)

        logger.set_assignment_log_file("%s/%s.log" % ("logs", "tests"))

        logger.logger.info("abc")
        logger.logger.warning("cde")
        logger.logger.error("efg")

        text = self.read_file_into_text('logs/athina.log')
        self.check_text_in_log(text)

        text = self.read_file_into_text('logs/tests.log')
        self.check_text_in_log(text)

        import tests.helper_files.logger_script
        tests.helper_files.logger_script.start_logger()

        text = self.read_file_into_text('logs/tests.log')
        self.assertEqual("test1" in text, True, "import logger, check if logger settings are available globally")
