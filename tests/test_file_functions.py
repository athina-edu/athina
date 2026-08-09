# Tests for athina.file_functions (copy_dir / rm_dir helpers).
import os
import shutil
from unittest import mock, TestCase

from athina.file_functions import copy_dir, rm_dir


class TestFileFunctions(TestCase):
    def test_copy_dir_success(self):
        src = "/tmp/cov_src"
        dst = "/tmp/cov_dst"
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("hi")
        copy_dir(src, dst)
        self.assertTrue(os.path.isfile(os.path.join(dst, "f.txt")))
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)

    def test_copy_dir_file_not_found(self):
        with mock.patch('athina.file_functions.logger.logger.error') as mock_err:
            copy_dir("/tmp/does_not_exist_src", "/tmp/does_not_exist_dst")
            mock_err.assert_called()

    def test_copy_dir_shutil_error(self):
        with mock.patch('athina.file_functions.shutil.copytree', side_effect=shutil.Error("x")), \
                mock.patch('athina.file_functions.logger.logger.error') as mock_err:
            copy_dir("/tmp/a", "/tmp/b")
            mock_err.assert_called()

    def test_rm_dir_success(self):
        folder = "/tmp/cov_rm"
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        rm_dir(folder)
        self.assertFalse(os.path.exists(folder))

    def test_rm_dir_file_not_found(self):
        rm_dir("/tmp/does_not_exist_rm")  # should not raise

    def test_rm_dir_permission_error(self):
        with mock.patch('athina.file_functions.shutil.rmtree', side_effect=PermissionError("denied")), \
                mock.patch('athina.file_functions.logger.logger.error') as mock_err:
            with self.assertRaises(PermissionError):
                rm_dir("/tmp/x")
            mock_err.assert_called()
