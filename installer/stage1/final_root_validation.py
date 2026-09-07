"""Stage-1 final-root content and image validation."""

from __future__ import annotations


def validate_final_root_contents(facade: object,
    *,
    paths: set[str],
    shadow: bytes,
    authorized_keys: bytes,
    dropbear_init: bytes,
    network_init: bytes,
    interfaces_config: bytes,
    loopback_config: bytes,
    wlan_config: bytes,
    dhcp_script: bytes,
    resolver_policy: bytes,
    wpa_config: bytes,
    prudynt_config: bytes,
    thingino_config: bytes,
    onvif_config: bytes,
) -> None:
    DROPBEAR_KEY_ONLY_ARGUMENTS = getattr(facade, 'DROPBEAR_KEY_ONLY_ARGUMENTS')
    EMPTY_RESOLVER_POLICY = getattr(facade, 'EMPTY_RESOLVER_POLICY')
    FINAL_FORBIDDEN_PATHS = getattr(facade, 'FINAL_FORBIDDEN_PATHS')
    FINAL_REQUIRED_PATHS = getattr(facade, 'FINAL_REQUIRED_PATHS')
    FINAL_WEBUI_PATHS = getattr(facade, 'FINAL_WEBUI_PATHS')
    LOOPBACK_INTERFACE = getattr(facade, 'LOOPBACK_INTERFACE')
    NETWORK_DEFAULT_ROUTE_GUARD = getattr(facade, 'NETWORK_DEFAULT_ROUTE_GUARD')
    NETWORK_INTERFACES = getattr(facade, 'NETWORK_INTERFACES')
    Stage1BuildError = getattr(facade, 'Stage1BuildError')
    UDHCPC_NO_DEFAULT = getattr(facade, 'UDHCPC_NO_DEFAULT')
    WLAN_DHCP_NO_DEFAULT = getattr(facade, 'WLAN_DHCP_NO_DEFAULT')
    derive_rtsp_viewer_credential = getattr(facade, 'derive_rtsp_viewer_credential')
    json = getattr(facade, 'json')
    normalize_ed25519_authorized_key = getattr(facade, 'normalize_ed25519_authorized_key')
    re = getattr(facade, 're')
    missing = FINAL_REQUIRED_PATHS - paths
    if missing:
        raise Stage1BuildError(f"final root lacks required path: {min(missing)}")
    forbidden = FINAL_FORBIDDEN_PATHS & paths
    if forbidden:
        raise Stage1BuildError(
            f"final root retains a forbidden path: {min(forbidden)}"
        )
    module_prefix = "usr/lib/modules/3.10.14__isvp_swan_1.0__/"
    for path in paths:
        if path == "var/www/x" or path.startswith("var/www/x/"):
            raise Stage1BuildError("final root retains the CGI request tree")
        if path == "var/www/onvif" or path.startswith("var/www/onvif/"):
            raise Stage1BuildError("final root retains the legacy ONVIF request tree")
        if path == "var/www-portal" or path.startswith("var/www-portal/"):
            raise Stage1BuildError("final root retains the CGI captive portal")
        if path.endswith(".cgi"):
            raise Stage1BuildError(f"final root retains CGI code: {path}")
        if "thingino-agent" in path:
            raise Stage1BuildError(f"final root retains the retired Agent name: {path}")
        if path == "usr/libexec/thingino-webui" or path.startswith(
            "usr/libexec/thingino-webui/"
        ):
            raise Stage1BuildError("final root retains a legacy WebUI shell helper")
        if path.startswith(("opt/dlink-media/closure/", "opt/dlink-media/runtime/")):
            raise Stage1BuildError("final root retains a duplicate media runtime")
        if path.startswith("usr/lib/modules/") and not (
            path == "usr/lib/modules/3.10.14__isvp_swan_1.0__"
            or path.startswith(module_prefix)
        ):
            raise Stage1BuildError("final root contains a polluted module path")
    unexpected_webui = {
        path
        for path in paths
        if (path == "var/www" or path.startswith("var/www/"))
        and path not in FINAL_WEBUI_PATHS
    }
    if unexpected_webui:
        raise Stage1BuildError(
            f"final root WebUI is not the exact static closure: {min(unexpected_webui)}"
        )
    root_lines = [line for line in shadow.decode("utf-8").splitlines() if line.startswith("root:")]
    if len(root_lines) != 1 or len(root_lines[0].split(":")) != 9:
        raise Stage1BuildError("final root has an invalid root account")
    password_hash = root_lines[0].split(":", 2)[1]
    if re.fullmatch(r"\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}", password_hash) is None:
        raise Stage1BuildError("final root lacks the per-install SHA-512 Web UI password")
    try:
        if normalize_ed25519_authorized_key(authorized_keys) != authorized_keys:
            raise Stage1BuildError("final SSH authorized key is not normalized")
    except ValueError as exc:
        raise Stage1BuildError(str(exc)) from exc
    try:
        dropbear = dropbear_init.decode("ascii")
        network = network_init.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Stage1BuildError("final SSH or network init is not ASCII") from exc
    expected_dropbear = f'DAEMON_ARGS="{DROPBEAR_KEY_ONLY_ARGUMENTS}"'
    argument_lines = [line for line in dropbear.splitlines() if line.startswith("DAEMON_ARGS=")]
    if argument_lines != [expected_dropbear]:
        raise Stage1BuildError("final Dropbear is not key-only")
    if interfaces_config != NETWORK_INTERFACES or loopback_config != LOOPBACK_INTERFACE:
        raise Stage1BuildError("final network profile include or loopback policy changed")
    if wlan_config != WLAN_DHCP_NO_DEFAULT:
        raise Stage1BuildError("final WLAN profile can create an unreviewed route")
    if dhcp_script != UDHCPC_NO_DEFAULT:
        raise Stage1BuildError("final DHCP policy can consume an unreviewed route")
    if resolver_policy != EMPTY_RESOLVER_POLICY:
        raise Stage1BuildError("final resolver policy is not local-only")
    if network.count(NETWORK_DEFAULT_ROUTE_GUARD) != 1:
        raise Stage1BuildError("final network init lacks the default-route guard")
    if b"ssid=" not in wpa_config or b"psk=" not in wpa_config:
        raise Stage1BuildError("final root lacks host-supplied private Wi-Fi configuration")
    try:
        prudynt = json.loads(prudynt_config)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage1BuildError("final Prudynt configuration is invalid JSON") from exc
    for service in ("http", "rtsp"):
        config = prudynt.get(service, {})
        if config.get("auth_required") is not True:
            raise Stage1BuildError(f"final Prudynt {service} authentication is disabled")
        username = config.get("username")
        password = config.get("password")
        if (
            not isinstance(username, str)
            or not 1 <= len(username) <= 64
            or not isinstance(password, str)
            or not 16 <= len(password) <= 128
        ):
            raise Stage1BuildError(f"final Prudynt {service} credential is invalid")
    rtsp = prudynt.get("rtsp", {})
    if rtsp.get("username") != "viewer":
        raise Stage1BuildError("final RTSP username is not the viewer account")
    for name in ("stream0", "stream1"):
        stream = prudynt.get(name)
        if not isinstance(stream, dict) or stream.get("enabled") is not True:
            raise Stage1BuildError(f"final Prudynt {name} is not enabled")
        fps = stream.get("fps")
        buffers = stream.get("buffers")
        if (
            isinstance(fps, bool)
            or not isinstance(fps, int)
            or not 1 <= fps <= 30
            or isinstance(buffers, bool)
            or not isinstance(buffers, int)
            or not 1 <= buffers <= 65_535
        ):
            raise Stage1BuildError(
                f"final Prudynt {name} has an invalid effective stream state"
            )
    try:
        thingino = json.loads(thingino_config)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage1BuildError("final Thingino configuration is invalid JSON") from exc
    daynight = thingino.get("daynight", {})
    controls = daynight.get("controls", {}) if isinstance(daynight, dict) else {}
    if not isinstance(controls, dict) or controls.get("color") is not True:
        raise Stage1BuildError("final Thingino Day color control is not enabled")
    try:
        onvif = json.loads(onvif_config)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage1BuildError("final ONVIF configuration is invalid JSON") from exc
    server = onvif.get("server", {})
    onvif_username = server.get("username") if isinstance(server, dict) else None
    onvif_password = server.get("password") if isinstance(server, dict) else None
    if (
        onvif_username != "root"
        or not isinstance(onvif_password, str)
        or len(onvif_password) < 16
        or onvif_password == "thingino"
    ):
        raise Stage1BuildError("final ONVIF credential is not unique")
    try:
        expected_rtsp_password = derive_rtsp_viewer_credential(
            (onvif_password + "\n").encode("ascii")
        ).decode("ascii").strip()
    except (UnicodeError, ValueError) as exc:
        raise Stage1BuildError("final management credential is not canonical") from exc
    if rtsp.get("password") != expected_rtsp_password:
        raise Stage1BuildError(
            "final RTSP credential does not match the derived viewer credential"
        )

