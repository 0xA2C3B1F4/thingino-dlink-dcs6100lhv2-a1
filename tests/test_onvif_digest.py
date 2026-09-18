from __future__ import annotations

import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading
import unittest

from tests.test_onvif_control_imaging_bridge import _added_file


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/onvif/0002-snapshot-digest.patch"
HASH = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
REALM = "Thingino ONVIF"


class OnvifDigestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="onvif-digest-", dir=os.environ["TMPDIR"])
        root = Path(cls.temp.name)
        patch = PATCH.read_text()
        for name in ("onvif_digest.c", "onvif_digest.h"):
            (root / name).write_text(_added_file(patch, f"src/{name}"))
        (root / "size.c").write_text(
            '#include "onvif_digest.h"\n'
            'size_t digest_state_size(void) { return sizeof(onvif_digest_state); }\n'
        )
        try:
            subprocess.run([
                "cc", "-std=c99", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror",
                "-shared", "-fPIC", str(root / "onvif_digest.c"), str(root / "size.c"),
                "-o", str(root / "digest.so"),
            ], check=True, capture_output=True, timeout=20)
            cls.lib = ctypes.CDLL(str(root / "digest.so"))
            cls.lib.digest_state_size.restype = ctypes.c_size_t
            args = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint64, HASH]
            cls.lib.onvif_digest_challenge.argtypes = args + [ctypes.c_void_p]
            cls.lib.onvif_digest_challenge.restype = ctypes.c_bool
            cls.lib.onvif_digest_verify.argtypes = args + [ctypes.c_char_p, ctypes.c_char_p]
            cls.lib.onvif_digest_verify.restype = ctypes.c_bool
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.state = ctypes.create_string_buffer(self.lib.digest_state_size())
        self.username = b"root"
        self.password = b"test-only-password"
        self.now = 1000

        @HASH
        def hash_bytes(data: int, size: int, output: int) -> bool:
            result = hashlib.md5(ctypes.string_at(data, size)).digest()
            ctypes.memmove(output, result, len(result))
            return True

        self.hash = hash_bytes

    def challenge(self) -> str:
        result = ctypes.create_string_buffer(33)
        self.assertTrue(self.lib.onvif_digest_challenge(
            self.state, self.username, self.password, self.now, self.hash, result,
        ))
        nonce = result.value.decode()
        self.assertRegex(nonce, r"^[0-9a-f]{32}$")
        return nonce

    def header(self, nonce: str, *, target: str = "/onvif/image.cgi",
               nc: str = "00000001", cnonce: str = "client-one",
               password: bytes | None = None, method: str = "GET",
               realm: str = REALM) -> str:
        password = self.password if password is None else password
        md5 = lambda value: hashlib.md5(value).hexdigest()
        ha1 = md5(self.username + b":" + realm.encode() + b":" + password)
        ha2 = md5(f"{method}:{target}".encode())
        response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode())
        quoted_cnonce = cnonce.replace("\\", "\\\\").replace('"', '\\"')
        return (f'Digest username="{self.username.decode()}", realm="{realm}", '
                f'nonce="{nonce}", uri="{target}", response="{response}", '
                f'algorithm=MD5, qop=auth, cnonce="{quoted_cnonce}", nc={nc}')

    def verify(self, header: str, target: str = "/onvif/image.cgi") -> bool:
        return self.lib.onvif_digest_verify(
            self.state, self.username, self.password, self.now, self.hash,
            target.encode(), header.encode(),
        )

    def test_both_streams_and_replay_counters(self) -> None:
        nonce = self.challenge()
        first = self.header(nonce)
        self.assertTrue(self.verify(first))
        self.assertFalse(self.verify(first))
        second = self.header(nonce, target="/onvif/image1.cgi", nc="00000003")
        self.assertTrue(self.verify(second, "/onvif/image1.cgi"))
        self.assertFalse(self.verify(self.header(nonce, nc="00000002")))
        self.assertTrue(self.verify(self.header(nonce, cnonce="client-two")))

    def test_authentication_failure_does_not_consume_counter(self) -> None:
        nonce = self.challenge()
        self.assertFalse(self.verify(self.header(nonce, password=b"wrong")))
        self.assertTrue(self.verify(self.header(nonce)))

    def test_expiry_clock_reversal_and_restart(self) -> None:
        for delta in (300, 301, -1):
            with self.subTest(delta=delta):
                nonce = self.challenge()
                self.now += delta
                self.assertFalse(self.verify(self.header(nonce)))
        nonce = self.challenge()
        self.state = ctypes.create_string_buffer(self.lib.digest_state_size())
        self.assertFalse(self.verify(self.header(nonce)))
        self.assertTrue(self.verify(self.header(self.challenge())))

    def test_uri_remains_usable_with_fresh_authentication_after_expiry(self) -> None:
        nonce = self.challenge()
        self.now += 299
        self.assertTrue(self.verify(self.header(nonce)))
        self.now += 1
        self.assertFalse(self.verify(self.header(nonce, nc="00000002")))
        self.assertTrue(self.verify(self.header(self.challenge())))

    def test_credential_rotation_invalidates_old_nonce(self) -> None:
        nonce = self.challenge()
        old = self.header(nonce)
        self.password = b"replacement-password"
        self.assertFalse(self.verify(old))
        self.assertFalse(self.verify(self.header(nonce)))
        self.assertTrue(self.verify(self.header(self.challenge())))

    def test_bounded_nonce_table_retires_whole_old_nonce(self) -> None:
        old = self.challenge()
        for index in range(8):
            self.assertTrue(self.verify(self.header(old, cnonce=f"client-{index}")))
        nonces = []
        for _ in range(16):
            nonce = self.challenge()
            nonces.append(nonce)
            for index in range(8):
                self.assertTrue(self.verify(self.header(nonce, cnonce=f"client-{index}")))
        self.assertEqual(len(set([old] + nonces)), 17)
        self.assertFalse(self.verify(self.header(old, nc="00000002")))
        self.assertTrue(self.verify(self.header(nonces[-1], cnonce="client-0", nc="00000002")))

    def test_unauthenticated_challenges_do_not_evict_pending_nonce(self) -> None:
        nonce = self.challenge()
        for _ in range(100):
            self.assertEqual(self.challenge(), nonce)
        self.assertTrue(self.verify(self.header(nonce)))

    def test_client_table_pressure_retires_nonce_not_replay_state(self) -> None:
        nonce = self.challenge()
        for index in range(8):
            self.assertTrue(self.verify(self.header(nonce, cnonce=f"client-{index}")))
        self.assertFalse(self.verify(self.header(nonce, cnonce="forged", password=b"wrong")))
        self.assertTrue(self.verify(self.header(nonce, cnonce="client-0", nc="00000002")))
        self.assertFalse(self.verify(self.header(nonce, cnonce="overflow")))
        self.assertFalse(self.verify(self.header(nonce, cnonce="client-0", nc="00000002")))
        self.assertFalse(self.verify(self.header(nonce, cnonce="client-0")))
        self.assertTrue(self.verify(self.header(self.challenge())))

    def test_malformed_fields_and_binding(self) -> None:
        nonce = self.challenge()
        good = self.header(nonce)
        bad = [
            "", "Basic abc", good + ",", good + ', username="root"',
            good + ', USERNAME="root"', good + ', unknown="x"',
            good.replace("algorithm=MD5", "algorithm=SHA-256"),
            good.replace("qop=auth", "qop=auth-int"),
            good.replace('username="root"', 'username="other"'),
            good.replace('cnonce="client-one"', 'cnonce=""'),
            good.replace('cnonce="client-one"', 'cnonce="' + "x" * 129 + '"'),
            good.replace('cnonce="client-one"', 'cnonce="unterminated'),
            good.replace('cnonce="client-one"', 'cnonce="bad\\q"'),
            good + "\r\nAuthorization: other", "Digest " + "x" * 2048,
            self.header(nonce, method="POST"), self.header(nonce, realm="other"),
            self.header(nonce, target="/onvif/image1.cgi"),
        ]
        bad.extend(self.header(nonce, nc=value) for value in (
            "00000000", "1", "000000001", "0000000g", "-0000001",
        ))
        for value in bad:
            with self.subTest(header=value):
                self.assertFalse(self.verify(value))
        self.assertFalse(self.verify(good, "/onvif/image.cgi?token=anything"))
        self.assertTrue(self.verify(good))

    def test_missing_algorithm_uses_md5_default(self) -> None:
        self.assertTrue(self.verify(self.header(self.challenge()).replace("algorithm=MD5, ", "")))

    def test_counter_maximum_and_wrap(self) -> None:
        nonce = self.challenge()
        maximum = self.header(nonce, nc="ffffffff")
        self.assertTrue(self.verify(maximum))
        self.assertFalse(self.verify(maximum))
        self.assertFalse(self.verify(self.header(nonce, nc="00000000")))
        self.assertFalse(self.verify(self.header(nonce, nc="00000001")))

    def test_truncations_and_escaped_cnonce(self) -> None:
        nonce = self.challenge()
        good = self.header(nonce, cnonce='slash\\quote"x')
        for length in range(len(good)):
            with self.subTest(length=length):
                self.assertFalse(self.verify(good[:length]))
        self.assertFalse(self.verify(good[:good.index("cnonce=")] + 'cnonce="end\\'))
        self.assertTrue(self.verify(good.replace("algorithm=MD5", "algorithm=md5")))
        self.assertTrue(self.verify(self.header(nonce, cnonce="x" * 128)))
        self.assertFalse(self.verify(self.header(nonce, cnonce="x" * 129)))

    def test_observed_username_rotation_and_return_invalidates_nonce(self) -> None:
        nonce = self.challenge()
        original = self.header(nonce)
        self.username = b"other"
        self.assertFalse(self.verify(original))
        self.assertTrue(self.verify(self.header(self.challenge())))
        self.username = b"root"
        self.assertFalse(self.verify(original))
        self.assertTrue(self.verify(self.header(self.challenge())))

    def test_hash_failure_after_credential_check_preserves_counter(self) -> None:
        nonce = self.challenge()
        good = self.header(nonce)
        original_hash = self.hash
        for failure_call in (2, 3):
            calls = 0

            @HASH
            def fail_at(data: int, size: int, output: int) -> bool:
                nonlocal calls
                calls += 1
                return False if calls == failure_call else original_hash(data, size, output)

            self.hash = fail_at
            self.assertFalse(self.verify(good))
        self.hash = original_hash
        self.assertTrue(self.verify(good))

    @unittest.skipUnless(shutil.which("curl"), "curl is required for client interoperability")
    def test_real_curl_digest_client_roundtrip_both_paths(self) -> None:
        owner = self
        received: list[tuple[str, bool]] = []
        errors: list[BaseException] = []
        jpeg = b"\xff\xd8test-only-jpeg\xff\xd9"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                try:
                    authorized = owner.verify(self.headers.get("Authorization", ""), self.path)
                    received.append((self.path, authorized))
                    if authorized:
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(jpeg)))
                        self.end_headers()
                        self.wfile.write(jpeg)
                    else:
                        nonce = owner.challenge()
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", (
                            f'Digest realm="{REALM}", nonce="{nonce}", algorithm=MD5, qop="auth"'
                        ))
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                except BaseException as error:
                    errors.append(error)
                    self.close_connection = True

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            for path in ("/onvif/image.cgi", "/onvif/image1.cgi"):
                result = subprocess.run([
                    "curl", "--noproxy", "*", "--max-time", "3", "--fail",
                    "--silent", "--show-error", "--digest", "--user",
                    "root:test-only-password", f"http://127.0.0.1:{server.server_port}{path}",
                ], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, jpeg)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(received, [
            ("/onvif/image.cgi", False), ("/onvif/image.cgi", True),
            ("/onvif/image1.cgi", False), ("/onvif/image1.cgi", True),
        ])

    def test_empty_credentials_and_crypto_failure_fail_closed(self) -> None:
        nonce = self.challenge()
        old = self.header(nonce)
        self.password = b""
        self.assertFalse(self.verify(old))
        result = ctypes.create_string_buffer(33)
        self.assertFalse(self.lib.onvif_digest_challenge(
            self.state, self.username, self.password, self.now, self.hash, result,
        ))
        self.password = b"test-only-password"
        self.assertFalse(self.verify(old))
        failing_hash = HASH(lambda _data, _size, _out: False)
        self.assertFalse(self.lib.onvif_digest_challenge(
            self.state, self.username, self.password, self.now, failing_hash, result,
        ))

    def test_parser_bounds_under_address_and_undefined_sanitizers(self) -> None:
        root = Path(self.temp.name)
        harness = root / "sanitize.c"
        harness.write_text(r'''
#include "onvif_digest.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static bool hash(const void *input, size_t length, unsigned char out[16]) {
    (void)input; (void)length; memset(out, 0, 16); return true;
}
int main(void) {
    onvif_digest_state state = {0};
    char nonce[33], header[1024];
    if (!onvif_digest_challenge(&state, "root", "dummy", 1, hash, nonce)) return 1;
    snprintf(header, sizeof(header),
        "Digest username=\"root\", realm=\"Thingino ONVIF\", nonce=\"%s\", "
        "uri=\"/onvif/image.cgi\", response=\"00000000000000000000000000000000\", "
        "algorithm=MD5, qop=auth, cnonce=\"slash\\\\quote\\\"x\", nc=00000001", nonce);
    size_t length = strlen(header);
    for (size_t count = 0; count <= length; count++) {
        char *exact = malloc(count + 1);
        if (!exact) return 2;
        memcpy(exact, header, count); exact[count] = 0;
        (void)onvif_digest_verify(&state, "root", "dummy", 1, hash,
                                  "/onvif/image.cgi", exact);
        free(exact);
    }
    for (size_t index = 0; index < length; index++) {
        char old = header[index];
        for (unsigned byte = 1; byte < 256; byte++) {
            header[index] = (char)byte;
            (void)onvif_digest_verify(&state, "root", "dummy", 1, hash,
                                      "/onvif/image.cgi", header);
        }
        header[index] = old;
    }
    return 0;
}
''')
        binary = root / "sanitize"
        compiled = subprocess.run([
            "cc", "-std=c99", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            str(root / "onvif_digest.c"), str(harness), "-o", str(binary),
        ], capture_output=True, timeout=20)
        self.assertEqual(compiled.returncode, 0, compiled.stderr.decode(errors="replace"))
        checked = subprocess.run([str(binary)], capture_output=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode(errors="replace"))
        self.assertNotIn(b"runtime error:", checked.stderr)


if __name__ == "__main__":
    unittest.main()
