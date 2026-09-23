from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/onvif/0003-control-token-reader.patch"
TOKEN = "0123456789abcdef" * 4


class OnvifControlTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(
            prefix="onvif-control-token-", dir=os.environ["TMPDIR"]
        )
        root = Path(cls.temp.name)
        tree = root / "applied-tree"
        (tree / "src").mkdir(parents=True)
        try:
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=tree,
                check=True,
                capture_output=True,
                timeout=20,
            )
            subprocess.run(
                ["git", "apply", str(PATCH)],
                cwd=tree,
                check=True,
                capture_output=True,
                timeout=20,
            )
            subprocess.run(
                [
                    "cc",
                    "-std=c99",
                    "-D_GNU_SOURCE",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-shared",
                    "-fPIC",
                    str(tree / "src/onvif_control_token.c"),
                    "-o",
                    str(root / "onvif_control_token.so"),
                ],
                check=True,
                capture_output=True,
                timeout=20,
            )
            cls.lib = ctypes.CDLL(str(root / "onvif_control_token.so"))
            cls.lib.onvif_control_token.argtypes = [
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_char),
            ]
            cls.lib.onvif_control_token.restype = ctypes.c_bool
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.path = Path(self.temp.name) / "thingino.json"

    def call(self, content: bytes | str, *, path: Path | None = None) -> tuple[bool, bytes]:
        target = self.path if path is None else path
        target.write_bytes(content.encode() if isinstance(content, str) else content)
        output = ctypes.create_string_buffer(65)
        result = self.lib.onvif_control_token(os.fsencode(target), output)
        return bool(result), output.value

    def test_reads_only_root_control_token_and_accepts_standard_values(self) -> None:
        decoy_token = "f" * 64
        document = {
            "unrelated": [
                None,
                True,
                False,
                -0,
                0,
                -12.5,
                1.2e3,
                {"text": "quote\\slash\" and \\u0061"},
            ],
            "decoy": {"control": {"token": decoy_token}},
            "control": {"nested": {"token": decoy_token}, "token": TOKEN},
        }
        ok, value = self.call(json.dumps(document, separators=(",", ":")))
        self.assertTrue(ok)
        self.assertEqual(value, TOKEN.encode())

    def test_accepts_leading_trailing_and_formatted_json_whitespace(self) -> None:
        document = (
            " \n\t{\n"
            '  "unrelated" : [ true, -1.25e+2, { "text" : "\\u0061" } ],\n'
            '  "control" : {\n'
            '    "other" : { "number" : 0.5E-2 },\n'
            '    "token" : "' + TOKEN + '"\n'
            "  }\n"
            "}\n \t"
        )
        ok, value = self.call(document)
        self.assertTrue(ok)
        self.assertEqual(value, TOKEN.encode())

    def test_decodes_escaped_ascii_keys_and_token(self) -> None:
        escaped = lambda text: "".join(f"\\u{ord(char):04x}" for char in text)
        document = (
            '{"' + escaped("control") + '":{"' + escaped("token") + '":"'
            + escaped(TOKEN) + '"}}'
        )
        ok, value = self.call(document)
        self.assertTrue(ok)
        self.assertEqual(value, TOKEN.encode())

    def test_rejects_missing_root_path_and_nested_decoys(self) -> None:
        cases = [
            '{"nested":{"control":{"token":"' + TOKEN + '"}}}',
            '{"control":{"nested":{"token":"' + TOKEN + '"}}}',
            '{"controls":{"token":"' + TOKEN + '"}}',
            '[{"control":{"token":"' + TOKEN + '"}}]',
            '{"control":null}',
            '{"control":{"token":true}}',
        ]
        for document in cases:
            with self.subTest(document=document):
                ok, value = self.call(document)
                self.assertFalse(ok)
                self.assertEqual(value, b"")

    def test_rejects_duplicate_target_keys_including_escaped_equivalents(self) -> None:
        cases = [
            '{"control":{"token":"' + TOKEN + '"},"co\\u006etrol":{}}',
            '{"control":{"token":"' + TOKEN + '","t\\u006fken":"' + TOKEN + '"}}',
        ]
        for document in cases:
            with self.subTest(document=document):
                ok, value = self.call(document)
                self.assertFalse(ok)
                self.assertEqual(value, b"")

    def test_rejects_malformed_json_trailing_data_and_bad_numbers_or_strings(self) -> None:
        cases = [
            '{"control":{"token":"' + TOKEN + '"',
            '{"control":{"token":"' + TOKEN + '"}} trailing',
            '{"control":{"token":"' + TOKEN + '"},}',
            '{"control":{"token":"' + TOKEN + '"},"x":01}',
            '{"control":{"token":"' + TOKEN + '"},"x":1.}',
            '{"control":{"token":"' + TOKEN + '"},"x":1e}',
            '{"control":{"token":"' + TOKEN + '"},"x":"bad\\q"}',
            '{"control":{"token":"' + TOKEN + '"},"x":"unterminated}',
            '{"control":{"token":"' + TOKEN + '"},"x":"line\nfeed"}',
        ]
        for document in cases:
            with self.subTest(document=document):
                ok, value = self.call(document)
                self.assertFalse(ok)
                self.assertEqual(value, b"")

    def test_rejects_bad_token_length_hex_and_embedded_nul(self) -> None:
        cases = [
            '{"control":{"token":""}}',
            '{"control":{"token":"' + TOKEN[:-1] + '"}}',
            '{"control":{"token":"' + TOKEN + '0"}}',
            '{"control":{"token":"' + "g" * 64 + '"}}',
            b'{"control":{"token":"' + TOKEN.encode() + b'"}}\x00',
            b'{"control":{"token":"' + TOKEN.encode()[:32] + b'\x00' + TOKEN.encode()[32:] + b'"}}',
        ]
        for document in cases:
            with self.subTest(document=document):
                ok, value = self.call(document)
                self.assertFalse(ok)
                self.assertEqual(value, b"")

    def test_depth_is_bounded(self) -> None:
        for count, expected in ((32, True), (33, False)):
            document = (
                '{"control":{"token":"' + TOKEN + '"},"deep":'
                + "[" * count
                + "null"
                + "]" * count
                + "}"
            )
            with self.subTest(count=count):
                ok, value = self.call(document)
                self.assertEqual(ok, expected)
                self.assertEqual(value, TOKEN.encode() if expected else b"")

    def test_rejects_oversize_truncated_symlink_and_fifo(self) -> None:
        oversize = b"{" + b"x" * (64 * 1024) + b"}"
        ok, value = self.call(oversize)
        self.assertFalse(ok)
        self.assertEqual(value, b"")

        truncated = self.path.with_name("truncated.json")
        truncated.write_bytes(b'{"control":{"token":"' + TOKEN.encode())
        output = ctypes.create_string_buffer(65)
        self.assertFalse(self.lib.onvif_control_token(os.fsencode(truncated), output))
        self.assertEqual(output.value, b"")

        symlink = self.path.with_name("symlink.json")
        symlink.symlink_to(self.path)
        output = ctypes.create_string_buffer(65)
        self.assertFalse(self.lib.onvif_control_token(os.fsencode(symlink), output))
        self.assertEqual(output.value, b"")

        fifo = self.path.with_name("config.fifo")
        os.mkfifo(fifo)
        self.assertEqual(stat.S_IFMT(fifo.stat().st_mode), stat.S_IFIFO)
        output = ctypes.create_string_buffer(65)
        self.assertFalse(self.lib.onvif_control_token(os.fsencode(fifo), output))
        self.assertEqual(output.value, b"")


if __name__ == "__main__":
    unittest.main()
