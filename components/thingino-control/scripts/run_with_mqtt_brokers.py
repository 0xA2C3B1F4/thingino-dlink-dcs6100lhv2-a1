#!/usr/bin/env python3
"""Run a command against disposable anonymous, authenticated, and TLS brokers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time


def run_quiet(command: list[str], *, stdin: bytes | None = None) -> None:
    subprocess.run(
        command,
        input=stdin,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("local MQTT broker exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("local MQTT broker did not start")


def certificates(root: Path, openssl: str) -> tuple[Path, Path, Path, Path]:
    ca_key = root / "ca.key"
    ca_cert = root / "ca.crt"
    server_key = root / "server.key"
    server_csr = root / "server.csr"
    server_cert = root / "server.crt"
    extension = root / "server.ext"
    invalid_key = root / "invalid-ca.key"
    invalid_ca = root / "invalid-ca.crt"
    extension.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n", encoding="utf-8")
    run_quiet([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Thingino HA test CA", "-keyout", str(ca_key), "-out", str(ca_cert)])
    run_quiet([openssl, "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost", "-keyout", str(server_key), "-out", str(server_csr)])
    run_quiet([openssl, "x509", "-req", "-days", "1", "-in", str(server_csr), "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial", "-extfile", str(extension), "-out", str(server_cert)])
    run_quiet([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Wrong HA test CA", "-keyout", str(invalid_key), "-out", str(invalid_ca)])
    return ca_cert, invalid_ca, server_cert, server_key


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    temporary_root = os.environ.get("TMPDIR")
    if not temporary_root:
        raise RuntimeError("TMPDIR is required by repository policy")
    mosquitto = shutil.which("mosquitto")
    mosquitto_passwd = shutil.which("mosquitto_passwd")
    openssl = shutil.which("openssl")
    if not mosquitto or not mosquitto_passwd or not openssl:
        raise RuntimeError("mosquitto, mosquitto_passwd, and openssl are required")

    with tempfile.TemporaryDirectory(prefix="thingino-ha-mqtt-", dir=temporary_root) as directory:
        root = Path(directory)
        plain_port, auth_port, tls_port, absent_port = (free_port() for _ in range(4))
        username = f"ha-test-{secrets.token_hex(6)}"
        password = secrets.token_urlsafe(24)
        password_file = root / "passwords"
        # These are random disposable fixture credentials, never camera secrets.
        # Create the hashed file directly: -U uses temporary storage outside
        # the fixture on some Mosquitto versions, ignoring TMPDIR.
        run_quiet([mosquitto_passwd, "-b", "-c", str(password_file), username, password])
        ca_cert, invalid_ca, server_cert, server_key = certificates(root, openssl)
        broker_log = root / "broker.log"
        broker_config = root / "mosquitto.conf"
        restart_config = root / "restart.conf"
        broker_config.write_text(
            "\n".join(
                [
                    "per_listener_settings true",
                    "persistence false",
                    f"log_dest file {broker_log}",
                    f"listener {plain_port} 127.0.0.1",
                    "allow_anonymous true",
                    f"listener {auth_port} 127.0.0.1",
                    "allow_anonymous false",
                    f"password_file {password_file}",
                    f"listener {tls_port} 127.0.0.1",
                    "allow_anonymous true",
                    f"cafile {ca_cert}",
                    f"certfile {server_cert}",
                    f"keyfile {server_key}",
                    "tls_version tlsv1.2",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        restart_config.write_text(
            "\n".join(
                [
                    "persistence false",
                    "log_type none",
                    f"listener {absent_port} 127.0.0.1",
                    "allow_anonymous true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        broker = subprocess.Popen(
            [mosquitto, "-c", str(broker_config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for port in (plain_port, auth_port, tls_port):
                wait_for_port(port, broker)
            environment = os.environ.copy()
            environment.update(
                {
                    "THINGINO_HA_TEST_BROKER_PORT": str(plain_port),
                    "THINGINO_HA_TEST_AUTH_PORT": str(auth_port),
                    "THINGINO_HA_TEST_TLS_PORT": str(tls_port),
                    "THINGINO_HA_TEST_ABSENT_PORT": str(absent_port),
                    "THINGINO_HA_TEST_USERNAME": username,
                    "THINGINO_HA_TEST_PASSWORD": password,
                    "THINGINO_HA_TEST_CA": str(ca_cert),
                    "THINGINO_HA_TEST_INVALID_CA": str(invalid_ca),
                    "THINGINO_HA_TEST_MOSQUITTO": mosquitto,
                    "THINGINO_HA_TEST_RESTART_CONFIG": str(restart_config),
                }
            )
            if password in " ".join(command):
                raise RuntimeError("test credential appeared in child argv")
            completed = subprocess.run(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output = completed.stdout
            broker_output = broker_log.read_bytes() if broker_log.is_file() else b""
            encoded_password = password.encode("utf-8")
            if encoded_password in output or encoded_password in broker_output:
                raise RuntimeError("test credential appeared in captured logs")
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()
            return completed.returncode
        finally:
            broker.terminate()
            try:
                broker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                broker.kill()
                broker.wait(timeout=3)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"MQTT host harness failed: {error}", file=sys.stderr)
        raise SystemExit(1)
