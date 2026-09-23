#!/usr/bin/env python3
"""Validate the canonical Thingino Control and static WebUI API contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/thingino-control-api-v1.json"
ROUTES_TS = ROOT / "webui/src/api/routes.ts"
RUST_ROOT = ROOT / "components/thingino-control/src"
STORAGE_WORKER = ROOT / "components/thingino-control/storage-worker/mod.rs"
ALLOWED_EFFECTS = {"none", "read-only", "config-persisted", "physical"}
REQUIRED = {
    "auth",
    "contract_tests",
    "device_effect",
    "error_codes",
    "frontend_adapter",
    "id",
    "methods",
    "request_schema",
    "response_schema",
    "secret_semantics",
    "status_codes",
}


class ContractError(ValueError):
    """The machine-readable Control contract is incomplete or stale."""


def _split_alternatives(value: str) -> list[str]:
    alternatives: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == "|" and depth == 0:
            alternatives.append(value[start:index])
            start = index + 1
    alternatives.append(value[start:])
    return alternatives


def _expand_path_pattern(pattern: str) -> list[str]:
    expanded = [""]
    index = 0
    while index < len(pattern):
        opener = pattern[index]
        if opener not in "[{":
            expanded = [value + opener for value in expanded]
            index += 1
            continue
        closer = "]" if opener == "[" else "}"
        depth = 1
        end = index + 1
        while end < len(pattern) and depth:
            if pattern[end] == opener:
                depth += 1
            elif pattern[end] == closer:
                depth -= 1
            end += 1
        if depth:
            raise ContractError(f"Control route pattern is invalid: {pattern}")
        content = pattern[index + 1 : end - 1]
        alternatives = _split_alternatives(content)
        if opener == "{" and len(alternatives) == 1:
            choices = ["value"]
        else:
            choices = [
                option
                for alternative in alternatives
                for option in _expand_path_pattern(alternative)
            ]
            if opener == "[":
                choices.insert(0, "")
        expanded = [prefix + choice for prefix in expanded for choice in choices]
        index = end
    return expanded


def _covers(pattern: str, route: str) -> bool:
    examples = _expand_path_pattern(pattern)
    if route in examples:
        return True
    if route.endswith(("/", "?", "=")) and any(
        example.startswith(route) for example in examples
    ):
        return True
    return "?" not in pattern and any(
        route.startswith(f"{example}?") for example in examples
    )


def validate() -> dict[str, object]:
    try:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("Control contract is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ContractError("Control contract schema is unsupported")
    routes = document.get("routes")
    auth_kinds = document.get("auth_kinds")
    if not isinstance(routes, list) or not routes or not isinstance(auth_kinds, list):
        raise ContractError("Control contract route or auth list is invalid")
    defaults = document.get("defaults")
    if not isinstance(defaults, dict) or defaults.get("credential_transport") != (
        "Credentials are accepted only in the Authorization header, the trusted "
        "proxy X-API-Key header, or the thingino_session cookie. URL query "
        "credentials are rejected."
    ):
        raise ContractError("Control contract credential transport is invalid")
    for policy in ("mutation_csrf", "trusted_ip_semantics"):
        if not isinstance(defaults.get(policy), str) or not defaults[policy]:
            raise ContractError(f"Control contract lacks {policy}")
    ids: set[str] = set()
    paths: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            raise ContractError("Control contract route is not an object")
        allowed = REQUIRED | {"device_acceptance_checks", "path", "path_pattern"}
        if set(route) - allowed or not REQUIRED.issubset(route):
            raise ContractError(f"Control route fields are incomplete: {route.get('id')}")
        if ("path" in route) == ("path_pattern" in route):
            raise ContractError("Control route needs exactly one path or path_pattern")
        route_id = route.get("id")
        if not isinstance(route_id, str) or route_id in ids:
            raise ContractError("Control route ID is invalid or duplicated")
        ids.add(route_id)
        path = route.get("path", route.get("path_pattern"))
        if not isinstance(path, str) or not path.startswith(("/api/v1/", "/media/v1/")):
            raise ContractError(f"Control route path is invalid: {route_id}")
        paths.append(path)
        methods = route.get("methods")
        if not isinstance(methods, list) or not methods or any(
            method not in {"GET", "POST", "PUT", "DELETE"} for method in methods
        ):
            raise ContractError(f"Control route methods are invalid: {route_id}")
        if route.get("auth") not in auth_kinds:
            raise ContractError(f"Control route auth is invalid: {route_id}")
        if route.get("device_effect") not in ALLOWED_EFFECTS:
            raise ContractError(f"Control route device effect is invalid: {route_id}")
        if route.get("device_effect") in {"physical", "config-persisted", "read-only"}:
            checks = route.get("device_acceptance_checks")
            if not isinstance(checks, list) or not checks:
                raise ContractError(f"Control route lacks device acceptance: {route_id}")
        for field in ("request_schema", "response_schema"):
            if not isinstance(route.get(field), dict) or not route[field]:
                raise ContractError(f"Control route lacks {field}: {route_id}")
        if not isinstance(route.get("secret_semantics"), str) or not route["secret_semantics"]:
            raise ContractError(f"Control route lacks secret semantics: {route_id}")
        if not isinstance(route.get("status_codes"), list) or not route["status_codes"]:
            raise ContractError(f"Control route lacks status codes: {route_id}")
        if not isinstance(route.get("error_codes"), list):
            raise ContractError(f"Control route lacks error codes: {route_id}")
        if route_id != "auth.logout" and any(
            method in {"POST", "PUT", "DELETE"} for method in methods
        ):
            if 415 not in route["status_codes"] or "unsupported_media_type" not in route[
                "error_codes"
            ]:
                raise ContractError(
                    f"mutating route lacks the global browser guard contract: {route_id}"
                )
        tests = route.get("contract_tests")
        if not isinstance(tests, list) or not tests:
            raise ContractError(f"Control route lacks contract tests: {route_id}")
        for relative in tests:
            if not isinstance(relative, str) or not (ROOT / relative).is_file():
                raise ContractError(f"Control route test file is missing: {route_id}")

    frontend = ROUTES_TS.read_text(encoding="utf-8")
    frontend_routes = set(
        value
        for value in re.findall(r'["`](/(?:api|media)/v1/[^"`$]+)', frontend)
        if "${" not in value
    )
    missing_frontend = sorted(
        route for route in frontend_routes if not any(_covers(pattern, route) for pattern in paths)
    )
    if missing_frontend:
        raise ContractError("frontend routes lack canonical contracts: " + ", ".join(missing_frontend))

    production_rust_files = sorted(
        path
        for path in RUST_ROOT.rglob("*.rs")
        if not path.name.endswith("_tests.rs")
    )
    rust = "\n".join(path.read_text(encoding="utf-8") for path in production_rust_files)
    for route in routes:
        path = str(route.get("path", route.get("path_pattern")))
        prefix = re.split(r"[\[{]", path, maxsplit=1)[0].rstrip("/")
        if path.startswith("/api/v1/prudynt["):
            prefix = "/api/v1/prudynt"
        if prefix not in rust:
            raise ContractError(f"canonical route has no production backend implementation: {path}")
        if route["id"] not in {"config.ha", "media"}:
            positions = [match.start() for match in re.finditer(re.escape(prefix), rust)]
            context = "\n".join(
                rust[max(0, position - 2000) : position + 2000]
                for position in positions
            )
            missing_methods = [
                method for method in route["methods"] if f'"{method}"' not in context
            ]
            if missing_methods:
                raise ContractError(
                    "canonical route methods lack a nearby production backend dispatch: "
                    f"{path} ({','.join(missing_methods)})"
                )

    by_id = {str(route["id"]): route for route in routes}
    login = by_id.get("auth.login", {})
    if 429 not in login.get("status_codes", []) or "login_rate_limited" not in login.get(
        "error_codes", []
    ):
        raise ContractError("login rate-limit behavior is absent from the contract")
    for route_id in ("auth.password", "config.access", "webui.api_key"):
        if by_id.get(route_id, {}).get("auth") != "browser-session":
            raise ContractError(f"credential route is not browser-session only: {route_id}")
    for route_id in ("auth.password", "config.access", "webui.api_key"):
        semantics = by_id.get(route_id, {}).get("secret_semantics", "")
        if "10 minutes" not in semantics:
            raise ContractError(f"credential mutation lacks recent-login semantics: {route_id}")
    api_key = by_id.get("webui.api_key", {})
    semantics = api_key.get("secret_semantics", "")
    if "GET reports only exists" not in semantics or "never returned later" not in semantics:
        raise ContractError("API-key one-time secret semantics are incomplete")
    router = (RUST_ROOT / "router.rs").read_text(encoding="utf-8")
    web_auth = (RUST_ROOT / "web_auth.rs").read_text(encoding="utf-8")
    for required in (
        "auth.authorize_session(request.cookie.as_deref())",
        "auth.authorize_recent_session(request.cookie.as_deref())",
        'request.method == "GET"',
        "mutating_request_has_csrf_guard",
        'Some(b"Thingino-WebUI")',
        "api_key_exists()",
    ):
        if required not in router:
            raise ContractError(f"backend lacks contracted authorization behavior: {required}")
    if "pub fn api_key(" in web_auth:
        raise ContractError("backend exposes a stored API-key read method")
    for forbidden in ("Command::new", "std::process::Command", "fork(", "exec("):
        if forbidden in rust:
            raise ContractError(f"request-path backend contains forbidden child operation: {forbidden}")
    worker = STORAGE_WORKER.read_text(encoding="utf-8")
    for required in (
        'const SOCKET: &str = "/run/thingino-control/storage-v1.sock"',
        'const DEVICE: &str = "/dev/mmcblk0p1"',
        'const MOUNTPOINT: &str = "/mnt/mmcblk0p1"',
        'command("/bin/umount"',
        '"/sbin/mkfs.vfat"',
        '"/bin/mount"',
    ):
        if required not in worker:
            raise ContractError(f"storage worker lacks fixed operation: {required}")
    for forbidden in (
        "/bin/sh",
        "sh -c",
        "fdisk",
        "mdev",
        "/usr/sbin/mmc",
        "envfromcard",
        "umount -l",
        "mkfs.exfat",
    ):
        if forbidden in worker:
            raise ContractError(f"storage worker contains forbidden operation: {forbidden}")
    if re.search(r"/(?:cgi-bin|x)/|\.cgi(?:[?\"'`]|$)", frontend):
        raise ContractError("frontend contains a CGI route")
    return {"frontend_routes": len(frontend_routes), "routes": len(routes), "schema_version": 1}


def main() -> int:
    try:
        result = validate()
    except (OSError, ContractError) as exc:
        print(f"Thingino Control contract check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
