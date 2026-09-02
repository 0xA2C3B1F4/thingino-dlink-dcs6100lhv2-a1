#!/usr/bin/env python3
"""Exercise the pinned uhttpd Thingino Control relay against a local backend."""
import http.client
import json
import os
import queue
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time


class Backend(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        self.requests = queue.Queue()
        super().__init__(address, Handler)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(8193)
        if not line:
            return
        method, target, version = line.decode("ascii").strip().split(" ")
        headers = []
        while True:
            line = self.rfile.readline(8193)
            if line == b"\r\n":
                break
            name, value = line.decode("latin-1").rstrip("\r\n").split(":", 1)
            headers.append((name.lower(), value.strip()))
        length = int(dict(headers).get("content-length", "0"))
        body = self.rfile.read(length)
        self.server.requests.put((method, target, version, headers, body))

        if target == "/api/v1/stall":
            time.sleep(5)
        if target == "/api/v1/runtime/media/metrics":
            payload = (
                b"prudynt_rtsp_clients 1\n"
                b"prudynt_rtsp_queue_bytes 0\n"
                b"prudynt_mjpeg_rejections_total 0\n"
            )
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain; version=0.0.4\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
        if target == "/api/v1/unsafe-content-type":
            payload = b"<html>unsafe</html>\n"
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
        if target == "/api/v1/plain-content-type":
            payload = b"plain text is not an allowed Control API response\n"
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
        if target == "/api/v1/wrong-prometheus-version":
            payload = b"prudynt_rtsp_clients 1\n"
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain; version=1.0.0\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
        if target.startswith("/api/v1/internal/media-authorize?target="):
            self.wfile.write(
                b"HTTP/1.1 204 No Content\r\nContent-Type: application/json\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            return
        if target in ("/snapshot?ch=0", "/snapshot?ch=1"):
            channel = target[-1:].encode()
            payload = b"\xff\xd8direct-snapshot-" + channel + b"\xff\xd9"
            self.wfile.write(
                b"HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\n"
                b'Content-Disposition: attachment; filename="snapshot-ch'
                + channel
                + b'.jpg"\r\n'
                b"Content-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
        if target == "/api/v1/redirect":
            self.wfile.write(
                b"HTTP/1.1 302 Found\r\nLocation: /login.html\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            return
        if target == "/api/v1/auth/login":
            payload = b'{"success":true,"is_default_password":false}\n'
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Set-Cookie: thingino_session=0123456789abcdef0123456789abcdef; "
                b"Path=/; Max-Age=86400; HttpOnly; SameSite=Strict\r\n"
                b"Content-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
        if target == "/api/v1/auth/logout":
            self.wfile.write(
                b"HTTP/1.1 204 No Content\r\nContent-Type: application/json\r\n"
                b"Set-Cookie: thingino_session=; Path=/; Max-Age=0; HttpOnly; "
                b"SameSite=Strict\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            return
        if target == "/api/v1/bad-cookie":
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Set-Cookie: unrelated=value\r\nContent-Length: 3\r\n"
                b"Connection: close\r\n\r\n{}\n"
            )
            return
        if target == "/api/v1/oversized-response":
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2097153\r\nConnection: close\r\n\r\n"
            )
            return
        if target == "/api/v1/zero":
            payload = b""
        elif target == "/api/v1/large":
            payload = b'{"data":"' + b"x" * (1024 * 1024) + b'"}\n'
        else:
            payload = json.dumps({"target": target}, separators=(",", ":")).encode() + b"\n"
        self.wfile.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + payload
        )


def request(method, path, headers=None, body=b"", timeout=7):
    connection = http.client.HTTPConnection("127.0.0.1", 18080, timeout=timeout)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def wait_ready(process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("uhttpd exited before accepting requests")
        try:
            if request("GET", "/ready.txt")[0] == 200:
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("uhttpd did not become ready")


def one_header(headers, name):
    values = [value for key, value in headers if key == name]
    assert len(values) == 1, (name, values)
    return values[0]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: host_uhttpd_proxy.py UHTTPD")
    with tempfile.TemporaryDirectory(prefix="thingino-uhttpd-proxy-") as root:
        with open(os.path.join(root, "ready.txt"), "wb") as output:
            output.write(b"ready\n")
        backend = Backend(("127.0.0.1", 1998))
        backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
        backend_thread.start()
        media_backend = Backend(("127.0.0.1", 8080))
        media_thread = threading.Thread(
            target=media_backend.serve_forever, daemon=True
        )
        media_thread.start()
        environment = os.environ.copy()
        process = subprocess.Popen(
            [
                sys.argv[1],
                "-f",
                "-h",
                root,
                "-p",
                "127.0.0.1:18080",
                "-T",
                "8",
                "-E",
                "/ready.txt",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_ready(process)
            fallback_status, _, fallback_body = request("GET", "/preview.html")
            assert fallback_status == 200 and fallback_body == b"ready\n"
            status, response_headers, payload = request(
                "GET",
                "/api/v1/health",
                {
                    "Authorization": "Bearer public-fixture",
                    "Cookie": "thingino_session=fixture",
                    "X-API-Key": "fixture-key",
                    "Accept": "application/json",
                    "X-Thingino-Proxy": "client-spoof",
                    "X-Thingino-Remote-Addr": "203.0.113.9",
                },
            )
            assert status == 200
            assert response_headers["Cache-Control"] == "no-store"
            assert payload == b'{"target":"/api/v1/health"}\n'
            method, target, version, headers, body = backend.requests.get(timeout=1)
            assert (method, target, version, body) == ("GET", "/api/v1/health", "HTTP/1.1", b"")
            assert one_header(headers, "authorization") == "Bearer public-fixture"
            assert one_header(headers, "cookie") == "thingino_session=fixture"
            assert one_header(headers, "x-api-key") == "fixture-key"
            assert one_header(headers, "x-thingino-proxy") == "1"
            assert one_header(headers, "x-thingino-remote-addr") == "127.0.0.1"

            metrics_status, metrics_headers, metrics_body = request(
                "GET", "/api/v1/runtime/media/metrics"
            )
            assert metrics_status == 200
            assert metrics_headers["Content-Type"] == "text/plain; version=0.0.4"
            assert metrics_body == (
                b"prudynt_rtsp_clients 1\n"
                b"prudynt_rtsp_queue_bytes 0\n"
                b"prudynt_mjpeg_rejections_total 0\n"
            )
            _, metrics_target, _, _, _ = backend.requests.get(timeout=1)
            assert metrics_target == "/api/v1/runtime/media/metrics"
            for rejected_target in (
                "/api/v1/unsafe-content-type",
                "/api/v1/plain-content-type",
                "/api/v1/wrong-prometheus-version",
            ):
                assert request("GET", rejected_target)[0] == 502
                _, queued_target, _, _, _ = backend.requests.get(timeout=1)
                assert queued_target == rejected_target

            snapshot_status, snapshot_headers, snapshot_body = request(
                "GET",
                "/api/v1/actions/snapshot?stream_id=0",
                {"Cookie": "thingino_session=fixture"},
            )
            assert snapshot_status == 200
            assert snapshot_headers["Content-Type"] == "image/jpeg"
            assert snapshot_headers["Content-Disposition"] == (
                'attachment; filename="snapshot-ch0.jpg"'
            )
            assert snapshot_body == b"\xff\xd8direct-snapshot-0\xff\xd9"
            method, target, version, headers, body = backend.requests.get(timeout=1)
            assert method == "GET" and version == "HTTP/1.1" and body == b""
            assert target == (
                "/api/v1/internal/media-authorize?target="
                "%2Fapi%2Fv1%2Factions%2Fsnapshot%3Fstream_id%3D0"
            )
            assert one_header(headers, "cookie") == "thingino_session=fixture"
            method, target, version, headers, body = media_backend.requests.get(
                timeout=1
            )
            assert (method, target, version, body) == (
                "GET",
                "/snapshot?ch=0",
                "HTTP/1.0",
                b"",
            )
            assert all(name != "cookie" for name, _ in headers)

            onvif_status, onvif_headers, onvif_body = request(
                "GET", "/onvif/image1.cgi"
            )
            assert onvif_status == 200
            assert onvif_headers["Content-Type"] == "image/jpeg"
            assert onvif_headers["Content-Disposition"] == (
                'attachment; filename="snapshot-ch1.jpg"'
            )
            assert onvif_body == b"\xff\xd8direct-snapshot-1\xff\xd9"
            method, target, version, headers, body = backend.requests.get(timeout=1)
            assert method == "GET" and version == "HTTP/1.1" and body == b""
            assert target == (
                "/api/v1/internal/media-authorize?target=%2Fonvif%2Fimage1.cgi"
            )
            assert one_header(headers, "x-thingino-proxy") == "2"
            method, target, version, headers, body = media_backend.requests.get(
                timeout=1
            )
            assert (method, target, version, body) == (
                "GET",
                "/snapshot?ch=1",
                "HTTP/1.0",
                b"",
            )

            status, _, payload = request(
                "POST",
                "/api/v1/actions/daynight",
                {"Content-Type": "application/json"},
                b'{"mode":"day"}',
            )
            assert status == 200 and payload
            _, _, _, headers, body = backend.requests.get(timeout=1)
            assert one_header(headers, "content-type") == "application/json"
            assert body == b'{"mode":"day"}'

            started = time.monotonic()
            assert request("GET", "/api/v1/zero")[0] == 200
            assert time.monotonic() - started < 1
            redirect_status, redirect_headers, redirect_body = request(
                "GET", "/api/v1/redirect"
            )
            assert redirect_status == 302
            assert redirect_headers["Location"] == "/login.html"
            assert redirect_body == b""

            login_status, login_headers, login_body = request(
                "POST",
                "/api/v1/auth/login",
                {"Content-Type": "application/json"},
                b'{"username":"root","password":"__SET_LOCALLY__"}',
            )
            assert login_status == 200 and login_body
            assert login_headers["Set-Cookie"] == (
                "thingino_session=0123456789abcdef0123456789abcdef; "
                "Path=/; Max-Age=86400; HttpOnly; SameSite=Strict"
            )
            logout_status, logout_headers, logout_body = request(
                "POST", "/api/v1/auth/logout"
            )
            assert logout_status == 204 and logout_body == b""
            assert logout_headers["Set-Cookie"] == (
                "thingino_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            )
            assert request("GET", "/api/v1/bad-cookie")[0] == 502

            slow_client = socket.create_connection(("127.0.0.1", 18080), timeout=2)
            slow_client.sendall(
                b"GET /api/v1/large HTTP/1.1\r\nHost: fixture\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            assert b"HTTP/1.1 200 OK" in slow_client.recv(256)
            started = time.monotonic()
            assert request("GET", "/ready.txt")[0] == 200
            assert time.monotonic() - started < 0.5
            slow_client.close()
            assert request("GET", "/api/v1/health")[0] == 200

            assert request("GET", "/api/v1/oversized-response")[0] == 502

            stalled = {}
            stall_thread = threading.Thread(
                target=lambda: stalled.setdefault("response", request("GET", "/api/v1/stall")),
                daemon=True,
            )
            stall_thread.start()
            backend.requests.get(timeout=1)
            started = time.monotonic()
            static_status, _, static_body = request("GET", "/ready.txt")
            assert static_status == 200 and static_body == b"ready\n"
            assert time.monotonic() - started < 0.5
            stall_thread.join(6)
            assert stalled["response"][0] == 504

            status, _, _ = request(
                "POST",
                "/api/v1/actions/daynight",
                {"Content-Length": "4097"},
                b"x" * 4097,
            )
            assert status == 413

            children_path = f"/proc/{process.pid}/task/{process.pid}/children"
            with open(children_path, "r", encoding="ascii") as source:
                assert source.read().strip() == ""
            print("uhttpd Thingino Control proxy: PASS")
            print("static SPA fallback: PASS")
            print("forwarding/auth-context strip: PASS")
            print("authorized WebUI and ONVIF in-memory snapshot relay: PASS")
            print("POST body, session cookies, zero-length response, and login redirect: PASS")
            print("nonblocking static response during stalled backend: PASS")
            print("slow-client disconnect and follow-up request: PASS")
            print("backend timeout, request cap, and response cap: PASS")
            print("uhttpd child processes: 0")
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            backend.shutdown()
            backend.server_close()
            backend_thread.join(timeout=1)
            media_backend.shutdown()
            media_backend.server_close()
            media_thread.join(timeout=1)
            if process.returncode not in (0, -15):
                stderr = process.stderr.read().decode("utf-8", "replace")
                raise RuntimeError(f"uhttpd exited {process.returncode}: {stderr}")


if __name__ == "__main__":
    main()
