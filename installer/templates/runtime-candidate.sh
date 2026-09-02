#!/bin/sh
# Volatile media and control-plane replacement. A reboot discards it.
set -eu

DIR=/run/thingino-runtime-candidate
PRUDYNT=/usr/bin/prudynt
CONTROL=/usr/sbin/thingino-controld
UHTTPD=/usr/bin/uhttpd
PRUDYNT_INIT=/etc/init.d/S31prudynt
CONTROL_INIT=/etc/init.d/S95thingino-control
UHTTPD_INIT=/etc/init.d/S60uhttpd
READY=/run/prudynt-dlink-media.ready
STATE=$DIR/state
WORKER_PIDFILE=/run/thingino-storage-worker.pid
WORKER_SOCKET=/run/thingino-control/storage-v1.sock

mounted_on() {
	awk -v target="$1" '$2 == target { found = 1 } END { exit !found }' /proc/mounts
}

cmdline_has() {
	for token in $(cat /proc/cmdline); do
		[ "$token" = "$1" ] && return 0
	done
	return 1
}

pidfile_owns() {
	pidfile=$1
	expected=$2
	[ -s "$pidfile" ] || return 1
	IFS= read -r pid <"$pidfile" || return 1
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	[ -r "/proc/$pid/exe" ] || return 1
	[ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$expected" ]
}

worker_owned_pid() {
	pid=$1
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	[ -r "/proc/$pid/exe" ] || return 1
	[ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$CONTROL" ] || return 1
	tr '\000' '\n' <"/proc/$pid/cmdline" 2>/dev/null |
		grep -Fqx -- '--storage-worker'
}

worker_running() {
	[ -s "$WORKER_PIDFILE" ] || return 1
	IFS= read -r pid <"$WORKER_PIDFILE" || return 1
	worker_owned_pid "$pid" && [ -S "$WORKER_SOCKET" ]
}

services_stopped() {
	for executable in /proc/[0-9]*/exe; do
		case "$(readlink "$executable" 2>/dev/null || true)" in
			"$PRUDYNT" | "$CONTROL" | "$UHTTPD") return 1 ;;
		esac
	done
	return 0
}

services_running() {
	pidfile_owns /run/prudynt.pid "$PRUDYNT" &&
		pidfile_owns /run/thingino-controld.pid "$CONTROL" &&
		pidfile_owns /var/run/uhttpd.pid "$UHTTPD"
}

candidate_services_running() {
	services_running && worker_running
}

start_candidate_worker() {
	if worker_running; then
		return 0
	fi
	if [ -e "$WORKER_PIDFILE" ]; then
		if IFS= read -r stale_pid <"$WORKER_PIDFILE" && [ -d "/proc/$stale_pid" ]; then
			echo 'refusing an unowned storage worker PID file' >&2
			return 1
		fi
		rm -f "$WORKER_PIDFILE"
	fi
	[ ! -e "$WORKER_SOCKET" ] || [ -S "$WORKER_SOCKET" ] || {
		echo 'refusing a non-socket storage worker path' >&2
		return 1
	}
	start-stop-daemon -S -b -m -p "$WORKER_PIDFILE" -x "$CONTROL" -- \
		--storage-worker || return 1
	i=0
	while [ "$i" -lt 50 ]; do
		worker_running && return 0
		usleep 100000
		i=$((i + 1))
	done
	echo 'storage worker did not remain running' >&2
	return 1
}

stop_candidate_worker() {
	[ -e "$WORKER_PIDFILE" ] || {
		[ ! -S "$WORKER_SOCKET" ] || rm -f "$WORKER_SOCKET"
		return 0
	}
	[ -s "$WORKER_PIDFILE" ] || {
		echo 'refusing an empty storage worker PID file' >&2
		return 1
	}
	IFS= read -r pid <"$WORKER_PIDFILE" || return 1
	if [ ! -d "/proc/$pid" ]; then
		rm -f "$WORKER_PIDFILE"
		[ ! -S "$WORKER_SOCKET" ] || rm -f "$WORKER_SOCKET"
		return 0
	fi
	worker_owned_pid "$pid" || {
		echo 'refusing an unowned storage worker PID' >&2
		return 1
	}
	kill "$pid" 2>/dev/null || true
	i=0
	while worker_owned_pid "$pid" && [ "$i" -lt 50 ]; do
		usleep 100000
		i=$((i + 1))
	done
	worker_owned_pid "$pid" && {
		echo 'storage worker did not stop' >&2
		return 1
	}
	rm -f "$WORKER_PIDFILE"
	[ ! -S "$WORKER_SOCKET" ] || rm -f "$WORKER_SOCKET"
}

hash_file() {
	sha256sum "$1" | awk '{print $1}'
}

wait_ready() {
	i=0
	while [ "$i" -lt 45 ]; do
		[ "$(cat "$READY" 2>/dev/null || true)" = 1080p-started ] && return 0
		sleep 1
		i=$((i + 1))
	done
	return 1
}

print_state() {
	state=$1
	printf 'schema=1\nstate=%s\n' "$state"
	if [ -r "$DIR/candidate.sha256" ]; then
		prudynt_sha=$(awk '$2 == "usr/bin/prudynt" { print $1 }' "$DIR/candidate.sha256")
		control_sha=$(awk '$2 == "usr/sbin/thingino-controld" { print $1 }' "$DIR/candidate.sha256")
		uhttpd_sha=$(awk '$2 == "usr/bin/uhttpd" { print $1 }' "$DIR/candidate.sha256")
		[ -n "$prudynt_sha" ] && printf 'prudynt_sha256=%s\n' "$prudynt_sha"
		[ -n "$control_sha" ] && printf 'control_sha256=%s\n' "$control_sha"
		[ -n "$uhttpd_sha" ] && printf 'uhttpd_sha256=%s\n' "$uhttpd_sha"
	fi
}

stop_services() {
	"$UHTTPD_INIT" stop >/dev/null 2>&1
	"$CONTROL_INIT" stop >/dev/null 2>&1
	stop_candidate_worker
	"$PRUDYNT_INIT" stop >/dev/null 2>&1
	services_stopped
}

start_services() {
	rm -f "$READY"
	"$PRUDYNT_INIT" start >/dev/null 2>&1
	wait_ready
	"$CONTROL_INIT" start >/dev/null 2>&1
	"$UHTTPD_INIT" start >/dev/null 2>&1
	services_running
}

start_candidate_services() {
	rm -f "$READY"
	"$PRUDYNT_INIT" start >/dev/null 2>&1
	wait_ready
	"$CONTROL_INIT" start >/dev/null 2>&1
	start_candidate_worker
	"$UHTTPD_INIT" start >/dev/null 2>&1
	candidate_services_running
}

rollback_core() {
	failed=no
	stop_services || failed=yes
	if mounted_on "$CONTROL"; then
		umount "$CONTROL" || failed=yes
	fi
	if mounted_on "$UHTTPD"; then
		umount "$UHTTPD" || failed=yes
	fi
	if mounted_on "$PRUDYNT"; then
		umount "$PRUDYNT" || failed=yes
	fi
	mounted_on "$CONTROL" && failed=yes
	mounted_on "$UHTTPD" && failed=yes
	mounted_on "$PRUDYNT" && failed=yes
	[ -r "$DIR/baseline.sha256" ] || failed=yes
	if [ -r "$DIR/baseline.sha256" ]; then
		sha256sum -c "$DIR/baseline.sha256" >/dev/null 2>&1 || failed=yes
	fi
	[ "$failed" = no ] || return 1
	start_services || return 1
	printf '%s\n' rolled-back >"$STATE"
	return 0
}

status() {
	prudynt_mounted=no
	control_mounted=no
	uhttpd_mounted=no
	mounted_on "$PRUDYNT" && prudynt_mounted=yes
	mounted_on "$CONTROL" && control_mounted=yes
	mounted_on "$UHTTPD" && uhttpd_mounted=yes
	if [ "$prudynt_mounted" = yes ] && [ "$control_mounted" = yes ] &&
		[ "$uhttpd_mounted" = yes ] &&
		[ "$(cat "$STATE" 2>/dev/null || true)" = active ] &&
		[ "$(cat "$READY" 2>/dev/null || true)" = 1080p-started ] &&
		candidate_services_running &&
		(cd "$DIR" && sha256sum -c candidate.sha256 >/dev/null 2>&1); then
		print_state active
	elif [ "$prudynt_mounted" = no ] && [ "$control_mounted" = no ] &&
		[ "$uhttpd_mounted" = no ]; then
		state=$(cat "$STATE" 2>/dev/null || true)
		if [ "$state" = rolled-back ] &&
			[ -r "$DIR/baseline.sha256" ] &&
			sha256sum -c "$DIR/baseline.sha256" >/dev/null 2>&1 &&
			[ "$(cat "$READY" 2>/dev/null || true)" = 1080p-started ] &&
			services_running; then
			print_state rolled-back
		elif [ -z "$state" ]; then
			print_state absent
		else
			print_state partial
		fi
	else
		print_state partial
	fi
}

activate() {
	cmdline_has 'mem=42M@0x0'
	cmdline_has 'rmem=22M@0x2a00000'
	awk '$2 == "/run" && $3 == "tmpfs" { found = 1 } END { exit !found }' /proc/mounts
	[ "$(cat "$READY" 2>/dev/null || true)" = 1080p-started ]
	services_running
	[ -x "$PRUDYNT" ] && [ -x "$CONTROL" ] && [ -x "$UHTTPD" ]
	[ -x "$PRUDYNT_INIT" ] && [ -x "$CONTROL_INIT" ] && [ -x "$UHTTPD_INIT" ]
	for active in /run/prudynt/mp4ctl-ch*.active; do
		[ ! -e "$active" ] || { echo 'recorder is active' >&2; return 1; }
	done
	cd "$DIR"
	sha256sum -c candidate.sha256 >/dev/null

	! mounted_on "$PRUDYNT"
	! mounted_on "$CONTROL"
	! mounted_on "$UHTTPD"

	baseline_prudynt=$(hash_file "$PRUDYNT")
	baseline_control=$(hash_file "$CONTROL")
	baseline_uhttpd=$(hash_file "$UHTTPD")
	printf '%s  %s\n%s  %s\n%s  %s\n' \
		"$baseline_prudynt" "$PRUDYNT" \
		"$baseline_control" "$CONTROL" \
		"$baseline_uhttpd" "$UHTTPD" >"$DIR/baseline.sha256"

	complete=no
	cleanup() {
		code=$?
		trap - EXIT HUP INT TERM
		if [ "$complete" != yes ]; then
			set +e
			rollback_core
			rollback_status=$?
			set -e
			if [ "$rollback_status" -ne 0 ]; then
				echo 'runtime candidate rollback failed' >&2
			fi
		fi
		exit "$code"
	}
	trap cleanup EXIT HUP INT TERM

	stop_services
	mount -o bind "$DIR/usr/bin/prudynt" "$PRUDYNT"
	mount -o bind "$DIR/usr/sbin/thingino-controld" "$CONTROL"
	mount -o bind "$DIR/usr/bin/uhttpd" "$UHTTPD"
	start_candidate_services
	printf '%s\n' active >"$STATE"
	complete=yes
	print_state active
}

case "${1:-}" in
	activate) activate ;;
	status) status ;;
	rollback)
		if [ ! -d "$DIR" ]; then
			printf 'schema=1\nstate=absent\n'
		else
			set +e
			rollback_core
			rollback_status=$?
			set -e
			[ "$rollback_status" -eq 0 ] || exit "$rollback_status"
			print_state rolled-back
		fi
		;;
	*) echo 'usage: runtime-candidate.sh activate|status|rollback' >&2; exit 2 ;;
esac
