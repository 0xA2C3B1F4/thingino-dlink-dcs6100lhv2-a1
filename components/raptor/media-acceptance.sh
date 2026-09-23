#!/bin/sh
# Read-only media check. S96raptor alone owns service startup and shutdown.
set -eu
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
state=/run/raptor-boot

owner_running() {
    name=$1
    binary=$2
    test -f "$state/$name.pid"
    IFS= read -r pid <"$state/$name.pid"
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    test "$(readlink "/proc/$pid/exe")" = "$binary"
    kill -0 "$pid"
}

start() {
    test "$(cat "$state/ready")" = started
    sub_status=$(/usr/bin/raptorctl -j \
        '{"daemon":"rvd","cmd":"get-stream-enabled","stream_id":1}')
    case "$sub_status" in
        *'"status":"ok"'*) ;;
        *) return 1 ;;
    esac
    case "$sub_status" in *'"supported":true'*) ;; *) return 1 ;; esac
    case "$sub_status" in
        *'"active_enabled":true'*) active=true ;;
        *'"active_enabled":false'*) active=false ;;
        *) return 1 ;;
    esac
    case "$sub_status" in
        *'"configured_enabled":true'*) configured=true ;;
        *'"configured_enabled":false'*) configured=false ;;
        *) return 1 ;;
    esac
    test "$active" = "$configured"
    sub_enabled=$active
    for name in rvd rhd rod rsd ric rad rwd; do
        owner_running "$name" "/usr/bin/$name"
    done
    owner_running rmr0 /usr/bin/rmr
    if [ "$sub_enabled" = true ]; then
        owner_running rmr1 /usr/bin/rmr
    fi
    owner_running storage /usr/sbin/thingino-controld
    owner_running control /usr/sbin/thingino-controld
    curl --fail --silent --max-time 3 --output /dev/null \
        'http://127.0.0.1:8080/snapshot?ch=0'
    if [ "$sub_enabled" = true ]; then
        curl --fail --silent --max-time 3 --output /dev/null \
            'http://127.0.0.1:8080/snapshot?ch=1'
    fi
    printf '%s\n' 'D-Link media: configured Raptor owners and snapshots respond'
}

case "${1:-}" in
    start) start ;;
    stop) true ;;
    *) exit 1 ;;
esac
