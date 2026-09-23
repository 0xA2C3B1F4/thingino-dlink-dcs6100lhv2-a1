"""Local build Git and Rust toolchain acquisition helpers."""

from __future__ import annotations


def _run(facade: object, arguments: list[str], *, label: str, timeout: int = 1800) -> str:
    LocalBuildAcquireError = getattr(facade, 'LocalBuildAcquireError')
    subprocess = getattr(facade, 'subprocess')
    try:
        completed = subprocess.run(
            arguments,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalBuildAcquireError(f"{label} could not run") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise LocalBuildAcquireError(f"{label} failed{suffix}")
    return completed.stdout.strip()

def _git_environment(facade: object) -> dict[str, str]:
    os = getattr(facade, 'os')
    allowed = {
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        {
            "GIT_ASKPASS": "",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment

def _git(facade: object,
    arguments: list[str],
    *,
    checkout: Path,
    label: str,
    network: bool = False,
    timeout: int = 20,
) -> str:
    LocalBuildAcquireError = getattr(facade, 'LocalBuildAcquireError')
    Path = getattr(facade, 'Path')
    _git_environment = getattr(facade, '_git_environment')
    subprocess = getattr(facade, 'subprocess')
    command = [
        "git",
        "--no-replace-objects",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=",
        "-c",
        "credential.helper=",
    ]
    if network:
        command.extend(
            [
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
            ]
        )
    command.extend(arguments)
    try:
        completed = subprocess.run(
            command,
            cwd=checkout,
            env=_git_environment(),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalBuildAcquireError(f"{label} could not run") from exc
    if completed.returncode != 0:
        raise LocalBuildAcquireError(f"{label} failed")
    return completed.stdout.strip()

def _verify_git_checkout(facade: object,
    *,
    checkout: Path,
    revision: str,
    tree: str,
    canonical_url: str,
) -> dict[str, str]:
    LocalBuildAcquireError = getattr(facade, 'LocalBuildAcquireError')
    Path = getattr(facade, 'Path')
    _directory = getattr(facade, '_directory')
    _git = getattr(facade, '_git')
    source_checkout = getattr(facade, 'source_checkout')
    checkout = _directory(checkout, "cached Ingenic toolchain")
    git_directory = checkout / ".git"
    if git_directory.is_symlink() or not git_directory.is_dir():
        raise LocalBuildAcquireError("Ingenic toolchain Git directory is invalid")
    top = _git(
        ["rev-parse", "--show-toplevel"],
        checkout=checkout,
        label="Ingenic toolchain root check",
    )
    actual_git_directory = _git(
        ["rev-parse", "--git-dir"],
        checkout=checkout,
        label="Ingenic toolchain Git directory check",
    )
    actual_revision = _git(
        ["rev-parse", "HEAD"],
        checkout=checkout,
        label="Ingenic toolchain revision check",
    )
    actual_tree = _git(
        ["rev-parse", "HEAD^{tree}"],
        checkout=checkout,
        label="Ingenic toolchain tree check",
    )
    origin = _git(
        ["remote", "get-url", "origin"],
        checkout=checkout,
        label="Ingenic toolchain origin check",
    )
    dirty = _git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        checkout=checkout,
        label="Ingenic toolchain clean-state check",
    )
    index_flags = _git(
        ["ls-files", "-v"],
        checkout=checkout,
        label="Ingenic toolchain index-flag check",
    )
    if (
        Path(top).resolve() != checkout
        or (checkout / actual_git_directory).resolve() != git_directory.resolve()
        or actual_revision != revision
        or actual_tree != tree
        or source_checkout.canonical_git_url(origin, "Ingenic toolchain origin")
        != canonical_url
        or dirty
        or any(not line.startswith("H ") for line in index_flags.splitlines())
    ):
        raise LocalBuildAcquireError("cached Ingenic toolchain identity changed")
    return {
        "revision": actual_revision,
        "tree": actual_tree,
        "url": canonical_url,
    }

def _git_checkout(facade: object,
    *, destination: Path, source: dict[str, object]
) -> tuple[Path, dict[str, str]]:
    HEX40 = getattr(facade, 'HEX40')
    LocalBuildAcquireError = getattr(facade, 'LocalBuildAcquireError')
    Path = getattr(facade, 'Path')
    _directory = getattr(facade, '_directory')
    _git = getattr(facade, '_git')
    _private_child_directory = getattr(facade, '_private_child_directory')
    _verify_git_checkout = getattr(facade, '_verify_git_checkout')
    os = getattr(facade, 'os')
    shutil = getattr(facade, 'shutil')
    source_checkout = getattr(facade, 'source_checkout')
    tempfile = getattr(facade, 'tempfile')
    url = source.get("url")
    revision = source.get("revision")
    tree = source.get("tree")
    if (
        not isinstance(url, str)
        or not isinstance(revision, str)
        or not isinstance(tree, str)
        or HEX40.fullmatch(revision) is None
        or HEX40.fullmatch(tree) is None
    ):
        raise LocalBuildAcquireError("locked Git toolchain metadata is invalid")
    canonical = source_checkout.canonical_git_url(url, "Ingenic toolchain URL")
    _private_child_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        checkout = _directory(destination, "cached Ingenic toolchain")
    else:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.fetch-", dir=destination.parent)
        )
        try:
            _git(
                ["init", "--quiet"],
                checkout=temporary,
                label="Ingenic toolchain Git initialization",
            )
            _git(
                ["remote", "add", "origin", url],
                checkout=temporary,
                label="Ingenic toolchain remote configuration",
            )
            _git(
                ["fetch", "--depth=1", "--no-tags", "origin", revision],
                checkout=temporary,
                label="Ingenic toolchain fetch",
                network=True,
                timeout=600,
            )
            _git(
                ["checkout", "--detach", "--quiet", revision],
                checkout=temporary,
                label="Ingenic toolchain checkout",
            )
            identity = _verify_git_checkout(
                checkout=temporary,
                revision=revision,
                tree=tree,
                canonical_url=canonical,
            )
            os.replace(temporary, destination)
            checkout = destination
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    identity = _verify_git_checkout(
        checkout=checkout,
        revision=revision,
        tree=tree,
        canonical_url=canonical,
    )
    return checkout, identity

def _install_rust_toolchain(facade: object,
    *,
    cache_root: Path,
    builder_image_id: str,
    rust_distribution: Path,
    rust_distribution_sha256: str,
    rust_source_component: Path,
    rust_source_component_sha256: str,
    identity: str,
) -> tuple[Path, dict[str, object]]:
    EXPECTED_RUST_COMMIT = getattr(facade, 'EXPECTED_RUST_COMMIT')
    EXPECTED_RUST_RELEASE = getattr(facade, 'EXPECTED_RUST_RELEASE')
    HEX64 = getattr(facade, 'HEX64')
    IMAGE_ID = getattr(facade, 'IMAGE_ID')
    LocalBuildAcquireError = getattr(facade, 'LocalBuildAcquireError')
    Path = getattr(facade, 'Path')
    RUST_RECEIPT = getattr(facade, 'RUST_RECEIPT')
    RUST_TOOLCHAIN_SCHEMA_VERSION = getattr(facade, 'RUST_TOOLCHAIN_SCHEMA_VERSION')
    _cache_generation_identity = getattr(facade, '_cache_generation_identity')
    _directory = getattr(facade, '_directory')
    _load_json_object = getattr(facade, '_load_json_object')
    _private_child_directory = getattr(facade, '_private_child_directory')
    _run = getattr(facade, '_run')
    _tree_digest = getattr(facade, '_tree_digest')
    atomic_write = getattr(facade, 'atomic_write')
    json = getattr(facade, 'json')
    os = getattr(facade, 'os')
    shutil = getattr(facade, 'shutil')
    tempfile = getattr(facade, 'tempfile')
    if IMAGE_ID.fullmatch(builder_image_id) is None:
        raise LocalBuildAcquireError("builder image ID is invalid")
    if (
        HEX64.fullmatch(rust_distribution_sha256) is None
        or HEX64.fullmatch(rust_source_component_sha256) is None
        or HEX64.fullmatch(identity) is None
    ):
        raise LocalBuildAcquireError("Rust toolchain input identity is invalid")
    parent = _private_child_directory(cache_root, "toolchains")
    cache_identity = _cache_generation_identity(
        "rust-toolchain-v1",
        identity,
        builder_image_id,
    )
    destination = parent / f"rust-arm64-{cache_identity}"
    receipt_path = destination / RUST_RECEIPT
    receipt_base: dict[str, object] = {
        "builder_image_id": builder_image_id,
        "identity": identity,
        "rust_distribution_sha256": rust_distribution_sha256,
        "rust_source_component_sha256": rust_source_component_sha256,
        "schema_version": RUST_TOOLCHAIN_SCHEMA_VERSION,
    }

    def validate_runtime(toolchain: Path) -> None:
        output = _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
                "--platform",
                "linux/arm64",
                "--entrypoint",
                "/bin/sh",
                "-v",
                f"{toolchain}:/input:ro",
                builder_image_id,
                "-c",
                "/input/bin/rustc --version --verbose; /input/bin/cargo --version",
            ],
            label="Rust toolchain identity check",
        )
        if (
            f"release: {EXPECTED_RUST_RELEASE}" not in output
            or f"commit-hash: {EXPECTED_RUST_COMMIT}" not in output
            or f"cargo {EXPECTED_RUST_RELEASE} " not in output
        ):
            raise LocalBuildAcquireError("installed Rust toolchain identity changed")

    if destination.exists() or destination.is_symlink():
        destination = _directory(destination, "cached Rust toolchain")
        receipt = _load_json_object(receipt_path, "cached Rust toolchain receipt")
        expected_receipt = {
            **receipt_base,
            "tree_sha256": _tree_digest(
                destination,
                excluded_root_names=frozenset({RUST_RECEIPT}),
            ),
        }
        if receipt != expected_receipt:
            raise LocalBuildAcquireError("cached Rust toolchain identity changed")
        validate_runtime(destination)
        return destination, receipt
    temporary = Path(tempfile.mkdtemp(prefix=".rust-toolchain-", dir=parent))
    try:
        command = (
            "set -eu; "
            "/input/rust/install.sh --prefix=/output --disable-ldconfig; "
            "/input/rust-src/install.sh --prefix=/output --disable-ldconfig; "
            "test -x /output/bin/rustc; "
            "test -x /output/bin/cargo; "
            "test -f /output/lib/rustlib/src/rust/library/Cargo.toml"
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
                "--platform",
                "linux/arm64",
                "--entrypoint",
                "/bin/sh",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "-v",
                f"{rust_distribution}:/input/rust:ro",
                "-v",
                f"{rust_source_component}:/input/rust-src:ro",
                "-v",
                f"{temporary}:/output",
                builder_image_id,
                "-c",
                command,
            ],
            label="Rust toolchain installation",
        )
        validate_runtime(temporary)
        receipt = {
            **receipt_base,
            "tree_sha256": _tree_digest(
                temporary,
                excluded_root_names=frozenset({RUST_RECEIPT}),
            ),
        }
        atomic_write(
            temporary / RUST_RECEIPT,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
        )
        (temporary / RUST_RECEIPT).chmod(0o600)
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, receipt
