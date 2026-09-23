#!/bin/sh
# Run on an already-authorized camera after the production init script starts
# Thingino Control. This script does not upload binaries or write flash.
set -eu

requests=${1:-1000}
web_origin=${2:-https://127.0.0.1}
case "$requests" in ''|*[!0-9]*) echo "request count must be an integer" >&2; exit 2 ;; esac
[ "$requests" -gt 0 ] && [ "$requests" -le 10000 ] || {
	echo "request count must be between 1 and 10000" >&2
	exit 2
}
case "$web_origin" in
	http://* | https://*) ;;
	*) echo "WebUI origin must start with http:// or https://" >&2; exit 2 ;;
esac
case "$web_origin" in
	*[?#]*) echo "WebUI origin must not contain a query or fragment" >&2; exit 2 ;;
esac
web_origin=${web_origin%/}

select_control_pidfile() {
	raptor_state=$1
	legacy_pidfile=$2
	if [ -e "$raptor_state" ]; then
		[ -s "$raptor_state/ready" ] && [ "$(cat "$raptor_state/ready")" = started ] || {
			echo "Raptor boot is not ready" >&2
			return 1
		}
		[ ! -e "$legacy_pidfile" ] || {
			echo "Conflicting Thingino Control PID files" >&2
			return 1
		}
		printf '%s\n' "$raptor_state/control.pid"
	else
		printf '%s\n' "$legacy_pidfile"
	fi
}

pidfile=$(select_control_pidfile /run/raptor-boot /run/thingino-controld.pid)
case "$pidfile" in
	/run/raptor-boot/control.pid) config_check=unsupported ;;
	/run/thingino-controld.pid) config_check=passed ;;
	*) echo "Unrecognized Thingino Control PID file" >&2; exit 1 ;;
esac
config=/etc/thingino.json
api_key_file=/etc/thingino-api.key
work=/run/thingino-control-acceptance.$$
[ -s "$pidfile" ] || { echo "Thingino Control PID file is missing" >&2; exit 1; }
IFS= read -r control_pid <"$pidfile"
case "$control_pid" in ''|*[!0-9]*) echo "invalid Thingino Control PID" >&2; exit 1 ;; esac
[ "$(readlink "/proc/$control_pid/exe" 2>/dev/null)" = /usr/sbin/thingino-controld ] || {
	echo "Thingino Control PID is not owned by Control" >&2
	exit 1
}
tr '\000' '\n' <"/proc/$control_pid/cmdline" | grep -Fqx -- '--camera' || {
	echo "Thingino Control PID is not the camera process" >&2
	exit 1
}
kill -0 "$control_pid" || { echo "Thingino Control is not running" >&2; exit 1; }

mkdir -m 700 "$work"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT HUP INT TERM

curl_config=$work/curl.conf
{
	printf 'header = "Authorization: Bearer '
	jct "$config" get control.token | tr -d '"\r\n'
	printf '"\n'
} >"$curl_config"
chmod 600 "$curl_config"

web_curl_config=$work/web-curl.conf
api_key=$(tr -d '\r\n ' <"$api_key_file" 2>/dev/null || true)
case "$api_key" in
	????????????????????????????????????????????????????????????????)
		case "$api_key" in *[!0-9a-fA-F]*) echo "WebUI API key is not 64 hex" >&2; exit 1 ;; esac
		;;
	*) echo "WebUI API key is not 64 hex" >&2; exit 1 ;;
esac
{
	printf 'header = "X-API-Key: %s"\n' "$api_key"
	case "$web_origin" in https://*) printf 'insecure\n' ;; esac
} >"$web_curl_config"
chmod 600 "$web_curl_config"

request() {
	curl --config "$curl_config" -sS --max-time 5 -o /dev/null -w '%{http_code}' "$@"
}

web_request() {
	curl --config "$web_curl_config" -sS --max-time 5 -o /dev/null -w '%{http_code}' "$@"
}

expect_http() {
	label=$1
	expected=$2
	actual=$3
	[ "$actual" = "$expected" ] || {
		printf '%s: HTTP %s (expected %s)\n' "$label" "$actual" "$expected" >&2
		exit 1
	}
}

expect_equal() {
	label=$1
	[ "$2" = "$3" ] || {
		printf '%s changed during acceptance\n' "$label" >&2
		exit 1
	}
}

expect_unsupported_config() {
	label=$1
	config_file=$2
	target=$3
	status=$(curl --config "$config_file" -sS --max-time 5 \
		-o "$work/unsupported-config.json" -w '%{http_code}' "$target")
	expect_http "$label" 503 "$status"
	grep -Fqx '{"status":"error","error":{"code":"service_unavailable","message":"backend is unavailable"}}' \
		"$work/unsupported-config.json" || {
			echo "$label: unexpected 503 response" >&2
			exit 1
		}
}

rss_before=$(awk '/^VmRSS:/ {print $2}' "/proc/$control_pid/status")
threads_before=$(awk '/^Threads:/ {print $2}' "/proc/$control_pid/status")
fd_before=$(find "/proc/$control_pid/fd" -mindepth 1 -maxdepth 1 | wc -l)
children_before=$(pgrep -P "$control_pid" 2>/dev/null | wc -l)
tmp_before=$(find /tmp -mindepth 1 -maxdepth 1 -type f -name '*control*' | wc -l)

