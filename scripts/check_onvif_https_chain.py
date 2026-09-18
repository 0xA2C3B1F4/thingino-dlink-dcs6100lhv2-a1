"""Actual native uhttpd -> ONVIF -> fake Control, isolated container only."""
import http.server
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path('/deps')
TOKEN = 'a' * 64
PASSWORD = 'fixture-only-password'
IMAGE = b'\xff\xd8' + bytes(range(256)) * 4096 + b'\xff\xd9'


class Backend(http.server.BaseHTTPRequestHandler):
    calls = []
    status = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).calls.append((self.path, self.headers.get('Authorization')))
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            self.send_error(403)
            return
        self.send_response(type(self).status)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(IMAGE)))
        self.end_headers()
        try:
            self.wfile.write(IMAGE)
        except (BrokenPipeError, ConnectionResetError):
            # The relay closes immediately after a rejected backend status.
            pass


class HTTPSChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('RAPTOR_TEST_CONTAINER') != '1' or not ROOT.is_dir():
            raise RuntimeError('Run only in the isolated test container')
        cls.tmp = tempfile.TemporaryDirectory(prefix='https-chain-')
        cls.base = Path(cls.tmp.name)
        cls.resources = Path('/usr/share/onvif')
        cls.resources.mkdir(parents=True, exist_ok=True)
        cls.config = cls.resources / 'onvif.json'
        cls.write_config(PASSWORD)
        Path('/etc/thingino.json').write_text(json.dumps({'control': {'token': TOKEN}}))
        cls.cert, cls.key = cls.base / 'cert.pem', cls.base / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, capture_output=True, timeout=20)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 1998), Backend)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.log = (cls.base / 'processes.log').open('wb')
        cls.processes = []
        try:
            cls.processes.append(subprocess.Popen([str(ROOT / 'onvif-host/onvif_httpd')],
                                                 stdout=cls.log, stderr=cls.log))
            cls.processes.append(subprocess.Popen([
                str(ROOT / 'build-uhttpd/uhttpd'), '-f', '-h', str(cls.resources),
                '-p', '127.0.0.1:8080', '-s', '127.0.0.1:8443',
                '-C', str(cls.cert), '-K', str(cls.key), '-T', '5'],
                stdout=cls.log, stderr=cls.log))
            for port in (1999, 8443):
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError((cls.base / 'processes.log').read_text())
                        time.sleep(.02)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def write_config(cls, password):
        cls.config.write_text(json.dumps({'server': {'username': 'root', 'password': password}}))

    @classmethod
    def tearDownClass(cls):
        for proc in cls.processes:
            proc.terminate()
        for proc in cls.processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=2)
        cls.log.close()
        cls.tmp.cleanup()

    def setUp(self):
        Backend.calls.clear()
        Backend.status = 200
        self.write_config(PASSWORD)

    def fetch(self, path='/onvif/image.cgi', password=PASSWORD, tls=True, auth=True):
        body, headers = self.base / 'body', self.base / 'headers'
        args = ['curl', '-sS', '--max-time', '8', '--cacert', str(self.cert),
                '-o', str(body), '-D', str(headers), '-w', '%{http_code}']
        if auth:
            args += ['--digest', '--user', 'root:' + password]
        args += [('https://localhost:8443' if tls else 'http://127.0.0.1:8080') + path]
        result = subprocess.run(args, capture_output=True, timeout=10, check=True)
        return int(result.stdout), headers.read_bytes(), body.read_bytes()

    def test_both_streams_authenticate_and_relay_exact_large_image(self):
        for stream, path in enumerate(('/onvif/image.cgi', '/onvif/image1.cgi')):
            status, headers, body = self.fetch(path)
            self.assertEqual(status, 200)
            self.assertIn(b'WWW-Authenticate: Digest', headers)
            self.assertEqual(body, IMAGE)
            self.assertEqual(Backend.calls[-1],
                             (f'/api/v1/actions/snapshot?stream_id={stream}', 'Bearer ' + TOKEN))
        self.assertEqual(len(Backend.calls), 2)

    def test_missing_and_wrong_credentials_never_reach_control(self):
        self.assertEqual(self.fetch(auth=False)[0], 401)
        self.assertEqual(self.fetch(password='wrong')[0], 401)
        self.assertEqual(Backend.calls, [])

    def test_plain_http_cannot_fetch_snapshot(self):
        self.assertNotEqual(self.fetch(tls=False)[0], 200)
        self.assertEqual(Backend.calls, [])

    def raw_https(self, request):
        context = ssl.create_default_context(cafile=str(self.cert))
        with socket.create_connection(('127.0.0.1', 8443), timeout=5) as plain:
            with context.wrap_socket(plain, server_hostname='localhost') as client:
                client.sendall(request)
                response = bytearray()
                while chunk := client.recv(8192):
                    response.extend(chunk)
        return bytes(response)

    def test_proxy_marker_cannot_replace_digest(self):
        response = self.raw_https(
            b'GET /onvif/image.cgi HTTP/1.1\r\nHost: localhost\r\n'
            b'X-Thingino-Proxy: 2\r\nConnection: close\r\n\r\n')
        self.assertTrue(response.startswith(b'HTTP/1.1 401 '), response[:100])
        self.assertEqual(Backend.calls, [])

    def test_duplicate_authorization_and_query_fail_closed(self):
        response = self.raw_https(
            b'GET /onvif/image.cgi HTTP/1.1\r\nHost: localhost\r\n'
            b'Authorization: Digest first\r\nAuthorization: Digest second\r\n'
            b'Connection: close\r\n\r\n')
        self.assertTrue(response.startswith(b'HTTP/1.1 400 '), response[:100])
        self.assertNotEqual(self.fetch('/onvif/image.cgi?token=fixture')[0], 200)
        self.assertEqual(Backend.calls, [])

    def test_current_credentials_are_reloaded(self):
        self.assertEqual(self.fetch()[0], 200)
        self.write_config('rotated-fixture-only')
        self.assertEqual(self.fetch()[0], 401)
        self.assertEqual(self.fetch(password='rotated-fixture-only')[0], 200)
        self.assertEqual(len(Backend.calls), 2)

    def test_backend_failure_does_not_become_image_success(self):
        Backend.status = 503
        status, _, body = self.fetch()
        self.assertEqual(status, 502)
        self.assertNotEqual(body, IMAGE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
