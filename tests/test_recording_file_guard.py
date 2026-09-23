"""Compile the exact uhttpd recording guard added by its packaged patch."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RecordingFileGuardTests(unittest.TestCase):
    def test_packaged_guard_rejects_replacement_links_and_nonregular_files(self):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        patch = (ROOT / "patches/uhttpd/0017-bind-recording-file-identity.patch").read_text()
        section = patch.split("+++ b/recording_file.h\n", 1)[1]
        header = "\n".join(line[1:] for line in section.splitlines() if line.startswith("+")) + "\n"
        with tempfile.TemporaryDirectory(prefix="recording-guard-", dir=os.environ["TMPDIR"]) as directory:
            work = Path(directory)
            (work / "recording_file.h").write_text(header)
            executable = work / "guard"
            subprocess.run([compiler, "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-I", str(work), str(ROOT / "tests/fixtures/recording-files/guard.c"), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable), str(work)], check=True, capture_output=True, timeout=10)

    def test_identity_is_internal_and_checked_on_the_streaming_descriptor(self):
        patch = (ROOT / "patches/uhttpd/0017-bind-recording-file-identity.patch").read_text()
        self.assertIn('!proxy->authorizing || !proxy->local_file', patch)
        self.assertIn('proxy->local_file_identity[0]', patch)
        self.assertIn('"/mnt/mmcblk0p1/raptor/", 22', patch)
        self.assertIn('recording_file_open(path, identity)', patch)
        self.assertIn('recording_file_matches(fd, expected)', patch)
        self.assertNotIn('blobmsg_add_string(&proxy->hdr, "X-Thingino-File-Identity"', patch)


if __name__ == "__main__":
    unittest.main()
