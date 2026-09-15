"""Final-root extraction, credentials, networking, and security configuration."""

from __future__ import annotations


def _run(facade: object, arguments: list[str], label: str, *, input_bytes: bytes | None = None) -> bytes:
    FinalRootError = getattr(facade, 'FinalRootError')
    subprocess = getattr(facade, 'subprocess')
    result = subprocess.run(
        arguments,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace")[-1200:]
        raise FinalRootError(f"{label} failed: {detail}")
    return result.stdout

def _write_private(facade: object, path: Path, raw: bytes) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise FinalRootError("private output path changed type")
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o600)

def _materialize_wpa_runtime_policy(facade: object, wpa_config: bytes) -> bytes:
    FinalRootError = getattr(facade, 'FinalRootError')
    """Add fixed wpa_supplicant runtime policy without changing network secrets."""

    try:
        text = wpa_config.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FinalRootError("private WPA configuration is not ASCII") from exc
    required = {
        "ctrl_interface": "/run/wpa_supplicant",
        "update_config": "0",
        "ap_scan": "1",
    }
    present: set[str] = set()
    for key, expected in required.items():
        values = [
            line.strip().partition("=")[2]
            for line in text.splitlines()
            if line.strip().startswith(f"{key}=")
        ]
        if len(values) > 1:
            raise FinalRootError(f"private WPA configuration repeats {key}")
        if values and values[0] != expected:
            raise FinalRootError(
                f"private WPA configuration has conflicting {key}"
            )
        if values:
            present.add(key)

    missing = [
        f"{key}={value}\n".encode("ascii")
        for key, value in required.items()
        if key not in present
    ]
    normalized = wpa_config if wpa_config.endswith(b"\n") else wpa_config + b"\n"
    return b"".join(missing) + normalized

def _validate_built_station_wifi(facade: object,
    *, image: Path, expected: bytes, unsquashfs: Path
) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    _run = getattr(facade, '_run')
    hmac = getattr(facade, 'hmac')
    actual = _run(
        [str(unsquashfs), "-cat", str(image), "etc/wpa_supplicant.conf"],
        "built station Wi-Fi audit",
    )
    if not hmac.compare_digest(actual, expected):
        raise FinalRootError(
            "built station Wi-Fi does not match the bound private input"
        )

def _write_executable(facade: object, path: Path, raw: bytes) -> None:
    Path = getattr(facade, 'Path')
    path.write_bytes(raw)
    path.chmod(0o755)

def _extract_base_root(facade: object, *, unsquashfs: Path, source: Path, destination: Path) -> None:
    Path = getattr(facade, 'Path')
    _run = getattr(facade, '_run')
    os = getattr(facade, 'os')
    # The work directory is private, but the final root must retain the modes
    # recorded in the public SquashFS. A caller's restrictive umask must not
    # silently turn WebUI assets and daemon-readable data into mode 0600.
    previous_umask = os.umask(0)
    try:
        _run(
            [str(unsquashfs), "-d", str(destination), str(source)],
            "base final-root extraction",
        )
    finally:
        os.umask(previous_umask)

def _read_private(facade: object, path: Path, label: str, *, limit: int) -> bytes:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    os = getattr(facade, 'os')
    stat = getattr(facade, 'stat')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise FinalRootError(f"cannot read {label}") from exc
    try:
        first = os.fstat(descriptor)
        if not stat.S_ISREG(first.st_mode) or first.st_size > limit:
            raise FinalRootError(f"{label} is not a bounded regular file")
        raw = os.read(descriptor, limit + 1)
        second = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if first.st_dev != second.st_dev or first.st_ino != second.st_ino or first.st_size != second.st_size:
        raise FinalRootError(f"{label} changed while being read")
    if len(raw) != first.st_size:
        raise FinalRootError(f"{label} read was incomplete")
    return raw

