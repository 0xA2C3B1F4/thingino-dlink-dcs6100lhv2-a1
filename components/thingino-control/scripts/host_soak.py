#!/usr/bin/env python3
"""Run the host-only process, descriptor, memory, timeout, and leak checks."""

from __future__ import annotations

import argparse
from contextlib import closing
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time


TOKEN = "fixture-token-public-poc"
MEDIA = {
    "streams": {
        "ch0": {
            "available": True,
            "enabled": True,
            "snapshot_url": "/api/v1/actions/snapshot?stream_id=0",
        },
        "ch1": {"available": True, "enabled": False, "snapshot_url": None},
    }
}


class BackendState:
    def __init__(self) -> None:
        self.mode = "ok"
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()


def wait_backend_idle(state: BackendState, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with state.lock:
            if state.active == 0:
                return
        time.sleep(0.01)
    raise AssertionError("fixture backend did not become idle")


def begin_backend_phase(state: BackendState, mode: str) -> None:
    wait_backend_idle(state)
    with state.lock:
        state.mode = mode
        state.peak = 0


class FixtureBackend(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fixture-backend"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _handle(self) -> None:
        state: BackendState = self.server.state  # type: ignore[attr-defined]
        with state.lock:
            state.active += 1
            state.peak = max(state.peak, state.active)
            mode = state.mode
        try:
            if mode == "slow":
                time.sleep(0.1)
            if mode == "hang":
                time.sleep(4)
            if mode == "error":
                status = 500
                body = b'{"status":"error"}\n'
            else:
                status = 200
                if self.path == "/api/v1/runtime/media":
                    body = json.dumps(MEDIA, separators=(",", ":")).encode() + b"\n"
                elif self.path == "/api/v1/actions/daynight":
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                else:
                    body = b'{"status":"ok","healthy":true}\n'
        finally:
            with state.lock:
                state.active -= 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = _handle
    do_POST = _handle


class ThreadingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: BackendState) -> None:
        super().__init__(address, FixtureBackend)
        self.state = state


def free_port() -> int:
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request(port: int, path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, bytes, float]:
    started = time.monotonic()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    connection.close()
    return status, payload, time.monotonic() - started


def wait_ready(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("daemon exited before becoming ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("daemon did not listen within five seconds")


def rss_kib(pid: int) -> int:
    output = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True)
    return int(output.strip())


def fd_count(pid: int) -> int:
    if shutil.which("lsof"):
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-Fn"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return sum(line.startswith("n") for line in result.stdout.splitlines())
    proc_fd = Path(f"/proc/{pid}/fd")
    if proc_fd.is_dir():
        return len(list(proc_fd.iterdir()))
    raise RuntimeError("neither lsof nor /proc/PID/fd is available")


def child_pids(pid: int) -> list[int]:
    if shutil.which("pgrep"):
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        return [int(value) for value in result.stdout.split()]
    output = subprocess.check_output(["ps", "ax", "-o", "pid=,ppid="], text=True)
    children = []
    for line in output.splitlines():
        process_id, parent_id = (int(value) for value in line.split())
        if parent_id == pid:
            children.append(process_id)
    return children


def matching_processes() -> set[int]:
    output = subprocess.check_output(["ps", "ax", "-o", "pid=,command="], text=True)
    matches = set()
    for line in output.splitlines():
        if "agent.cgi" in line or re.search(r"(^|[ /])curl(?:\s|$)", line):
            try:
                matches.add(int(line.strip().split(None, 1)[0]))
            except (IndexError, ValueError):
                pass
    return matches


def legacy_request_files() -> set[str]:
    root = Path("/tmp")
    if not root.is_dir():
        return set()
    return {str(path) for pattern in ("agent-cgi-*", "thingino-agent-*") for path in root.glob(pattern)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    if not binary.is_file():
        parser.error(f"binary does not exist: {binary}")
    source_root = Path(__file__).resolve().parents[1] / "src"
    source_text = "\n".join(
        path.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        for path in source_root.rglob("*.rs")
    )
    request_file_source = source_text.replace('"/tmp/sessions"', "")
    for forbidden in ("Command::new", "std::process::Command", "fork(", "exec(", "/tmp/"):
        if forbidden in request_file_source:
            raise AssertionError(f"request-path source contains forbidden operation: {forbidden}")

    state = BackendState()
    backend = ThreadingServer(("127.0.0.1", 0), state)
    backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
    backend_thread.start()
    backend_port = int(backend.server_address[1])
    daemon_port = free_port()
    tmp_before = legacy_request_files()
    processes_before = matching_processes()

    with tempfile.TemporaryDirectory(prefix="thingino-control-soak-") as temp_name:
        temp = Path(temp_name)
        token_file = temp / "fixture-token"
        token_file.write_text(TOKEN + "\n", encoding="ascii")
        log_file = temp / "daemon-output.txt"
        with log_file.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(binary),
                    "--listen",
                    f"127.0.0.1:{daemon_port}",
                    "--backend",
                    f"127.0.0.1:{backend_port}",
                    "--token-file",
                    str(token_file),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            observed_children: set[int] = set()
            monitor_stop = threading.Event()

            def monitor_children() -> None:
                while not monitor_stop.is_set() and process.poll() is None:
                    observed_children.update(child_pids(process.pid))
                    monitor_stop.wait(0.001)

            child_monitor = threading.Thread(target=monitor_children, daemon=True)
            child_monitor.start()
            try:
                wait_ready(process, daemon_port)
                for _ in range(20):
                    status, _, _ = request(daemon_port, "/api/v1/runtime/media")
                    if status != 200:
                        raise AssertionError(f"warm-up request returned {status}")
                baseline_rss = rss_kib(process.pid)
                baseline_fds = fd_count(process.pid)
                rss_samples = []
                for index in range(1000):
                    status, payload, _ = request(daemon_port, "/api/v1/runtime/media")
                    if status != 200 or json.loads(payload) != MEDIA:
                        raise AssertionError(f"media request {index} failed")
                    if index % 100 == 99:
                        rss_samples.append(rss_kib(process.pid))
                final_rss = rss_kib(process.pid)
                final_fds = fd_count(process.pid)

                begin_backend_phase(state, "slow")
                slow_results: list[int] = []
                slow_barrier = threading.Barrier(9)

                def slow_request() -> None:
                    slow_barrier.wait()
                    status, _, _ = request(daemon_port, "/api/v1/runtime/media")
                    slow_results.append(status)

                slow_threads = [threading.Thread(target=slow_request) for _ in range(8)]
                for worker in slow_threads:
                    worker.start()
                slow_barrier.wait()
                for worker in slow_threads:
                    worker.join(timeout=5)
                if slow_results != [200] * 8:
                    raise AssertionError(f"concurrent backend results were {slow_results}")
                wait_backend_idle(state)
                with state.lock:
                    concurrency_peak = state.peak
                if concurrency_peak > 2:
                    raise AssertionError(f"backend concurrency reached {concurrency_peak}")

                begin_backend_phase(state, "hang")
                hang_results: list[tuple[int, float]] = []
                hang_errors: list[str] = []
                hang_barrier = threading.Barrier(9)

                def hung_request() -> None:
                    hang_barrier.wait()
                    try:
                        status, _, seconds = request(daemon_port, "/api/v1/health")
                        hang_results.append((status, seconds))
                    except Exception as error:
                        hang_errors.append(repr(error))

                hang_threads = [threading.Thread(target=hung_request) for _ in range(8)]
                for worker in hang_threads:
                    worker.start()
                hang_barrier.wait()
                for worker in hang_threads:
                    worker.join(timeout=5)
                if len(hang_results) != 8:
                    raise AssertionError(
                        f"not all concurrent hung requests completed: {hang_errors}"
                    )
                if any(status != 504 or seconds > 3.2 for status, seconds in hang_results):
                    raise AssertionError(f"hung backend results were {hang_results}")
                hang_seconds = max(seconds for _, seconds in hang_results)
                wait_backend_idle(state)
                begin_backend_phase(state, "error")
                status, _, _ = request(daemon_port, "/api/v1/health")
                if status != 502:
                    raise AssertionError(f"backend error returned {status}")

                backend.shutdown()
                backend.server_close()
                backend_thread.join(timeout=2)
                status, _, _ = request(daemon_port, "/api/v1/health")
                if status != 502:
                    raise AssertionError(f"connection refusal returned {status}")

                monitor_stop.set()
                child_monitor.join(timeout=1)
                children = child_pids(process.pid)
                if children:
                    raise AssertionError(f"daemon created child processes: {children}")
                if observed_children:
                    raise AssertionError(
                        f"daemon created child processes during the run: {sorted(observed_children)}"
                    )
                if final_fds != baseline_fds:
                    raise AssertionError(
                        f"file descriptors changed from {baseline_fds} to {final_fds}"
                    )
                rss_growth = final_rss - baseline_rss
                if rss_growth > 1024:
                    raise AssertionError(f"RSS grew by {rss_growth} KiB")
                rss_tail = rss_samples[-5:]
                rss_tail_growth = min(rss_tail[-2:]) - max(rss_tail[:2])
                if rss_tail_growth > 128:
                    raise AssertionError(
                        f"RSS kept growing in the final samples: {rss_tail}"
                    )
                running_process_listing = subprocess.check_output(
                    ["ps", "ax", "-o", "command="], text=False
                )
                if TOKEN.encode() in running_process_listing:
                    raise AssertionError("fixture token appeared in the live process list")
            finally:
                monitor_stop.set()
                child_monitor.join(timeout=1)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)

        log_bytes = log_file.read_bytes()
        if TOKEN.encode() in log_bytes:
            raise AssertionError("fixture token appeared in daemon output")
    tmp_after = legacy_request_files()
    processes_after = matching_processes()
    new_tmp_files = sorted(tmp_after - tmp_before)
    new_helper_processes = sorted(processes_after - processes_before)
    if new_tmp_files:
        raise AssertionError(f"request files appeared in /tmp: {new_tmp_files}")
    if new_helper_processes:
        raise AssertionError(f"legacy CGI or curl processes appeared: {new_helper_processes}")

    report = {
        "schema_version": 1,
        "requests": 1000,
        "statuses": {
            "sequential_media": 200,
            "hung_backend": 504,
            "backend_error": 502,
            "connection_refused": 502,
        },
        "hung_backend_seconds": round(hang_seconds, 3),
        "hung_backend_concurrent_requests": 8,
        "backend_peak_operations": concurrency_peak,
        "child_process_poll_interval_ms": 1,
        "daemon_child_processes": 0,
        "file_descriptors": {"baseline": baseline_fds, "final": final_fds},
        "rss_kib": {
            "baseline": baseline_rss,
            "samples": rss_samples,
            "final": final_rss,
            "growth": final_rss - baseline_rss,
            "final_sample_growth": rss_tail_growth,
        },
        "new_legacy_cgi_or_curl_processes": 0,
        "new_tmp_request_files": 0,
        "token_in_process_list_or_logs": False,
        "result": "pass",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"host soak failed: {error}", file=sys.stderr)
        raise
