from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SplitKernelBuildTests(unittest.TestCase):
    def test_each_role_keeps_the_camera_kernel_fragment(self) -> None:
        script_path = ROOT / "scripts" / "container_build_split_kernels.sh"
        script = script_path.read_text(encoding="utf-8")

        subprocess.run(["sh", "-n", str(script_path)], check=True)
        self.assertIn(
            'fragment_list="$base_fragment_rel $fragment_rel"',
            script,
        )
        self.assertIn(
            'BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\\"$fragment_list\\"',
            script,
        )
        self.assertIn(
            'for required_fragment in "$fragment_dir/kernel.fragment" "$fragment_file"',
            script,
        )
        self.assertIn("'# CONFIG_'*' is not set'", script)


if __name__ == "__main__":
    unittest.main()