def _set_root_password(facade: object, path: Path, credential: bytes) -> str:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    SHA512_CRYPT_PATTERN = getattr(facade, 'SHA512_CRYPT_PATTERN')
    _run = getattr(facade, '_run')
    _write_private = getattr(facade, '_write_private')
    hashlib = getattr(facade, 'hashlib')
    shutil = getattr(facade, 'shutil')
    lines = path.read_text(encoding="utf-8").splitlines()
    roots = [index for index, line in enumerate(lines) if line.startswith("root:")]
    if roots != [0]:
        raise FinalRootError("base root has an unexpected root account")
    fields = lines[0].split(":")
    if len(fields) != 9:
        raise FinalRootError("base root shadow schema changed")
    password = credential.decode("ascii").strip()
    salt = hashlib.sha256(
        b"dcs6100-web-ui-shadow-salt\0" + password.encode("ascii")
    ).hexdigest()[:16]
    openssl = shutil.which("openssl")
    if openssl is None:
        raise FinalRootError("required host tool is missing: openssl")
    password_hash = _run(
        [openssl, "passwd", "-6", "-salt", salt, "-stdin"],
        "root SHA-512 password hash",
        input_bytes=(password + "\n").encode("ascii"),
    ).decode("ascii").strip()
    if SHA512_CRYPT_PATTERN.fullmatch(password_hash) is None:
        raise FinalRootError("root SHA-512 password hash is invalid")
    fields[1] = password_hash
    lines[0] = ":".join(fields)
    _write_private(path, ("\n".join(lines) + "\n").encode())
    return password_hash

def _replace_exact(facade: object, path: Path, old: str, new: str, label: str) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    try:
        source = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise FinalRootError(f"cannot read {label}") from exc
    if source.count(old) != 1:
        raise FinalRootError(f"{label} changed from its pinned structure")
    path.write_text(source.replace(old, new), encoding="ascii")

def _configure_key_only_ssh(facade: object,
    root: Path,
    authorized_key: bytes,
    dropbear_host_key: bytes,
    credential: bytes,
) -> None:
    DROPBEAR_DEVELOPMENT_ARGUMENTS = getattr(facade, 'DROPBEAR_DEVELOPMENT_ARGUMENTS')
    DROPBEAR_KEY_ONLY_ARGUMENTS = getattr(facade, 'DROPBEAR_KEY_ONLY_ARGUMENTS')
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    _replace_exact = getattr(facade, '_replace_exact')
    _set_root_password = getattr(facade, '_set_root_password')
    _write_private = getattr(facade, '_write_private')
    normalize_ed25519_authorized_key = getattr(facade, 'normalize_ed25519_authorized_key')
    try:
        normalized_key = normalize_ed25519_authorized_key(authorized_key)
    except ValueError as exc:
        raise FinalRootError(str(exc)) from exc
    _set_root_password(root / "etc/shadow", credential)

    first_login = root / "etc/profile.d/chpasswd"
    if first_login.is_symlink():
        raise FinalRootError("first-login password profile changed type")
    if first_login.exists():
        if not first_login.is_file():
            raise FinalRootError("first-login password profile changed type")
        first_login.unlink()

    ssh_root = root / "root/.ssh"
    if ssh_root.is_symlink() or (ssh_root.exists() and not ssh_root.is_dir()):
        raise FinalRootError("root SSH directory changed type")
    ssh_root.mkdir(parents=True, exist_ok=True)
    ssh_root.chmod(0o700)
    _write_private(ssh_root / "authorized_keys", normalized_key)

    if (
        not 64 <= len(dropbear_host_key) <= 4096
        or b"ssh-ed25519" not in dropbear_host_key[:64]
    ):
        raise FinalRootError("session Dropbear Ed25519 host key is invalid")
    _write_private(
        root / "etc/dropbear/dropbear_ed25519_host_key",
        dropbear_host_key,
    )

    dropbear = root / "etc/init.d/S30dropbear"
    source = dropbear.read_text(encoding="ascii")
    development = f'DAEMON_ARGS="{DROPBEAR_DEVELOPMENT_ARGUMENTS}"'
    key_only = f'DAEMON_ARGS="{DROPBEAR_KEY_ONLY_ARGUMENTS}"'
    legacy_key_only = 'DAEMON_ARGS="-s -g -k -K 300"'
    argument_lines = [line for line in source.splitlines() if line.startswith("DAEMON_ARGS=")]
    if argument_lines in ([development], [legacy_key_only]):
        _replace_exact(
            dropbear, argument_lines[0], key_only, "Dropbear init script"
        )
    elif argument_lines != [key_only]:
        raise FinalRootError("Dropbear init script changed from its pinned arguments")
    source = dropbear.read_text(encoding="ascii")
    handoff = (
        "\t# Recovery owns port 22 until the final image begins normal startup.\n"
        "\t# End that session before launching the final key-only SSH daemon.\n"
        "\tkillall dropbear 2>/dev/null || true\n"
        "\trm -f \"$PIDFILE\"\n"
    )
    if handoff not in source:
        _replace_exact(
            dropbear,
            "start() {\n",
            "start() {\n" + handoff,
            "Dropbear recovery handoff",
        )
    dropbear.chmod(0o755)

