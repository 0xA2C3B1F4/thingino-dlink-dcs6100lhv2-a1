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
    return RecoveryApHostSession(
        identity=identity,
        known_hosts=known_hosts,
        station_mdns_name=station_mdns_name,
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

def resolve_recovery_ap_station(facade: object, session_dir: Path) -> tuple[str, str]:
    Path = getattr(facade, 'Path')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    resolve_recovery_ap_station_candidates = getattr(facade, 'resolve_recovery_ap_station_candidates')
    """Resolve only an unambiguous private session .local identity."""

    mdns_name, addresses = resolve_recovery_ap_station_candidates(session_dir)
    if len(addresses) != 1:
        raise RecoveryApHostError(
            "camera station mDNS name did not resolve to exactly one private IPv4"
        )
    return mdns_name, addresses[0]

def ssh_arguments(facade: object,
    session: RecoveryApHostSession, *, host: str, command: str
) -> list[str]:
    RUNTIME_ACTIVATE_COMMAND = getattr(facade, 'RUNTIME_ACTIVATE_COMMAND')
    RUNTIME_RECEIVE_COMMAND = getattr(facade, 'RUNTIME_RECEIVE_COMMAND')
    RUNTIME_ROLLBACK_COMMAND = getattr(facade, 'RUNTIME_ROLLBACK_COMMAND')
    RUNTIME_STATUS_COMMAND = getattr(facade, 'RUNTIME_STATUS_COMMAND')
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    RecoveryApHostSession = getattr(facade, 'RecoveryApHostSession')
    _host = getattr(facade, '_host')
    re = getattr(facade, 're')
    host = _host(host)
    fixed = command in {
        "status",
        "thingino-failure",
        "inspect-nor",
        "provision",
        "reboot",
        "vendor-export",
        "thingino-health; sha256sum /dev/mtd3",
        "dlink-media-verify",
        "dlink-runtime-snapshot",
        RUNTIME_RECEIVE_COMMAND,
        RUNTIME_ACTIVATE_COMMAND,
        RUNTIME_STATUS_COMMAND,
        RUNTIME_ROLLBACK_COMMAND,
    }
    transfer = re.fullmatch(
        r"(?:receive [1-9][0-9]{0,6} [0-9a-f]{64}|send [0-9a-f]{64}|install-recovery [0-9a-f]{64}|install-mtd3 [0-9a-f]{64}|activate-mtd3 [0-9a-f]{64})",
        command,
    )
    if not fixed and transfer is None:
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
        f"UserKnownHostsFile={session.known_hosts}",
        "-o",
        "HostKeyAlias=192.168.88.1",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ConnectTimeout=10",
        f"root@{host}",
        command,
    ]

def _exchange(facade: object,
    session: RecoveryApHostSession,
    *,
    host: str,
    command: str,
    payload: bytes = b"",
    timeout: float = 30.0,
) -> bytes:
    RecoveryApHostError = getattr(facade, 'RecoveryApHostError')
    RecoveryApHostSession = getattr(facade, 'RecoveryApHostSession')
    ssh_arguments = getattr(facade, 'ssh_arguments')
    subprocess = getattr(facade, 'subprocess')
    try:
        result = subprocess.run(
            ssh_arguments(session, host=host, command=command),
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryApHostError("authenticated recovery-AP exchange failed") from exc
    if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
        raise RecoveryApHostError("authenticated recovery-AP exchange was rejected")
    return result.stdout
