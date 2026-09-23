from __future__ import annotations

import ctypes
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.test_onvif_control_imaging_bridge import _added_file


ROOT = Path(__file__).resolve().parents[1]


class OnvifSnapshotIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="onvif-snapshot-", dir=os.environ["TMPDIR"])
        root = Path(cls.temp.name)
        cls.listener = socket.socket()
        cls.listener.bind(("127.0.0.1", 0))
        cls.listener.listen(2)
        cls.listener.settimeout(2)
        patch = (ROOT / "patches/onvif/0002-snapshot-digest.patch").read_text()
        for name in ("onvif_snapshot_io.c", "onvif_snapshot_io.h"):
            (root / name).write_text(_added_file(patch, f"src/{name}"))
        try:
            subprocess.run([
                "cc", "-std=c99", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror",
                "-shared", "-fPIC", f"-DONVIF_CONTROL_PORT={cls.listener.getsockname()[1]}",
                str(root / "onvif_snapshot_io.c"), "-o", str(root / "snapshot.so"),
            ], capture_output=True, check=True, timeout=20)
            cls.lib = ctypes.CDLL(str(root / "snapshot.so"))
            cls.lib.onvif_io_deadline.argtypes = [ctypes.c_uint]
            cls.lib.onvif_io_deadline.restype = ctypes.c_int64
            cls.lib.onvif_snapshot_relay.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_char_p, ctypes.c_int64]
            cls.lib.onvif_snapshot_relay.restype = ctypes.c_int
            cls.lib.onvif_io_write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int64]
            cls.lib.onvif_io_write.restype = ctypes.c_int
        except BaseException:
            cls.listener.close()
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.listener.close()
        cls.temp.cleanup()

    @staticmethod
    def response(jpeg: bytes, *, declared: int | None = None, extra: bytes = b"") -> bytes:
        return (b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg) if declared is None else declared).encode()
                + b"\r\n" + extra + b"\r\n" + jpeg)

    def relay(self, chunks: list[tuple[bytes, float]], *, stream: int = 0,
              budget: int = 3500) -> tuple[int, bytes, float]:
        errors: list[BaseException] = []
        requests: list[bytes] = []
        results: list[int] = []
        outgoing, incoming = socket.socketpair()
        incoming.settimeout(5)

        def backend() -> None:
            try:
                connection, _ = self.listener.accept()
                with connection:
                    connection.settimeout(3)
                    request = b""
                    while b"\r\n\r\n" not in request:
                        part = connection.recv(4096)
                        if not part or len(request) > 8192:
                            raise ValueError("incomplete backend request")
                        request += part
                    requests.append(request)
                    for data, delay in chunks:
                        if delay:
                            threading.Event().wait(delay)
                        connection.sendall(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The relay deliberately rejects bad or late responses.
            except BaseException as error:
                errors.append(error)

        def invoke() -> None:
            try:
                results.append(self.lib.onvif_snapshot_relay(
                    outgoing.fileno(), stream, b"a" * 64, self.lib.onvif_io_deadline(budget),
                ))
            finally:
                outgoing.shutdown(socket.SHUT_WR)

        server = threading.Thread(target=backend, daemon=True)
        worker = threading.Thread(target=invoke, daemon=True)
        started = time.monotonic()
        server.start()
        worker.start()
        output = bytearray()
        try:
            while part := incoming.recv(65536):
                output.extend(part)
            elapsed = time.monotonic() - started
        finally:
            worker.join(timeout=4)
            server.join(timeout=4)
            outgoing.close()
            incoming.close()
        self.assertFalse(worker.is_alive())
        self.assertFalse(server.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].startswith(
            f"GET /api/v1/actions/snapshot?stream_id={stream} HTTP/1.1\r\n".encode()))
        self.assertIn(b"Authorization: Bearer " + b"a" * 64 + b"\r\n", requests[0])
        self.assertNotIn(b"Digest", requests[0])
        return results[0], bytes(output), elapsed

    def test_binary_large_image_both_streams(self) -> None:
        jpeg = b"\xff\xd8" + bytes(range(256)) * 5000 + b"\xff\xd9"
        for stream in (0, 1):
            with self.subTest(stream=stream):
                status, output, _ = self.relay([(self.response(jpeg), 0)], stream=stream)
                self.assertEqual(status, 0)
                header, body = output.split(b"\r\n\r\n", 1)
                self.assertIn(b"Cache-Control: no-store", header)
                self.assertEqual(body, jpeg)

    def test_headers_and_soi_fragmented_at_each_boundary(self) -> None:
        jpeg = b"\xff\xd8hello\x00world\xff\xd9"
        full = self.response(jpeg)
        offset = full.index(b"\r\n\r\n") + 4
        for split in (offset - 3, offset - 2, offset - 1, offset, offset + 1, offset + 2):
            status, output, _ = self.relay([(full[:split], 0), (full[split:], 0.01)])
            self.assertEqual(status, 0)
            self.assertEqual(output.split(b"\r\n\r\n", 1)[1], jpeg)

    def test_rejects_backend_failure_and_malformed_headers_before_success(self) -> None:
        jpeg = b"\xff\xd8hi\xff\xd9"
        good = self.response(jpeg)
        bad = [
            good.replace(b"200 OK", b"403 Forbidden"),
            good.replace(b"200 OK", b"503 Unavailable"),
            good.replace(b"200 OK", b"302 Found"),
            good.replace(b"image/jpeg", b"text/plain"),
            good.replace(b"image/jpeg", b"image/jpeg\x00text"),
            self.response(jpeg, declared=2097153),
            self.response(jpeg, declared=3),
            self.response(jpeg, extra=b"Content-Length: 6\r\n"),
            self.response(jpeg, extra=b"Content-Type: image/jpeg\r\n"),
            self.response(jpeg, extra=b"Transfer-Encoding: chunked\r\n"),
            self.response(jpeg, extra=b"invalid header\r\n"),
            self.response(b"not-a-jpeg"),
            b"HTTP/1.1 200 OK\r\n" + b"x" * 8200,
        ]
        for response in bad:
            with self.subTest(response=response[:160]):
                status, output, _ = self.relay([(response, 0)])
                self.assertEqual(status, 502)
                self.assertEqual(output, b"")

    def test_truncated_body_closes_without_second_http_response(self) -> None:
        status, output, _ = self.relay([(self.response(b"\xff\xd8short", declared=100), 0)])
        self.assertEqual(status, -1)
        self.assertEqual(output.count(b"HTTP/1.1"), 1)
        self.assertLess(len(output.split(b"\r\n\r\n", 1)[1]), 100)

    def test_invalid_jpeg_ending_never_completes_the_advertised_body(self) -> None:
        bad = b"\xff\xd8invalid-endingXY"
        response = self.response(bad)
        schedules = [
            [(response, 0)],
            [(response[:-2], 0), (response[-2:-1], 0.01), (response[-1:], 0.01)],
        ]
        for chunks in schedules:
            with self.subTest(chunks=len(chunks)):
                status, output, _ = self.relay(chunks)
                self.assertEqual(status, -1)
                header, body = output.split(b"\r\n\r\n", 1)
                self.assertIn(f"Content-Length: {len(bad)}".encode(), header)
                self.assertEqual(len(body), len(bad) - 2)
                self.assertEqual(output.count(b"HTTP/1.1"), 1)

    def test_slow_trickle_cannot_extend_total_deadline(self) -> None:
        response = self.response(b"\xff\xd8hello\xff\xd9")
        status, output, elapsed = self.relay([(bytes([byte]), 0.03) for byte in response], budget=120)
        self.assertEqual(status, 502)
        self.assertEqual(output, b"")
        self.assertLess(elapsed, 0.8)
        status, output, _ = self.relay([(response, 0)])
        self.assertEqual(status, 0)
        self.assertTrue(output.endswith(b"\xff\xd9"))

    def test_invalid_stream_and_token_fail_before_connecting(self) -> None:
        for stream, token in ((2, b"a" * 64), (0, b""), (0, b"x" * 64), (0, b"a" * 63)):
            self.assertEqual(self.lib.onvif_snapshot_relay(
                -1, stream, token, self.lib.onvif_io_deadline(100),
            ), 500)

    def test_body_trickle_shares_header_deadline(self) -> None:
        jpeg = b"\xff\xd8" + b"x" * 50 + b"\xff\xd9"
        response = self.response(jpeg)
        offset = response.index(b"\r\n\r\n") + 4 + 2
        chunks = [(response[:offset], 0.04)] + [(bytes([byte]), 0.03) for byte in response[offset:]]
        status, output, elapsed = self.relay(chunks, budget=120)
        self.assertEqual(status, -1)
        self.assertLess(len(output.split(b"\r\n\r\n", 1)[1]), len(jpeg))
        self.assertLess(elapsed, 0.8)

    def test_client_backpressure_obeys_same_deadline(self) -> None:
        # A broken blocking-send implementation must fail this test, not hang CI.
        probe = '''
import ctypes, socket, sys, time
lib = ctypes.CDLL(sys.argv[1])
lib.onvif_io_deadline.argtypes = [ctypes.c_uint]
lib.onvif_io_deadline.restype = ctypes.c_int64
lib.onvif_io_write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int64]
lib.onvif_io_write.restype = ctypes.c_int
sender, receiver = socket.socketpair()
try:
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    body = ctypes.create_string_buffer(b"x" * (2 * 1024 * 1024))
    started = time.monotonic()
    result = lib.onvif_io_write(sender.fileno(), body, len(body), lib.onvif_io_deadline(120))
    assert result == -1, result
    assert time.monotonic() - started < 0.8
finally:
    sender.close()
    receiver.close()
'''
        result = subprocess.run([
            sys.executable, "-c", probe, str(Path(self.temp.name) / "snapshot.so"),
        ], capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
