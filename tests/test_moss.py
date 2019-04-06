import unittest
import shutil
from athina.moss import *
from athina.logger import *


class TestFunctions(unittest.TestCase):
    @unittest.skip("Moss service hangs for too long. Implement timeouts in moss.py")
    def test_moss(self):
        shutil.rmtree("/tmp/u1", ignore_errors=True)
        shutil.rmtree("/tmp/u2", ignore_errors=True)
        shutil.rmtree("/tmp/u3", ignore_errors=True)
        os.makedirs("/tmp/u1", exist_ok=True)
        os.makedirs("/tmp/u2", exist_ok=True)
        os.makedirs("/tmp/u3", exist_ok=True)
        f = open("/tmp/u1/test.py", 'a')
        f.write("print(1)\nprint(12345)")
        f.close()
        f = open("/tmp/u2/test.py", 'a')
        f.write("print(1)\nprint(54321)")
        f.close()
        f = open("/tmp/u3/test.py", 'a')
        f.write("a=9875\nprint(a)")
        f.close()

        logger = self.create_logger()
        x = Plagiarism(logger=logger,
                       service_type="moss",
                       moss_id=20181579,
                       moss_lang="python")
        data = x.check_plagiarism(["/tmp/u1/*.py",
                                   "/tmp/u2/*.py",
                                   "/tmp/u3/*.py"])
        self.assertEqual({1: [75], 2: [75]}, data)

    @staticmethod
    def create_logger():
        logger = Logger()
        logger.set_verbose(True)
        logger.set_debug(True)
        return logger