def _configure_no_default_route(facade: object, root: Path) -> None:
    EMPTY_RESOLVER_POLICY = getattr(facade, 'EMPTY_RESOLVER_POLICY')
    FinalRootError = getattr(facade, 'FinalRootError')
    LOOPBACK_INTERFACE = getattr(facade, 'LOOPBACK_INTERFACE')
    NETWORK_DEFAULT_ROUTE_GUARD = getattr(facade, 'NETWORK_DEFAULT_ROUTE_GUARD')
    NETWORK_INTERFACES = getattr(facade, 'NETWORK_INTERFACES')
    Path = getattr(facade, 'Path')
    UDHCPC_NO_DEFAULT = getattr(facade, 'UDHCPC_NO_DEFAULT')
    WLAN_DHCP_NO_DEFAULT = getattr(facade, 'WLAN_DHCP_NO_DEFAULT')
    _replace_exact = getattr(facade, '_replace_exact')
    _write_executable = getattr(facade, '_write_executable')
    interfaces = root / "etc/network/interfaces.d"
    if not interfaces.is_dir() or interfaces.is_symlink():
        raise FinalRootError("base root lacks the pinned network interface directory")
    allowed = {"eth0", "lo", "wlan0"}
    entries = {path.name for path in interfaces.iterdir()}
    unexpected = entries - allowed
    if unexpected:
        raise FinalRootError(f"base root has an unexpected network profile: {min(unexpected)}")
    for name in entries:
        path = interfaces / name
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(f"network profile changed type: {name}")
    (interfaces / "eth0").unlink(missing_ok=True)
    main_interfaces = root / "etc/network/interfaces"
    if main_interfaces.is_symlink() or not main_interfaces.is_file():
        raise FinalRootError("base root lacks the pinned network include file")
    main_interfaces.write_bytes(NETWORK_INTERFACES)
    main_interfaces.chmod(0o644)
    loopback = interfaces / "lo"
    loopback.write_bytes(LOOPBACK_INTERFACE)
    loopback.chmod(0o644)
    wlan = interfaces / "wlan0"
    wlan.write_bytes(WLAN_DHCP_NO_DEFAULT)
    wlan.chmod(0o644)

    dhcp_script = root / "usr/share/udhcpc/default.script"
    if dhcp_script.is_symlink() or not dhcp_script.is_file():
        raise FinalRootError("base root lacks the pinned DHCP script")
    old_dhcp = dhcp_script.read_bytes()
    if old_dhcp != UDHCPC_NO_DEFAULT and not all(
        marker in old_dhcp
        for marker in (b'case "$1" in', b"renew | bound)", b"route add default")
    ):
        raise FinalRootError("base DHCP script changed from its pinned structure")
    _write_executable(dhcp_script, UDHCPC_NO_DEFAULT)

    network_init = root / "etc/init.d/S40network"
    source = network_init.read_text(encoding="ascii")
    if NETWORK_DEFAULT_ROUTE_GUARD not in source:
        _replace_exact(
            network_init,
            "\tifup -v -a\n",
            NETWORK_DEFAULT_ROUTE_GUARD,
            "network init script",
        )
    network_init.chmod(0o755)

    resolver = root / "etc/default/resolv.conf"
    if resolver.is_symlink() or (resolver.exists() and not resolver.is_file()):
        raise FinalRootError("default resolver policy changed type")
    resolver.parent.mkdir(parents=True, exist_ok=True)
    resolver.write_bytes(EMPTY_RESOLVER_POLICY)
    resolver.chmod(0o644)

