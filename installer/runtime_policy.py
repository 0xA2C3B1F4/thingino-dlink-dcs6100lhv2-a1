"""Canonical first-release SSH and local-network policy."""

from __future__ import annotations

import base64
import binascii


DROPBEAR_DEVELOPMENT_ARGUMENTS = '-k -K 300 -R'
DROPBEAR_KEY_ONLY_ARGUMENTS = '-s -g -k -K 300 -R'

WLAN_DHCP_NO_DEFAULT = (
    b"auto wlan0\n"
    b"iface wlan0 inet dhcp\n"
)

NETWORK_INTERFACES = b"source-dir /etc/network/interfaces.d\n"
LOOPBACK_INTERFACE = b"auto lo\niface lo inet loopback\n"

EMPTY_RESOLVER_POLICY = (
    b"# Intentionally empty: the first-release profile has no default route.\n"
)

NETWORK_DEFAULT_ROUTE_GUARD = (
	"\twlan_wait=0\n"
	"\twhile [ \"$wlan_wait\" -lt 45 ]; do\n"
	"\t\tawk '$1 == \"wlan0:\" && ($3 + 0) > 0 { ready=1 } END { exit !ready }' /proc/net/wireless && break\n"
	"\t\tsleep 1\n"
	"\t\twlan_wait=$((wlan_wait + 1))\n"
	"\tdone\n"
	"\tif [ \"$wlan_wait\" -ge 45 ]; then\n"
	"\t\techo -c 160 -e \"D-Link station association timed out\" >&2\n"
	"\t\treturn 1\n"
	"\tfi\n"
    "\tifup -v -f -a || return 1\n"
    "\twhile ip -4 route del default 2>/dev/null; do :; done\n"
	"\tif ip -4 route show default | grep -q '^default'; then\n"
	"\t\techo -c 160 -e \"D-Link policy rejected an IPv4 default route\" >&2\n"
	"\t\treturn 1\n"
	"\tfi\n"
	"\tif [ -e /proc/net/ipv6_route ]; then\n"
	"\t\twhile ip -6 route del default 2>/dev/null; do :; done\n"
	"\t\tif ip -6 route show default | grep -q '^default'; then\n"
	"\t\t\techo -c 160 -e \"D-Link policy rejected an IPv6 default route\" >&2\n"
	"\t\t\treturn 1\n"
	"\t\tfi\n"
	"\tfi\n"
)

UDHCPC_NO_DEFAULT = b"""#!/bin/sh

# D-Link DCS-6100LHV2 A1 first-release DHCP policy.  Accept an address and
# directly connected subnet, but never consume a router, resolver, or hook.

if [ "${interface:-}" != "wlan0" ]; then
	exit 1
fi

remove_default_routes() {
	while ip -4 route del default 2>/dev/null; do :; done
	! ip -4 route show default | grep -q '^default'
}

case "${1:-}" in
	deconfig)
		ip -4 addr flush dev "$interface" || exit 1
		ip link set dev "$interface" up || exit 1
		remove_default_routes || exit 1
		;;
	bound | renew)
		[ -n "${ip:-}" ] && [ -n "${subnet:-}" ] || exit 1
		ip -4 addr flush dev "$interface" || exit 1
		if [ -n "${broadcast:-}" ]; then
			ifconfig "$interface" "$ip" netmask "$subnet" broadcast "$broadcast" || exit 1
		else
			ifconfig "$interface" "$ip" netmask "$subnet" || exit 1
		fi
		ip link set dev "$interface" up || exit 1
		remove_default_routes || exit 1
		;;
	*)
		exit 1
		;;
esac

exit 0
"""


def normalize_ed25519_authorized_key(payload: bytes) -> bytes:
    """Validate one OpenSSH Ed25519 public key and remove its optional comment."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SSH authorized key is not ASCII") from exc
    lines = text.splitlines()
    if len(lines) != 1:
        raise ValueError("SSH authorized key must contain exactly one line")
    fields = lines[0].split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("SSH authorized key must be an Ed25519 public key")
    encoded = fields[1]
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("SSH authorized key has invalid Base64") from exc

    offset = 0

    def ssh_string() -> bytes:
        nonlocal offset
        if offset + 4 > len(blob):
            raise ValueError("SSH authorized key blob is truncated")
        size = int.from_bytes(blob[offset : offset + 4], "big")
        offset += 4
        if offset + size > len(blob):
            raise ValueError("SSH authorized key blob is truncated")
        value = blob[offset : offset + size]
        offset += size
        return value

    if ssh_string() != b"ssh-ed25519":
        raise ValueError("SSH authorized key blob has the wrong algorithm")
    key = ssh_string()
    if len(key) != 32 or offset != len(blob):
        raise ValueError("SSH authorized key blob has the wrong size")
    if base64.b64encode(blob).decode("ascii") != encoded:
        raise ValueError("SSH authorized key is not canonically encoded")
    return f"ssh-ed25519 {encoded}\n".encode("ascii")
