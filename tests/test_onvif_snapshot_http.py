from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
import re
import socket
import subprocess
import threading
import unittest

from tests.test_onvif_control_imaging_bridge import _added_file
from tests import test_onvif_digest as digest_tests
from tests import test_onvif_snapshot_io as io_tests


HASH = digest_tests.HASH
TOKEN = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p)
ROOT = Path(__file__).resolve().parents[1]


class OnvifSnapshotHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        io_tests.OnvifSnapshotIOTests.setUpClass.__func__(cls)
        root = Path(cls.temp.name)
        patch = (ROOT / "patches/onvif/0002-snapshot-digest.patch").read_text()
        for name in ("onvif_digest.c", "onvif_digest.h", "onvif_snapshot_http.c", "onvif_snapshot_http.h"):
            (root / name).write_text(_added_file(patch, f"src/{name}"))
        try:
            subprocess.run([
                "cc", "-std=c99", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror",
                "-shared", "-fPIC", f"-DONVIF_CONTROL_PORT={cls.listener.getsockname()[1]}",
                str(root / "onvif_digest.c"), str(root / "onvif_snapshot_io.c"),
                str(root / "onvif_snapshot_http.c"), "-o", str(root / "http.so"),
            ], capture_output=True, check=True, timeout=20)
            cls.http = ctypes.CDLL(str(root / "http.so"))
            cls.http.onvif_snapshot_http.argtypes = [
                ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
                ctypes.c_char_p, ctypes.c_char_p, HASH, TOKEN, ctypes.c_int64,
            ]
            cls.http.onvif_snapshot_http.restype = ctypes.c_int
        except BaseException:
            cls.listener.close()
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        io_tests.OnvifSnapshotIOTests.tearDownClass.__func__(cls)

    def setUp(self) -> None:
        self.username = b"root"
        self.password = b"http-test-only-password"
        self.token_reads = 0
        self.token_ok = True

        @HASH
        def hash_bytes(data: int, length: int, out: int) -> bool:
            ctypes.memmove(out, hashlib.md5(ctypes.string_at(data, length)).digest(), 16)
            return True

        @TOKEN
        def token_reader(out: int) -> bool:
            self.token_reads += 1
            if self.token_ok:
                ctypes.memmove(out, b"a" * 64 + b"\x00", 65)
            return self.token_ok

        self.hash = hash_bytes
        self.token_reader = token_reader

    def exchange(self, header: bytes, extra_body: int = 0) -> bytes:
        writer, reader = socket.socketpair()
        reader.settimeout(1)
        try:
            buffer = ctypes.create_string_buffer(header)
            result = self.http.onvif_snapshot_http(
                writer.fileno(), buffer, len(header), extra_body,
                self.username, self.password, self.hash, self.token_reader,
                self.lib.onvif_io_deadline(500),
            )
            self.assertEqual(result, 0)
            writer.shutdown(socket.SHUT_WR)
            output = bytearray()
            while chunk := reader.recv(4096):
                output.extend(chunk)
            return bytes(output)
        finally:
            writer.close()
            reader.close()

    def challenge(self, target: str = "/onvif/image.cgi") -> str:
        reply = self.exchange(f"GET {target} HTTP/1.1\r\nHost: camera\r\nContent-Length: 0".encode())
        self.assertTrue(reply.startswith(b"HTTP/1.1 401 Unauthorized\r\n"))
        self.assertIn(b"Cache-Control: no-store\r\n", reply)
        self.assertEqual(self.token_reads, 0)
        return re.search(rb'nonce="([0-9a-f]{32})"', reply).group(1).decode()

    def authorized(self, nonce: str, target: str = "/onvif/image.cgi") -> bytes:
        auth = digest_tests.OnvifDigestTests.header(self, nonce, target=target)
        return f"GET {target} HTTP/1.1\r\nHost: camera\r\nAuthorization: {auth}\r\nContent-Length: 0".encode()

    def test_challenge_authenticated_jpeg_and_replay_both_streams(self) -> None:
        jpeg = b"\xff\xd8test-jpeg\xff\xd9"
        for stream, target in enumerate(("/onvif/image.cgi", "/onvif/image1.cgi")):
            self.token_reads = 0
            nonce = self.challenge(target)
            # Independent curl test exercises the core. Here use distinct clients
            # to avoid reusing a previously accepted counter across test requests.
            auth = digest_tests.OnvifDigestTests.header(self, nonce, target=target, cnonce=f"http-stream-{stream}")
            request = f"GET {target} HTTP/1.1\r\nHost: camera\r\nAuthorization: {auth}".encode()
            requests: list[bytes] = []
            errors: list[BaseException] = []

            def backend() -> None:
                try:
                    conn, _ = self.listener.accept()
                    with conn:
                        conn.settimeout(1)
                        data = b""
                        while b"\r\n\r\n" not in data:
                            part = conn.recv(4096)
                            if not part:
                                raise EOFError("truncated Control request")
                            data += part
                        requests.append(data)
                        conn.sendall(io_tests.OnvifSnapshotIOTests.response(jpeg))
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=backend, daemon=True)
            worker.start()
            try:
                reply = self.exchange(request)
            finally:
                worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(reply.startswith(b"HTTP/1.1 200 OK\r\n"))
            self.assertEqual(reply.split(b"\r\n\r\n", 1)[1], jpeg)
            self.assertEqual(self.token_reads, 1)
            self.assertEqual(len(requests), 1)
            self.assertTrue(requests[0].startswith(f"GET /api/v1/actions/snapshot?stream_id={stream} ".encode()))
            self.assertNotIn(b"Digest", requests[0])
            self.assertTrue(self.exchange(request).startswith(b"HTTP/1.1 401 "))
            self.assertEqual(self.token_reads, 1)

    def test_proxy_marker_and_cookie_cannot_authorize(self) -> None:
        reply = self.exchange(b"GET /onvif/image.cgi HTTP/1.1\r\nHost: camera\r\nX-Thingino-Proxy: 2\r\nCookie: thingino_session=forged")
        self.assertTrue(reply.startswith(b"HTTP/1.1 401 "))
        self.assertEqual(self.token_reads, 0)

    def test_invalid_http_fails_before_token_access(self) -> None:
        good = b"GET /onvif/image.cgi HTTP/1.1\r\nHost: camera"
        requests = [
            good.replace(b"GET ", b"POST "),
            good.replace(b"image.cgi", b"image.cgi?token=anything"),
            good.replace(b"GET ", b"GET\n"),
            good + b"\r\nAuthorization: first\r\nauthorization: second",
            good + b"\r\nContent-Length: 1",
            good + b"\r\nContent-Length: 0\r\nContent-Length: 0",
            good + b"\r\nTransfer-Encoding: chunked",
            good + b"\r\nExpect: 100-continue",
            good + b"\r\nAuthorization: one\x00two",
            good + b"\r\nAuthorization: one\ntwo",
            good + b"\r\n Authorization: folded",
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assertTrue(self.exchange(request).startswith(b"HTTP/1.1 400 "))
        self.assertTrue(self.exchange(good, extra_body=1).startswith(b"HTTP/1.1 400 "))
        self.assertEqual(self.token_reads, 0)

    def test_missing_or_rotated_credentials_and_missing_control_token(self) -> None:
        nonce = self.challenge()
        old = self.authorized(nonce)
        self.password = b"rotated-test-password"
        self.assertTrue(self.exchange(old).startswith(b"HTTP/1.1 401 "))
        self.assertEqual(self.token_reads, 0)
        nonce = self.challenge()
        self.token_ok = False
        self.assertTrue(self.exchange(self.authorized(nonce)).startswith(b"HTTP/1.1 503 "))
        self.assertEqual(self.token_reads, 1)
        self.password = None
        self.assertTrue(self.exchange(b"GET /onvif/image.cgi HTTP/1.1\r\nHost: camera").startswith(b"HTTP/1.1 503 "))
        self.assertEqual(self.token_reads, 1)


if __name__ == "__main__":
    unittest.main()
