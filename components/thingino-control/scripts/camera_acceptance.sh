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

pidfile=/run/thingino-controld.pid
config=/etc/thingino.json
api_key_file=/etc/thingino-api.key
work=/run/thingino-control-acceptance.$$
[ -s "$pidfile" ] || { echo "Thingino Control PID file is missing" >&2; exit 1; }
IFS= read -r control_pid <"$pidfile"
case "$control_pid" in ''|*[!0-9]*) echo "invalid Thingino Control PID" >&2; exit 1 ;; esac
[ -r "/proc/$control_pid/exe" ] || { echo "Thingino Control is not running" >&2; exit 1; }

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

rss_before=$(awk '/^VmRSS:/ {print $2}' "/proc/$control_pid/status")
threads_before=$(awk '/^Threads:/ {print $2}' "/proc/$control_pid/status")
fd_before=$(find "/proc/$control_pid/fd" -mindepth 1 -maxdepth 1 | wc -l)
children_before=$(pgrep -P "$control_pid" 2>/dev/null | wc -l)
tmp_before=$(find /tmp -mindepth 1 -maxdepth 1 -type f -name '*control*' | wc -l)

[ "$(request http://127.0.0.1:1998/api/v1/health)" = 200 ]
[ "$(request http://127.0.0.1:1998/api/v1/runtime/media)" = 200 ]
[ "$(request http://127.0.0.1:1998/api/v1/runtime/motion)" = 200 ]
[ "$(request http://127.0.0.1:1998/api/v1/runtime/ha)" = 200 ]
[ "$(request http://127.0.0.1:1998/api/v1/config)" = 200 ]
[ "$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:1998/api/v1/health)" = 401 ]
[ "$(request http://127.0.0.1:1998/api/v1/not-a-route)" = 404 ]

# Exercise the production same-origin uhttpd ingress. Control serves API data
# and authorizes each media URL before uhttpd relays its in-memory JPEG. The API
# key lives only in the mode-0600 curl config, never in argv. A static asset
# check binds this run to the CGI-free WebUI build.
[ "$(web_request "$web_origin/api/v1/health")" = 200 ]
[ "$(web_request "$web_origin/api/v1/runtime/media")" = 200 ]
[ "$(web_request "$web_origin/api/v1/config")" = 200 ]
[ "$(web_request "$web_origin/api/v1/actions/snapshot?stream_id=0")" = 200 ]
[ "$(web_request "$web_origin/api/v1/actions/snapshot?stream_id=1")" = 200 ]
[ "$(web_request "$web_origin/onvif/image.cgi")" = 200 ]
[ "$(web_request "$web_origin/onvif/image1.cgi")" = 200 ]
case "$web_origin" in https://*) web_tls=-k ;; *) web_tls= ;; esac
app_js_sha=$(curl $web_tls -sS --max-time 5 "$web_origin/assets/app.js" | sha256sum | awk '{print $1}')
installed_app_js_sha=$(sha256sum /var/www/assets/app.js | awk '{print $1}')
[ "$app_js_sha" = "$installed_app_js_sha" ]
if curl $web_tls -sS --max-time 5 "$web_origin/assets/app.js" | grep -E '/x/|cgi-bin|\.cgi' >/dev/null; then
	echo "WebUI still contains a CGI request path" >&2
	exit 1
fi

started=$(cut -d ' ' -f 1 /proc/uptime)
i=0
while [ "$i" -lt "$requests" ]; do
	[ "$(request http://127.0.0.1:1998/api/v1/runtime/media)" = 200 ]
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

[ "$threads_before" = "$threads_after" ]
[ "$fd_before" = "$fd_after" ]
[ "$children_before" = 0 ]
[ "$children_after" = 0 ]
[ "$tmp_before" = "$tmp_after" ]

elapsed=$(awk -v start="$started" -v finish="$finished" 'BEGIN { printf "%.2f", finish-start }')
printf '{"checks":{"auth_rejection":"passed","config_get":"passed","direct_control":"passed","ha_runtime":"passed","motion_runtime":"passed","same_origin_assets":"passed","same_origin_media":"passed","snapshots":"passed"},"metrics":{"children_after":%s,"children_before":%s,"elapsed_seconds":%s,"fd_after":%s,"fd_before":%s,"requests":%s,"rss_kib_after":%s,"rss_kib_before":%s,"threads_after":%s,"threads_before":%s,"tmp_control_files_after":%s,"tmp_control_files_before":%s},"result":"passed","schema_version":1}\n' \
	"$children_after" "$children_before" \
	"$elapsed" \
	"$fd_after" "$fd_before" \
	"$requests" \
	"$rss_after" "$rss_before" \
	"$threads_after" "$threads_before" \
	"$tmp_after" "$tmp_before"
