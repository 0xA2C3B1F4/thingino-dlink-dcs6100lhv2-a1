#!/bin/sh
# Exercise an already-running Thingino Control daemon against RAM-only fixtures.
#
# This helper is not device authorization and does not upload or start the
# daemon. Run it only after separately confirming that the exact target is safe,
# the binary and generated token are under /run/thingino-control-smoke, the
# daemon PID is recorded there, and loopback ports 39081/39082 are unused.
set -eu

work=/run/thingino-control-smoke
daemon_pid=$(cat "$work/pid")
token=$(cat "$work/token")
fixture_pid=

cleanup_fixture() {
	if test -n "$fixture_pid" && kill -0 "$fixture_pid" 2>/dev/null; then
		kill "$fixture_pid" 2>/dev/null || true
		wait "$fixture_pid" 2>/dev/null || true
	fi
}
trap cleanup_fixture EXIT HUP INT TERM

request() {
	printf 'GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer %s\r\nContent-Length: 0\r\n\r\n' "$token" |
		nc -w 5 127.0.0.1 39081
}

(
	printf 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 31\r\nConnection: keep-alive\r\n\r\n{"status":"ok","healthy":true}\n'
) | nc -l -p 39082 >/dev/null &
fixture_pid=$!
sleep 1
success=$(request)
wait "$fixture_pid"
fixture_pid=
success_status=$(printf '%s\n' "$success" | sed -n '1s/\r$//p')
success_body=$(printf '%s\n' "$success" | sed -n '/^\r$/,$p' | sed '1d')
test "$success_status" = 'HTTP/1.1 200 OK'
test "$success_body" = '{"status":"ok","healthy":true}'
printf 'success_status=%s\n' "$success_status"

(
	sleep 4
) | nc -l -p 39082 >/dev/null &
fixture_pid=$!
sleep 1
started=$(cut -d ' ' -f 1 /proc/uptime)
timeout_response=$(request)
finished=$(cut -d ' ' -f 1 /proc/uptime)
wait "$fixture_pid"
fixture_pid=
timeout_status=$(printf '%s\n' "$timeout_response" | sed -n '1s/\r$//p')
timeout_seconds=$(awk -v start="$started" -v finish="$finished" 'BEGIN { printf "%.2f", finish - start }')
test "$timeout_status" = 'HTTP/1.1 504 Gateway Timeout'
awk -v seconds="$timeout_seconds" 'BEGIN { exit !(seconds <= 3.20) }'
printf 'timeout_status=%s\n' "$timeout_status"
printf 'timeout_seconds=%s\n' "$timeout_seconds"

kill -0 "$daemon_pid"
awk '/^(VmSize|VmRSS|VmData|VmStk|Threads):/ {print}' "/proc/$daemon_pid/status"
printf 'mem_free_kib='
awk '/^MemFree:/ {print $2}' /proc/meminfo
printf 'daemon_children='
for child in /proc/[0-9]*/stat; do
	{ read -r child_pid child_name child_state parent_pid rest < "$child"; } 2>/dev/null || continue
	test "$parent_pid" = "$daemon_pid" && printf '%s,' "$child_pid"
done
printf '\n'
