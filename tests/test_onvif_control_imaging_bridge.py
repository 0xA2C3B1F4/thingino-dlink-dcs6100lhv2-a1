from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/onvif/0001-persistent-httpd-no-request-children.patch"


def _added_file(patch: str, path: str) -> str:
    marker = f"diff --git a/{path} b/{path}\n"
    start = patch.index(marker) + len(marker)
    end = patch.find("\ndiff --git ", start)
    section = patch[start:] if end < 0 else patch[start:end]
    body = []
    in_hunk = False
    for line in section.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
        elif in_hunk and line.startswith("+") and not line.startswith("+++"):
            body.append(line[1:])
    return "\n".join(body) + "\n"


class _ControlServer:
    def __init__(self, responses: list[dict[str, object] | bytes]) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(5)
        self.port = self.listener.getsockname()[1]
        self.responses = responses
        self.requests: list[bytes] = []
        self.error: BaseException | None = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> _ControlServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        finished = self.done.wait(timeout=6)
        self.listener.close()
        self.thread.join(timeout=1)
        if self.thread.is_alive():
            raise TimeoutError("fake Control server did not stop")
        if not finished:
            raise TimeoutError("fake Control server exceeded its socket timeout")
        if self.error:
            raise self.error

    def _serve(self) -> None:
        try:
            for response in self.responses:
                connection, _ = self.listener.accept()
                with connection:
                    connection.settimeout(5)
                    data = b""
                    while b"\r\n\r\n" not in data:
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise EOFError("client closed before sending HTTP headers")
                        data += chunk
                    header, body = data.split(b"\r\n\r\n", 1)
                    length = 0
                    for line in header.split(b"\r\n"):
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":", 1)[1])
                    while len(body) < length:
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise EOFError("client closed before sending the HTTP body")
                        body += chunk
                    self.requests.append(header + b"\r\n\r\n" + body[:length])
                    encoded = (
                        response
                        if isinstance(response, bytes)
                        else json.dumps(response, separators=(",", ":")).encode()
                    )
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
                        + encoded
                    )
        except BaseException as error:  # surfaced in the owning test thread
            self.error = error
        finally:
            self.done.set()


def _state(*, brightness: int = 64, available: bool = True, maximum: int = 255) -> dict[str, object]:
    fields = {}
    for name in ("brightness", "contrast", "saturation", "sharpness"):
        fields[name] = {
            "supported": True,
            "available": available,
            "min": 0,
            "max": maximum,
            "value": brightness,
        }
    return {"code": 200, "result": "success", "message": {"fields": fields}}


class OnvifControlImagingBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = os.environ.get("TMPDIR")
        self.temp = tempfile.TemporaryDirectory(prefix="onvif-control-", dir=tmpdir)
        self.root = Path(self.temp.name)
        patch = PATCH.read_text()
        (self.root / "control_imaging_bridge.c").write_text(
            _added_file(patch, "src/control_imaging_bridge.c")
        )
        (self.root / "control_imaging_bridge.h").write_text(
            _added_file(patch, "src/control_imaging_bridge.h")
        )
        (self.root / "log.h").write_text("#define log_error(...) ((void)0)\n")
        (self.root / "harness.c").write_text(
            """
#include "control_imaging_bridge.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    control_imaging_state_t state;
    if (argc == 2 && !strcmp(argv[1], "get")) {
        if (control_load_imaging_state(&state)) return 2;
        printf("%d %.0f %.0f %.0f\\n", state.brightness.present,
               state.brightness.min, state.brightness.max,
               state.brightness.value);
        return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "set")) {
        control_imaging_command_t command = {argv[2], 0.5f};
        return control_apply_imaging_changes(&command, 1) ? 3 : 0;
    }
    return 64;
}
"""
        )
        self.token = "a" * 64
        self.config = self.root / "thingino.json"
        self.config.write_text(json.dumps({"control": {"token": self.token}}))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fake_control_server_fails_immediately_on_request_eof(self) -> None:
        for iteration in range(100):
            with self.subTest(iteration=iteration):
                with self.assertRaisesRegex(EOFError, "before sending HTTP headers"):
                    with _ControlServer([_state()]) as server:
                        connection = socket.create_connection(
                            ("127.0.0.1", server.port), timeout=2
                        )
                        connection.close()

    def _compile(self, port: int) -> Path:
        binary = self.root / "bridge-test"
        subprocess.run(
            [
                "cc",
                "-std=c99",
                "-D_GNU_SOURCE",
                "-Wall",
                "-Wextra",
                "-Werror",
                f'-DCONTROL_CONFIG_PATH="{self.config}"',
                f"-DCONTROL_PORT={port}",
                f"-I{self.root}",
                str(self.root / "control_imaging_bridge.c"),
                str(self.root / "harness.c"),
                "-o",
                str(binary),
            ],
            check=True,
            timeout=15,
        )
        return binary

    def test_get_uses_authenticated_fixed_loopback_route(self) -> None:
        with _ControlServer([_state()]) as server:
            result = subprocess.run(
                [self._compile(server.port), "get"], capture_output=True, text=True, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1 0 255 64\n")
        request = server.requests[0]
        self.assertTrue(request.startswith(b"GET /api/v1/imaging HTTP/1.1\r\n"))
        self.assertIn(f"Authorization: Bearer {self.token}\r\n".encode(), request)

    def test_set_scales_to_control_range_and_requires_independent_readback(self) -> None:
        with _ControlServer([_state(), _state(brightness=128), _state(brightness=128)]) as server:
            result = subprocess.run(
                [self._compile(server.port), "set", "brightness"], capture_output=True, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(server.requests), 3)
        self.assertTrue(server.requests[1].startswith(b"POST /api/v1/imaging HTTP/1.1\r\n"))
        self.assertTrue(server.requests[1].endswith(b'{"brightness":128}'))
        self.assertTrue(server.requests[2].startswith(b"GET /api/v1/imaging HTTP/1.1\r\n"))

        with _ControlServer([_state(), _state(brightness=128), _state(brightness=127)]) as server:
            mismatch = subprocess.run(
                [self._compile(server.port), "set", "brightness"], capture_output=True, timeout=5
            )
        self.assertNotEqual(mismatch.returncode, 0)

    def test_unavailable_or_invalid_ranges_and_unknown_fields_fail_closed(self) -> None:
        for response in (_state(available=False), _state(maximum=300)):
            with self.subTest(response=response), _ControlServer([response]) as server:
                result = subprocess.run(
                    [self._compile(server.port), "get"], capture_output=True, timeout=5
                )
            if response["message"]["fields"]["brightness"]["available"] is False:
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"0 0 0 0\n")
            else:
                self.assertNotEqual(result.returncode, 0)

        with _ControlServer([_state()]) as server:
            result = subprocess.run(
                [self._compile(server.port), "set", "noise_reduction"], capture_output=True, timeout=5
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(server.requests), 1)

        valid = json.dumps(_state(), separators=(",", ":"))
        malformed = [
            json.dumps({"outer": _state()}, separators=(",", ":")).encode(),
            valid.replace('"supported":true', '"supported":truejunk', 1).encode(),
        ]
        for response in malformed:
            with self.subTest(response=response), _ControlServer([response]) as server:
                result = subprocess.run(
                    [self._compile(server.port), "get"], capture_output=True, timeout=5
                )
            self.assertNotEqual(result.returncode, 0)

    def test_namespace_aware_presence_helper_finds_nested_and_empty_elements(self) -> None:
        patch = PATCH.read_text()
        additions = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        begin = additions.index("/* TESTABLE_ELEMENT_LOOKUP_BEGIN */")
        end = additions.index("/* TESTABLE_ELEMENT_LOOKUP_END */", begin)
        helper = additions[begin:end]
        source = self.root / "element-helper.c"
        source.write_text(
            """
#include <stddef.h>
#include <string.h>
typedef struct node { const char *name; struct node *child; struct node *next; } mxml_node_t;
#define MXML_TYPE_ELEMENT 1
static int mxmlGetType(mxml_node_t *node) { return node ? MXML_TYPE_ELEMENT : 0; }
static const char *mxmlGetElement(mxml_node_t *node) { return node->name; }
static mxml_node_t *mxmlGetFirstChild(mxml_node_t *node) { return node->child; }
static mxml_node_t *mxmlGetNextSibling(mxml_node_t *node) { return node->next; }
"""
            + helper
            + """
int main(void) {
    mxml_node_t level = {"tt:Level", NULL, NULL};
    mxml_node_t nested = {"tt:WideDynamicRange", &level, NULL};
    mxml_node_t empty = {"tt:ImageStabilization", NULL, NULL};
    mxml_node_t root = {"tt:ImagingSettings", &nested, NULL};
    nested.next = &empty;
    if (find_child_element(&root, "WideDynamicRange") != &nested) return 1;
    if (find_child_element(&root, "ImageStabilization") != &empty) return 2;
    if (find_child_element(&root, "NoiseReduction") != NULL) return 3;
    return 0;
}
"""
        )
        binary = self.root / "element-helper"
        subprocess.run(
            ["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)],
            check=True,
            timeout=15,
        )
        subprocess.run([binary], check=True, timeout=5)

    def test_onvif_adapter_rejects_unsupported_setters_and_has_no_prudynt_paths(self) -> None:
        patch = PATCH.read_text()
        additions = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for required in (
            '"NoiseReduction", "BacklightCompensation", "WideDynamicRange"',
            '"ToneCompensation", "Defogging"',
            '"IrCutFilterAutoAdjustment", "ImageStabilization"',
            '"Unsupported imaging parameter"',
            "find_child_element(settings_node, unsupported_tags[i])",
            "control_apply_imaging_changes(commands, command_count)",
        ):
            self.assertIn(required, patch)
        for forbidden in ("/run/prudynt", "signal_prudynt", "prudynt_bridge.o"):
            self.assertNotIn(forbidden, additions)


if __name__ == "__main__":
    unittest.main()