expect_http direct_health 200 "$(request http://127.0.0.1:1998/api/v1/health)"
expect_http direct_media 200 "$(request http://127.0.0.1:1998/api/v1/runtime/media)"
expect_http direct_motion 200 "$(request http://127.0.0.1:1998/api/v1/runtime/motion)"
expect_http direct_ha 200 "$(request http://127.0.0.1:1998/api/v1/runtime/ha)"
if [ "$config_check" = passed ]; then
	expect_http direct_config 200 "$(request http://127.0.0.1:1998/api/v1/config)"
else
	expect_unsupported_config direct_config "$curl_config" http://127.0.0.1:1998/api/v1/config
fi
expect_http auth_rejection 401 "$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:1998/api/v1/health)"
expect_http missing_route 404 "$(request http://127.0.0.1:1998/api/v1/not-a-route)"

# Exercise the production same-origin uhttpd ingress. Control serves API data
# and authorizes each media URL before uhttpd relays its in-memory JPEG. The API
# key lives only in the mode-0600 curl config, never in argv. A static asset
# check binds this run to the CGI-free WebUI build.
expect_http web_health 200 "$(web_request "$web_origin/api/v1/health")"
expect_http web_media 200 "$(web_request "$web_origin/api/v1/runtime/media")"
if [ "$config_check" = passed ]; then
	expect_http web_config 200 "$(web_request "$web_origin/api/v1/config")"
else
	expect_unsupported_config web_config "$web_curl_config" "$web_origin/api/v1/config"
fi
expect_http main_snapshot 200 "$(web_request "$web_origin/api/v1/actions/snapshot?stream_id=0")"
expect_http sub_snapshot 200 "$(web_request "$web_origin/api/v1/actions/snapshot?stream_id=1")"
# These endpoints now require independent ONVIF HTTP Digest. A WebUI API key
# must not bypass the challenge; a positive Digest/JPEG test is separate.
expect_http onvif_main_api_key_rejection 401 "$(web_request "$web_origin/onvif/image.cgi")"
expect_http onvif_sub_api_key_rejection 401 "$(web_request "$web_origin/onvif/image1.cgi")"
case "$web_origin" in https://*) web_tls=-k ;; *) web_tls= ;; esac
app_js_sha=$(curl $web_tls -sS --max-time 5 "$web_origin/assets/app.js" | sha256sum | awk '{print $1}')
installed_app_js_sha=$(sha256sum /var/www/assets/app.js | awk '{print $1}')
expect_equal web_asset_sha "$app_js_sha" "$installed_app_js_sha"
if curl $web_tls -sS --max-time 5 "$web_origin/assets/app.js" | grep -E '/x/|cgi-bin|\.cgi' >/dev/null; then
	echo "WebUI still contains a CGI request path" >&2
	exit 1
fi

started=$(cut -d ' ' -f 1 /proc/uptime)
i=0
while [ "$i" -lt "$requests" ]; do
	expect_http soak_media 200 "$(request http://127.0.0.1:1998/api/v1/runtime/media)"
	i=$((i + 1))
done
finished=$(cut -d ' ' -f 1 /proc/uptime)

rss_after=$(awk '/^VmRSS:/ {print $2}' "/proc/$control_pid/status")
threads_after=$(awk '/^Threads:/ {print $2}' "/proc/$control_pid/status")
fd_after=$(find "/proc/$control_pid/fd" -mindepth 1 -maxdepth 1 | wc -l)
children_after=$(pgrep -P "$control_pid" 2>/dev/null | wc -l)
tmp_after=$(find /tmp -mindepth 1 -maxdepth 1 -type f -name '*control*' | wc -l)
token=$(jct "$config" get control.token | tr -d '"\r\n')
arguments=$(tr '\000' ' ' <"/proc/$control_pid/cmdline")
case "$arguments" in *"$token"*) echo "Control token leaked into process arguments" >&2; exit 1 ;; esac
processes=$(ps w)
case "$processes" in
	*"$token"*) echo "Control token leaked into the process list" >&2; exit 1 ;;
	*"$api_key"*) echo "WebUI API key leaked into the process list" >&2; exit 1 ;;
esac

expect_equal threads "$threads_before" "$threads_after"
expect_equal file_descriptors "$fd_before" "$fd_after"
expect_equal children_before "$children_before" 0
expect_equal children_after "$children_after" 0
expect_equal tmp_control_files "$tmp_before" "$tmp_after"

elapsed=$(awk -v start="$started" -v finish="$finished" 'BEGIN { printf "%.2f", finish-start }')
printf '{"checks":{"auth_rejection":"passed","config_get":"%s","direct_control":"passed","ha_runtime":"passed","motion_runtime":"passed","onvif_api_key_rejection":"passed","same_origin_assets":"passed","same_origin_media":"passed","snapshots":"passed"},"metrics":{"children_after":%s,"children_before":%s,"elapsed_seconds":%s,"fd_after":%s,"fd_before":%s,"requests":%s,"rss_kib_after":%s,"rss_kib_before":%s,"threads_after":%s,"threads_before":%s,"tmp_control_files_after":%s,"tmp_control_files_before":%s},"result":"passed","schema_version":1}\n' \
	"$config_check" \
	"$children_after" "$children_before" \
	"$elapsed" \
	"$fd_after" "$fd_before" \
	"$requests" \
	"$rss_after" "$rss_before" \
	"$threads_after" "$threads_before" \
	"$tmp_after" "$tmp_before"