def validate_final_root(facade: object,
    raw: bytes, *, unsquashfs: Path, temporary_parent: Path
) -> None:
    FINAL_WEBUI_PATHS = getattr(facade, 'FINAL_WEBUI_PATHS')
    PROVEN_IDENTITIES = getattr(facade, 'PROVEN_IDENTITIES')
    PRUDYNT_RING_START = getattr(facade, 'PRUDYNT_RING_START')
    PRUDYNT_START = getattr(facade, 'PRUDYNT_START')
    PRUDYNT_GUARDED_RING_START = getattr(facade, 'PRUDYNT_GUARDED_RING_START')
    PRUDYNT_GUARDED_START = getattr(facade, 'PRUDYNT_GUARDED_START')
    Path = getattr(facade, 'Path')
    SOURCE_BUILT_INIT_IDENTITIES = getattr(facade, 'SOURCE_BUILT_INIT_IDENTITIES')
    SOURCE_BUILT_PRUDYNT_MARKERS = getattr(facade, 'SOURCE_BUILT_PRUDYNT_MARKERS')
    SOURCE_BUILT_SUPPORT_IDENTITIES = getattr(facade, 'SOURCE_BUILT_SUPPORT_IDENTITIES')
    MATCHED_PUBLIC_MEDIA_RUNTIME = getattr(facade, 'MATCHED_PUBLIC_MEDIA_RUNTIME')
    MATCHED_STOCK_MEDIA_IDENTITIES = getattr(facade, 'MATCHED_STOCK_MEDIA_IDENTITIES')
    SOURCE_NATIVE_MEDIA_PATHS = getattr(facade, 'SOURCE_NATIVE_MEDIA_PATHS')
    SOURCE_NATIVE_MEDIA_RUNTIME = getattr(facade, 'SOURCE_NATIVE_MEDIA_RUNTIME')
    STOCK_VENDOR_IDENTITIES = getattr(facade, 'STOCK_VENDOR_IDENTITIES')
    Stage1BuildError = getattr(facade, 'Stage1BuildError')
    _listing_modes = getattr(facade, '_listing_modes')
    _listing_paths = getattr(facade, '_listing_paths')
    _run = getattr(facade, '_run')
    _valid_raptor_rwd_provenance = getattr(facade, '_valid_raptor_rwd_provenance')
    hashlib = getattr(facade, 'hashlib')
    json = getattr(facade, 'json')
    tempfile = getattr(facade, 'tempfile')
    validate_final_root_contents = getattr(facade, 'validate_final_root_contents')
    with tempfile.TemporaryDirectory(
        prefix="dcs6100-final-root-audit-", dir=temporary_parent
    ) as name:
        image = Path(name) / "system.squashfs"
        image.write_bytes(raw)
        listing = _run(
            [str(unsquashfs), "-lln", str(image)], "final root inventory"
        ).decode("utf-8", "replace")
        modes = _listing_modes(listing)
        for path in ("var/www", "var/www/assets"):
            if modes.get(path) != "drwxr-xr-x":
                raise Stage1BuildError(f"final WebUI directory mode changed: {path}")
        for path in FINAL_WEBUI_PATHS - {"var/www", "var/www/assets"}:
            if modes.get(path) != "-rw-r--r--":
                raise Stage1BuildError(f"final WebUI file mode changed: {path}")
        for path in (
            "etc/thingino-api.key",
            "etc/wpa_supplicant.conf",
            "root/.ssh/authorized_keys",
        ):
            if modes.get(path) != "-rw-------":
                raise Stage1BuildError(f"final private file mode changed: {path}")

        def extract(path: str) -> bytes:
            return _run(
                [str(unsquashfs), "-cat", str(image), path],
                f"final root extraction of {path}",
            )

        legacy_exact_media = {
            "usr/lib/ld.so.1": "lib/ld.so.1",
            "usr/lib/libimp.so": "lib/libimp.so",
            "usr/lib/libalog.so": "lib/libalog.so",
            "usr/lib/libsysutils.so": "lib/libsysutils.so",
            "usr/lib/libaudioProcess.so": "lib/libaudioProcess.so",
            "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/tx-isp-t31.ko": "modules/tx-isp-t31.ko",
            "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/sensor_os02g10_t31.ko": "modules/sensor_os02g10_t31.ko",
            "usr/share/sensor/os02g10-t31.bin": "sensor/os02g10-t31.bin",
        }
        prudynt_config = extract("etc/prudynt.json")
        try:
            provenance = json.loads(extract("etc/dlink-media-closure.private.json"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Stage1BuildError("final media provenance is invalid JSON") from exc
        raptor_rwd = _valid_raptor_rwd_provenance(provenance.get("raptor_rwd"))
        source_native = (
            provenance.get("runtime") == SOURCE_NATIVE_MEDIA_RUNTIME
            or provenance.get("media_base_runtime") == SOURCE_NATIVE_MEDIA_RUNTIME
        )
        source_matched = (
            provenance.get("schema_version") == 4
            and provenance.get("runtime") == MATCHED_PUBLIC_MEDIA_RUNTIME
            and isinstance(provenance.get("source_built_support"), list)
        )
        if source_native or source_matched:
            entries = provenance.get("files")
            if not isinstance(entries, list):
                raise Stage1BuildError("matched media provenance lacks files")
            by_destination = {
                str(entry.get("destination", "")).removeprefix("/"): entry
                for entry in entries
                if isinstance(entry, dict)
            }
            if source_matched:
                matched_identities = {
                    **MATCHED_STOCK_MEDIA_IDENTITIES,
                    **SOURCE_BUILT_SUPPORT_IDENTITIES,
                }
                required = set(matched_identities)
            else:
                required = set(STOCK_VENDOR_IDENTITIES) | set(SOURCE_NATIVE_MEDIA_PATHS)
            if not required.issubset(by_destination):
                raise Stage1BuildError("matched media provenance is incomplete")
            for final_path in sorted(required):
                raw = extract(final_path)
                digest = hashlib.sha256(raw).hexdigest()
                entry = by_destination[final_path]
                if entry.get("sha256") != digest or entry.get("size") != len(raw):
                    raise Stage1BuildError(
                        f"matched media provenance changed: {final_path}"
                    )
                if source_matched:
                    if digest != matched_identities[final_path]:
                        raise Stage1BuildError(
                            f"matched media identity changed: {final_path}"
                        )
                    expected_origin = (
                        "pinned-public-source-build"
                        if final_path in SOURCE_BUILT_SUPPORT_IDENTITIES
                        else "camera-read-only-stock-mtd3"
                    )
                elif final_path in STOCK_VENDOR_IDENTITIES:
                    if digest != STOCK_VENDOR_IDENTITIES[final_path]:
                        raise Stage1BuildError(
                            f"stock vendor media identity changed: {final_path}"
                        )
                    expected_origin = "camera-read-only-stock-mtd3"
                else:
                    expected_origin = "pinned-source-build"
                if entry.get("origin") != expected_origin:
                    raise Stage1BuildError(
                        f"source-built media origin changed: {final_path}"
                    )
        else:
            for final_path, closure_path in legacy_exact_media.items():
                if (
                    hashlib.sha256(extract(final_path)).hexdigest()
                    != PROVEN_IDENTITIES[closure_path]
                ):
                    raise Stage1BuildError(
                        f"final root changed C1 media identity: {final_path}"
                    )
        source_init_raw = {
            path: extract(path) for path in SOURCE_BUILT_INIT_IDENTITIES
        }
        for path, expected_sha256 in SOURCE_BUILT_INIT_IDENTITIES.items():
            raw_init = source_init_raw[path]
            if path == "etc/init.d/S31prudynt" and raptor_rwd:
                lines = [line.lstrip(b"\t") for line in raw_init.splitlines(keepends=True)]
                if (
                    lines.count(PRUDYNT_RING_START) + lines.count(PRUDYNT_GUARDED_RING_START) != 1
                    or lines.count(PRUDYNT_START) + lines.count(PRUDYNT_GUARDED_START) != 0
                ):
                    raise Stage1BuildError("final WebRTC Prudynt init is not lifecycle-stable")
            elif hashlib.sha256(raw_init).hexdigest() != expected_sha256:
                raise Stage1BuildError(
                    f"final root changed source-built media init: {path}"
                )

        if raptor_rwd:
            for path in ("etc/init.d/S96rwd", "etc/raptor.conf", "usr/bin/rwd"):
                if path not in modes:
                    raise Stage1BuildError(f"final WebRTC root lacks {path}")
            rwd = extract("usr/bin/rwd")
            if any(
                marker in rwd
                for marker in (
                    b"libimp.so",
                    b"raptor_hal",
                    b"libmbedtls.so",
                    b"libmbedx509.so",
                    b"libmbedcrypto.so",
                )
            ):
                raise Stage1BuildError("final WebRTC consumer violates static media isolation")
            rwd_service = extract("etc/init.d/S96rwd")
            if b"S31prudynt stop" in rwd_service or b"start-stop-daemon -K" not in rwd_service:
                raise Stage1BuildError("final WebRTC service teardown changed")

        runtime_hash = hashlib.sha256(prudynt_config).hexdigest()
        prudynt = extract("usr/bin/prudynt")
        if any(marker not in prudynt for marker in SOURCE_BUILT_PRUDYNT_MARKERS):
            raise Stage1BuildError("final root lacks the source-built Prudynt media API")
        prudynt_provenance = provenance.get("prudynt")
        expected_runtime = (
            "source-built-prudynt-with-rss-publisher"
            if raptor_rwd
            else (
                SOURCE_NATIVE_MEDIA_RUNTIME
                if source_native
                else "source-built-prudynt-global-glibc-c1-closure"
            )
        )
        expected_origin = (
            "pinned-source-build-with-rss-publisher"
            if raptor_rwd
            else "pinned-source-build"
        )
        if (
            provenance.get("runtime") != expected_runtime
            or not isinstance(prudynt_provenance, dict)
            or prudynt_provenance.get("origin") != expected_origin
            or prudynt_provenance.get("sha256")
            != hashlib.sha256(prudynt).hexdigest()
            or prudynt_provenance.get("size") != len(prudynt)
        ):
            raise Stage1BuildError("final source-built Prudynt provenance changed")
        if any(
            isinstance(entry, dict) and entry.get("path") == "bin/prudynt"
            for entry in provenance.get("files", [])
        ):
            raise Stage1BuildError("final media closure replaced source-built Prudynt")
        if any(
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and entry["path"].startswith("init/")
            for entry in provenance.get("files", [])
        ):
            raise Stage1BuildError("final media closure replaced source-built init")
        source_init_entries = provenance.get("source_built_init")
        if not isinstance(source_init_entries, list) or len(source_init_entries) != len(
            SOURCE_BUILT_INIT_IDENTITIES
        ):
            raise Stage1BuildError("final source-built init provenance changed")
        source_init_by_destination = {
            entry.get("destination"): entry
            for entry in source_init_entries
            if isinstance(entry, dict)
        }
        for path, raw in source_init_raw.items():
            entry = source_init_by_destination.get("/" + path)
            closure_path = "init/" + Path(path).name
            expected_init_origin = (
                "raptor-rwd-overlay"
                if raptor_rwd and path == "etc/init.d/S31prudynt"
                else "pinned-source-build"
            )
            if source_native or source_matched:
                init_valid = (
                    isinstance(entry, dict)
                    and entry.get("origin") == expected_init_origin
                    and entry.get("path") == closure_path
                    and entry.get("sha256") == hashlib.sha256(raw).hexdigest()
                    and entry.get("size") == len(raw)
                )
            else:
                init_valid = (
                    isinstance(entry, dict)
                    and entry.get("origin") == expected_init_origin
                    and entry.get("path") == closure_path
                    and entry.get("reference_sha256") == PROVEN_IDENTITIES[closure_path]
                    and entry.get("sha256") == hashlib.sha256(raw).hexdigest()
                    and entry.get("size") == len(raw)
                )
            if not init_valid:
                raise Stage1BuildError("final source-built init provenance changed")
        runtime_entries = [
            entry
            for entry in provenance.get("files", [])
            if isinstance(entry, dict) and entry.get("path") == "etc/prudynt.json"
        ]
        if len(runtime_entries) != 1:
            raise Stage1BuildError("final Prudynt configuration file provenance changed")
        runtime_entry = runtime_entries[0]
        source_hash = (
            runtime_entry.get("source_sha256")
            if source_native or source_matched
            else PROVEN_IDENTITIES["etc/prudynt.json"]
        )
        if not isinstance(source_hash, str):
            raise Stage1BuildError("final Prudynt configuration source is invalid")
        if (
            provenance.get("runtime_config_source_sha256") != source_hash
            or provenance.get("runtime_config_sha256") != runtime_hash
        ):
            raise Stage1BuildError("final Prudynt configuration provenance changed")
        if (
            runtime_entry.get("source_sha256") != source_hash
            or runtime_entry.get("sha256") != runtime_hash
            or runtime_entry.get("size") != len(prudynt_config)
        ):
            raise Stage1BuildError("final Prudynt configuration file provenance changed")

        validate_final_root_contents(
            paths=_listing_paths(listing),
            shadow=extract("etc/shadow"),
            authorized_keys=extract("root/.ssh/authorized_keys"),
            dropbear_init=extract("etc/init.d/S30dropbear"),
            network_init=extract("etc/init.d/S40network"),
            interfaces_config=extract("etc/network/interfaces"),
            loopback_config=extract("etc/network/interfaces.d/lo"),
            wlan_config=extract("etc/network/interfaces.d/wlan0"),
            dhcp_script=extract("usr/share/udhcpc/default.script"),
            resolver_policy=extract("etc/default/resolv.conf"),
            wpa_config=extract("etc/wpa_supplicant.conf"),
            prudynt_config=prudynt_config,
            thingino_config=extract("etc/thingino.json"),
            onvif_config=extract("etc/onvif.json"),
        )