def _patch_json(facade: object,
    path: Path, mutator: Callable[[dict[str, object]], None]
) -> None:
    Callable = getattr(facade, 'Callable')
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    _write_private = getattr(facade, '_write_private')
    json = getattr(facade, 'json')
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalRootError(f"invalid base JSON: {path.name}") from exc
    if not isinstance(document, dict):
        raise FinalRootError(f"base JSON is not an object: {path.name}")
    mutator(document)
    _write_private(
        path,
        (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
    )

def _configure_control(facade: object, document: dict[str, object], credential: bytes) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    hashlib = getattr(facade, 'hashlib')
    control = document.get("control")
    if not isinstance(control, dict):
        raise FinalRootError("Thingino lacks Control configuration")
    if control.get("enabled") is not True:
        raise FinalRootError("Thingino Control is not enabled")
    if control.get("listen") not in (None, "", "127.0.0.1", "localhost"):
        raise FinalRootError("Thingino Control listener is not loopback-only")
    port = control.get("port", 1998)
    if isinstance(port, bool) or not isinstance(port, int) or port != 1998:
        raise FinalRootError("Thingino Control port is not the pinned loopback port")
    control["backend"] = "raptor"
    control["listen"] = "127.0.0.1"
    control["port"] = 1998
    control["token"] = hashlib.sha256(
        b"dcs6100-control-token\0" + credential
    ).hexdigest()

def _configure_ha_live_image(facade: object, document: dict[str, object]) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    HA_LIVE_IMAGE_INTERVAL_SECONDS = getattr(facade, 'HA_LIVE_IMAGE_INTERVAL_SECONDS')
    ha = document.get("ha")
    if not isinstance(ha, dict):
        raise FinalRootError("Thingino lacks Home Assistant configuration")
    # MQTT Camera carries complete JPEG images rather than a video stream.
    # Use Control's supported minimum so the entity does not look frozen while
    # keeping JPEG work and broker traffic bounded.
    ha["camera_interval"] = HA_LIVE_IMAGE_INTERVAL_SECONDS

def _remove_unsafe_paths(facade: object, root: Path) -> None:
    FINAL_FORBIDDEN_PATHS = getattr(facade, 'FINAL_FORBIDDEN_PATHS')
    FinalRootError = getattr(facade, 'FinalRootError')
    KERNEL_RELEASE = getattr(facade, 'KERNEL_RELEASE')
    Path = getattr(facade, 'Path')
    shutil = getattr(facade, 'shutil')
    for relative in sorted(FINAL_FORBIDDEN_PATHS):
        path = root / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise FinalRootError(f"unsafe path changed type: {relative}")
    module_root = root / "usr/lib/modules"
    expected = module_root / KERNEL_RELEASE
    if not expected.is_dir():
        raise FinalRootError("base root lacks the exact kernel module directory")
    for child in module_root.iterdir():
        if child.name == KERNEL_RELEASE:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise FinalRootError("base root contains an unknown module entry")
