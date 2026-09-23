"""Recovery AP session loading, station resolution, and SSH transport."""

from __future__ import annotations


def _private_file(facade: object, path: Path, label: str, limit: int) -> bytes:
    Path = getattr(facade, 'Path')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    os = getattr(facade, 'os')
    stat = getattr(facade, 'stat')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RecoveryApHostError(f"cannot read private {label}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > limit
            or metadata.st_mode & 0o077
        ):
            raise RecoveryApHostError(f"private {label} violates its file policy")
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise RecoveryApHostError(f"private {label} changed while being read")
    return raw

def load_host_session(facade: object, session_dir: Path) -> RecoveryApHostSession:
    Path = getattr(facade, 'Path')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    RecoveryApHostSession = getattr(facade, 'RecoveryApHostSession')
    _private_file = getattr(facade, '_private_file')
    json = getattr(facade, 'json')
    re = getattr(facade, 're')
    if session_dir.is_symlink() or not session_dir.is_dir():
        raise RecoveryApHostError("recovery-AP session directory is invalid")
    identity = session_dir / "host/identity"
    known_hosts = session_dir / "host/known_hosts"
    manifest_path = session_dir / "host/session.json"
    _private_file(identity, "identity", 16 * 1024)
    known = _private_file(known_hosts, "known_hosts pin", 2048)
    if (
        len(known.splitlines()) != 1
        or not known.startswith(b"192.168.88.1 ssh-ed25519 ")
    ):
        raise RecoveryApHostError("recovery-AP known_hosts pin is invalid")
    manifest_raw = _private_file(manifest_path, "session manifest", 4096)
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryApHostError("recovery-AP session manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise RecoveryApHostError("recovery-AP session manifest is invalid")
    setup_ssid = manifest.get("setup_ssid")
    if not isinstance(setup_ssid, str) or re.fullmatch(
        r"DCS6100-[0-9a-f]{8}", setup_ssid
    ) is None:
        raise RecoveryApHostError("recovery-AP setup identity is invalid")
    station_mdns_name = f"{setup_ssid.lower()}.local"
    recorded_name = manifest.get("station_mdns_name", station_mdns_name)
    if recorded_name != station_mdns_name:
        raise RecoveryApHostError("recovery-AP station mDNS identity is invalid")
    session_kind = manifest.get("session_kind", "recovery-ap")
    camera_identity_sha256 = manifest.get("camera_identity_sha256")
    transport_enabled = manifest.get("transport_enabled", True)
    if session_kind == "recovery-ap":
        if camera_identity_sha256 is not None or transport_enabled is not True:
            raise RecoveryApHostError("recovery-AP session kind is invalid")
    elif session_kind == "uartless-functional-provisioning":
        if (
            not isinstance(camera_identity_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", camera_identity_sha256) is None
            or transport_enabled is not False
        ):
            raise RecoveryApHostError("UARTless provisioning session binding is invalid")
    else:
        raise RecoveryApHostError("recovery session kind is invalid")
    station_known_hosts: Path | None = None
    if session_kind == "uartless-functional-provisioning":
        candidate = session_dir / "host/station_known_hosts"
        if candidate.exists() or candidate.is_symlink():
            station_pin = _private_file(candidate, "station known-host binding", 4096)
            expected_prefix = recorded_name.encode("ascii") + b" ssh-ed25519 "
            if len(station_pin.splitlines()) != 1 or not station_pin.startswith(
                expected_prefix
            ):
                raise RecoveryApHostError("station known-host binding is invalid")
            station_known_hosts = candidate
    return RecoveryApHostSession(
        identity=identity,
        known_hosts=known_hosts,
        station_mdns_name=station_mdns_name,
        session_kind=session_kind,
        camera_identity_sha256=camera_identity_sha256,
        transport_enabled=transport_enabled,
        station_known_hosts=station_known_hosts,
    )

def load_service_credential(facade: object, session_dir: Path) -> bytes:
    Path = getattr(facade, 'Path')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    _private_file = getattr(facade, '_private_file')
    load_host_session = getattr(facade, 'load_host_session')
    re = getattr(facade, 're')
    """Read the session-bound management credential without exposing it in argv."""

    load_host_session(session_dir)
    raw = _private_file(
        session_dir / "host/service.credential",
        "service credential",
        128,
    )
    if re.fullmatch(rb"[0-9a-f]{64}\n", raw) is None:
        raise RecoveryApHostError("recovery-AP service credential is invalid")
    return raw[:-1]

def _host(facade: object, value: str) -> str:
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    ipaddress = getattr(facade, 'ipaddress')
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise RecoveryApHostError("camera host must be one literal IP address") from exc
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_multicast
        or address.is_unspecified
    ):
        raise RecoveryApHostError("camera host must be one private unicast IPv4 address")
    return str(address)

def resolve_recovery_ap_station_candidates(facade: object,
    session_dir: Path,
    *,
    allow_uartless_station: bool = False,
) -> tuple[str, tuple[str, ...]]:
    Path = getattr(facade, 'Path')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    _host = getattr(facade, '_host')
    load_host_session = getattr(facade, 'load_host_session')
    queue = getattr(facade, 'queue')
    socket = getattr(facade, 'socket')
    threading = getattr(facade, 'threading')
    """Resolve all private IPv4 candidates for this session's .local name."""
    session = load_host_session(session_dir)
    if not session.transport_enabled and not (
        allow_uartless_station
        and session.session_kind == "uartless-functional-provisioning"
    ):
        raise RecoveryApHostError("UARTless provisioning session has no recovery-AP transport")
    outcome: queue.Queue[object] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            outcome.put(
                socket.getaddrinfo(
                    session.station_mdns_name,
                    22,
                    family=socket.AF_INET,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            )
        except OSError as exc:
            outcome.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        resolved = outcome.get(timeout=10.0)
    except queue.Empty as exc:
        raise RecoveryApHostError("camera station mDNS resolution timed out") from exc
    if isinstance(resolved, OSError):
        raise RecoveryApHostError("camera station mDNS name did not resolve") from resolved
    answers = resolved
    addresses: set[str] = set()
    for answer in answers:
        if len(answer) != 5 or not isinstance(answer[4], tuple) or not answer[4]:
            raise RecoveryApHostError("camera station mDNS answer is invalid")
        addresses.add(_host(str(answer[4][0])))
    if not addresses:
        raise RecoveryApHostError(
            "camera station mDNS name did not resolve to a private IPv4"
        )
    return session.station_mdns_name, tuple(sorted(addresses))

def resolve_recovery_ap_station(
    facade: object,
    session_dir: Path,
    *,
    allow_uartless_station: bool = False,
) -> tuple[str, str]:
    Path = getattr(facade, 'Path')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    resolve_recovery_ap_station_candidates = getattr(facade, 'resolve_recovery_ap_station_candidates')
    """Resolve only an unambiguous private session .local identity."""

    mdns_name, addresses = resolve_recovery_ap_station_candidates(
        session_dir,
        allow_uartless_station=allow_uartless_station,
    )
    if len(addresses) != 1:
        raise RecoveryApHostError(
            "camera station mDNS name did not resolve to exactly one private IPv4"
        )
    return mdns_name, addresses[0]

def ssh_arguments(facade: object,
    session: RecoveryApHostSession,
    *,
    host: str,
    command: str,
    allow_uartless_station_health: bool = False,
    allow_uartless_raptor_runtime: bool = False,
    allow_uartless_post_install_readback: bool = False,
    allow_uartless_tls_identity: bool = False,
) -> list[str]:
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    RecoveryApHostSession = getattr(facade, 'RecoveryApHostSession')
    _host = getattr(facade, '_host')
    re = getattr(facade, 're')
    host = _host(host)
    uartless_station_health = (
        allow_uartless_station_health
        and session.session_kind == "uartless-functional-provisioning"
        and command in {
            "thingino-health; sha256sum /dev/mtd3",
            "dlink-application-verify",
            "dlink-runtime-snapshot",
        }
    )
    from ..raptor_runtime_protocol import is_runtime_command
    from ..post_install_readback import POST_INSTALL_READBACK_COMMAND
    TLS_IDENTITY_COMMAND = getattr(facade, 'UARTLESS_TLS_IDENTITY_COMMAND')
    uartless_raptor_runtime = (
        allow_uartless_raptor_runtime
        and session.session_kind == "uartless-functional-provisioning"
        and is_runtime_command(command)
    )
    uartless_readback = (
        allow_uartless_post_install_readback
        and session.session_kind == "uartless-functional-provisioning"
        and command == POST_INSTALL_READBACK_COMMAND
    )
    uartless_tls_identity = (
        allow_uartless_tls_identity
        and session.session_kind == "uartless-functional-provisioning"
        and not session.transport_enabled
        and command == TLS_IDENTITY_COMMAND
    )
    uartless_station = (
        uartless_station_health
        or uartless_raptor_runtime
        or uartless_readback
        or uartless_tls_identity
    )
    if (not session.transport_enabled
            or session.session_kind == "uartless-functional-provisioning") and not uartless_station:
        raise RecoveryApHostError("UARTless provisioning session has no recovery-AP transport")
    if uartless_station and (
        session.station_known_hosts is None
        or not session.station_known_hosts.is_file()
        or session.station_known_hosts.is_symlink()
        or not session.station_mdns_name
    ):
        raise RecoveryApHostError("UARTless station host-key pin is missing")
    fixed = command in {
        "status",
        "inspect-nor",
        "provision",
        "reboot",
        "vendor-export",
        "thingino-health; sha256sum /dev/mtd3",
        "dlink-media-verify",
        "dlink-application-verify",
        "dlink-runtime-snapshot",
        POST_INSTALL_READBACK_COMMAND,
    } or uartless_tls_identity
    transfer = re.fullmatch(
        r"(?:receive [1-9][0-9]{0,6} [0-9a-f]{64}|send [0-9a-f]{64}|install-recovery [0-9a-f]{64}|install-mtd3 [0-9a-f]{64}|activate-mtd3 [0-9a-f]{64})",
        command,
    )
    if not fixed and transfer is None and not is_runtime_command(command):
        raise RecoveryApHostError("recovery-AP command is outside the fixed protocol")
    return [
        "ssh",
        "-T",
        "-i",
        str(session.identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        f"UserKnownHostsFile={session.station_known_hosts if uartless_station else session.known_hosts}",
        "-o",
        f"HostKeyAlias={session.station_mdns_name if uartless_station else '192.168.88.1'}",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ConnectTimeout=10",
        f"root@{host}",
        command,
    ]

def _runtime_stderr_category(raw: bytes) -> str:
    """Classify bounded SSH diagnostics; never return remote-controlled text."""
    sample = raw[:4096].lower()
    for marker, category in (
        (b"string too long", "ssh-string-too-long"),
        (b"command too long", "command-too-long"),
        (b"exec request failed", "exec-request-failed"),
        (b"administratively prohibited", "channel-prohibited"),
        (b"host key verification failed", "host-key-rejected"),
        (b"remote host identification has changed", "host-key-changed"),
        (b"permission denied", "authentication-rejected"),
        (b"received disconnect", "peer-disconnected"),
        (b"connection reset", "connection-reset"),
        (b"connection closed", "connection-closed"),
        (b"connection timed out", "connection-timeout"),
        (b"raptor_error=quarantine-rejected", "receiver-quarantine-rejected"),
    ):
        if marker in sample:
            return category
    return "other" if raw else "empty"


def _bounded_tls_identity_exchange(
    facade: object,
    arguments: list[str],
    *,
    timeout: float,
    limit: int,
) -> tuple[int, bytes, bytes]:
    """Capture the fixed certificate command on POSIX without unbounded buffers."""

    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    os = getattr(facade, 'os')
    subprocess = getattr(facade, 'subprocess')
    time = getattr(facade, 'time')
    import selectors
    import signal

    def stop_and_reap(process: object, *, terminate_group: bool) -> None:
        if terminate_group:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            grace_deadline = time.monotonic() + 0.5
            while time.monotonic() < grace_deadline:
                process.poll()
                try:
                    os.killpg(process.pid, 0)
                except (ProcessLookupError, PermissionError):
                    break
                time.sleep(0.01)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            return
        if process.poll() is not None:
            process.wait()
            return
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    deadline = time.monotonic() + timeout
    selector = None
    streams = {}
    terminate_group = True
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise RecoveryApHostError("authenticated recovery-AP exchange failed") from exc
    try:
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        streams = {"stdout": process.stdout, "stderr": process.stderr}
        selector = selectors.DefaultSelector()
        for label, stream in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise RecoveryApHostError("authenticated recovery-AP exchange failed")
            ready = selector.select(remaining_time)
            if not ready:
                raise RecoveryApHostError("authenticated recovery-AP exchange failed")
            for key, _ in ready:
                label = key.data
                remaining_bytes = limit - len(captured[label])
                try:
                    chunk = os.read(key.fd, min(4096, remaining_bytes + 1))
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise RecoveryApHostError(
                        "authenticated recovery-AP exchange failed"
                    ) from exc
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(chunk) > remaining_bytes:
                    captured[label].extend(chunk[:remaining_bytes])
                    raise RecoveryApHostError(
                        "authenticated recovery-AP exchange was rejected"
                    )
                captured[label].extend(chunk)

        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise RecoveryApHostError("authenticated recovery-AP exchange failed")
        try:
            returncode = process.wait(timeout=remaining_time)
        except subprocess.TimeoutExpired:
            raise RecoveryApHostError(
                "authenticated recovery-AP exchange failed"
            ) from None
        terminate_group = returncode != 0
        return returncode, bytes(captured["stdout"]), bytes(captured["stderr"])
    finally:
        stop_and_reap(process, terminate_group=terminate_group)
        if selector is not None:
            selector.close()
        for stream in streams.values():
            stream.close()


def _exchange(facade: object,
    session: RecoveryApHostSession,
    *,
    host: str,
    command: str,
    payload: bytes = b"",
    timeout: float = 30.0,
    allow_uartless_station_health: bool = False,
    allow_uartless_raptor_runtime: bool = False,
    allow_uartless_post_install_readback: bool = False,
    allow_uartless_tls_identity: bool = False,
) -> bytes:
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    RecoveryApHostSession = getattr(facade, 'RecoveryApHostSession')
    os = getattr(facade, 'os')
    ssh_arguments = getattr(facade, 'ssh_arguments')
    subprocess = getattr(facade, 'subprocess')
    from ..raptor_runtime_protocol import is_runtime_command
    TLS_IDENTITY_COMMAND = getattr(facade, 'UARTLESS_TLS_IDENTITY_COMMAND')
    TLS_IDENTITY_MAX_BYTES = getattr(facade, 'UARTLESS_TLS_IDENTITY_MAX_BYTES')
    import shlex

    runtime_label = None
    if is_runtime_command(command):
        parts = shlex.split(command)
        runtime_label = f"Raptor runtime operation={parts[4]}"
        if parts[4] == "receive":
            runtime_label += f" member={parts[6]}"
    tls_identity = allow_uartless_tls_identity and command == TLS_IDENTITY_COMMAND
    if tls_identity and os.name != "posix":
        raise RecoveryApHostError(
            "UARTless TLS identity capture requires a POSIX host"
        )
    arguments = ssh_arguments(
        session,
        host=host,
        command=command,
        allow_uartless_station_health=allow_uartless_station_health,
        allow_uartless_raptor_runtime=allow_uartless_raptor_runtime,
        allow_uartless_post_install_readback=allow_uartless_post_install_readback,
        allow_uartless_tls_identity=allow_uartless_tls_identity,
    )
    if tls_identity:
        if payload:
            raise RecoveryApHostError("authenticated recovery-AP exchange was rejected")
        returncode, stdout, _stderr = _bounded_tls_identity_exchange(
            facade,
            arguments,
            timeout=timeout,
            limit=TLS_IDENTITY_MAX_BYTES,
        )
        if returncode:
            raise RecoveryApHostError("authenticated recovery-AP exchange was rejected")
        return stdout
    try:
        result = subprocess.run(
            arguments,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if runtime_label is not None:
            reason = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "transport-error"
            raise RecoveryApHostError(f"{runtime_label} {reason}") from None
        raise RecoveryApHostError("authenticated recovery-AP exchange failed") from exc
    stdout_limit = 16 * 1024 * 1024
    if result.returncode or len(result.stdout) > stdout_limit:
        if runtime_label is not None:
            raise RecoveryApHostError(
                f"{runtime_label} rejected ssh_returncode={result.returncode}"
                f" command_bytes={len(command.encode('utf-8'))}"
                f" stderr_bytes={len(result.stderr)}"
                f" ssh_diagnostic={_runtime_stderr_category(result.stderr)}"
            )
        raise RecoveryApHostError("authenticated recovery-AP exchange was rejected")
    return result.stdout
