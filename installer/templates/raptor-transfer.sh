#!/bin/sh
# receiver-section: setup
# Fixed member transfer over the existing pinned session. Never extract a tar.
ROOT=/run/raptor-full
MOUNTS=/proc/mounts
PROC=/proc
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

# receiver-section: hex
hex() { [ "${#1}" -eq "$2" ] && case "$1" in *[!0-9a-f]*) false;; *) true;; esac; }
# receiver-section: regular
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
# receiver-section: digest
digest() { sha256sum "$1" | cut -d ' ' -f 1; }
# receiver-section: members
members() {
    printf '%s\n' baseline.absent baseline.sha256 candidate.json etc/raptor.conf payload.sha256 supervisor.sh usr/sbin/thingino-controld usr/lib/librss_common.so usr/lib/librss_ipc.so
    for name in rac rad raptorctl rhd ric ringdump rmd rmr rod rsd rvd; do printf 'usr/bin/%s\n' "$name"; done
}
# receiver-section: member
member() { members | grep -Fqx -- "$1"; }
# receiver-section: volatile
volatile() {
    [ -d "${ROOT%/*}" ] && [ ! -L "${ROOT%/*}" ] || return 30
    awk '$2=="/run" && $3=="tmpfs" {r=1} $2=="/dev/shm" && $3=="tmpfs" {s=1} END {exit !(r&&s)}' "$MOUNTS" || return 31
}
# receiver-section: bound
bound() {
    hex "$1" 32 || return 32
    volatile || return $?
    [ -d "$ROOT" ] && [ ! -L "$ROOT" ] || return 33
    regular "$ROOT/stage.pending" || return 34
    [ "$(wc -l <"$ROOT/stage.pending")" -eq 4 ] && [ "$(wc -c <"$ROOT/stage.pending")" -le 384 ] || return 35
    [ "$(sed -n 's/^nonce=//p' "$ROOT/stage.pending")" = "$1" ] || return 36
    for directory in "$ROOT/etc" "$ROOT/usr" "$ROOT/usr/bin" "$ROOT/usr/lib" "$ROOT/usr/sbin"; do
        [ -d "$directory" ] && [ ! -L "$directory" ] || return 37
    done
}
# receiver-section: initialize
initialize() {
    [ "$#" -eq 3 ] && hex "$1" 32 && hex "$2" 64 && hex "$3" 64 && volatile || return 2
    [ ! -e "$ROOT" ] && [ ! -L "$ROOT" ] || return 2
    # Existing runtime replacement owns the same services; do not coexist.
    [ ! -e "${ROOT%/*}/thingino-runtime-candidate" ] && [ ! -L "${ROOT%/*}/thingino-runtime-candidate" ] || return 2
    mkdir -m 700 "$ROOT" || return 2
    mkdir "$ROOT/etc" "$ROOT/usr" "$ROOT/usr/bin" "$ROOT/usr/lib" "$ROOT/usr/sbin" || return 2
    printf 'schema=1\nnonce=%s\narchive_sha256=%s\nbaseline_mtd3_sha256=%s\n' "$1" "$2" "$3" >"$ROOT/stage.pending" || return 2
    : >"$ROOT/transfer.pending" || return 2
    printf 'initialized %s\n' "$1"
}
# receiver-section: transfer_lock
transfer_lock() {
    # Never steal a lock from another connection, including an interrupted one.
    mkdir "$ROOT/.transfer-lock" || return 2
    LOCK_ID=$(stat -c '%d:%i' "$ROOT/.transfer-lock") || return 2
    trap 'release_transfer_lock >/dev/null 2>&1' 0
    trap 'exit 2' 1 2 15
}
# receiver-section: empty_directory
empty_directory() {
    local entry
    [ -d "$1" ] && [ ! -L "$1" ] || return 2
    for entry in "$1"/* "$1"/.[!.]* "$1"/..?*; do
        [ ! -e "$entry" ] && [ ! -L "$entry" ] || return 2
    done
}
# receiver-section: release_transfer_lock
release_transfer_lock() {
    [ -n "${LOCK_ID-}" ] || return 2
    empty_directory "$ROOT/.transfer-lock" || return 2
    [ "$(stat -c '%d:%i' "$ROOT/.transfer-lock")" = "$LOCK_ID" ] || return 2
    # Only this invocation's verified empty directory; no removal glob.
    rm -r "$ROOT/.transfer-lock" || return 2
    LOCK_ID=
    trap - 0 1 2 15
}
# receiver-section: receive_member
receive_member() (
    [ "$#" -eq 4 ] && bound "$1" && member "$2" && hex "$4" 64 || return 2
    transfer_lock || return 2
    case "$3" in ''|*[!0-9]*) return 2;; esac
    [ "${#3}" -le 7 ] && [ "$3" -ge 1 ] && [ "$3" -le 2097152 ] || return 2
    [ ! -e "$ROOT/stage.receipt" ] && [ ! -L "$ROOT/stage.receipt" ] || return 2
    regular "$ROOT/transfer.pending" || return 2
    [ "$(wc -l <"$ROOT/transfer.pending")" -lt 20 ] || return 2
    path=$ROOT/$2
    [ ! -e "$path" ] && [ ! -L "$path" ] && [ ! -e "$path.part" ] && [ ! -L "$path.part" ] || return 2
    # One extra byte makes surplus payloads fail while bounding memory/disk use.
    head -c "$(( $3 + 1 ))" >"$path.part" || return 2
    [ "$(wc -c <"$path.part")" -eq "$3" ] && [ "$(digest "$path.part")" = "$4" ] || return 2
    case "$2" in usr/bin/*|usr/sbin/*|usr/lib/*) chmod 755 "$path.part";; supervisor.sh) chmod 700 "$path.part";; *) chmod 600 "$path.part";; esac || return 2
    mv "$path.part" "$path" || return 2
    printf '%s  %s\n' "$4" "$2" >>"$ROOT/transfer.pending" || return 2
    release_transfer_lock || return 2
    printf 'received %s %s %s\n' "$2" "$3" "$4"
)
# receiver-section: verify_members
verify_members() {
    regular "$ROOT/transfer.sha256" || return 2
    [ "$(wc -c <"$ROOT/transfer.sha256")" -le 4096 ] || return 2
    awk 'NF!=2 || length($1)!=64 || $1~/[^0-9a-f]/ {exit 1}' "$ROOT/transfer.sha256" || return 2
    [ "$(awk '{print $2}' "$ROOT/transfer.sha256" | sort)" = "$(members | sort)" ] || return 2
    while IFS=' ' read -r sum name extra; do
        [ -z "$extra" ] && member "$name" && regular "$ROOT/$name" || return 2
        [ "$(digest "$ROOT/$name")" = "$sum" ] || return 2
    done <"$ROOT/transfer.sha256"
}
# receiver-section: seal
seal() (
    [ "$#" -eq 2 ] && bound "$1" && hex "$2" 64 || return 2
    transfer_lock || return 2
    [ ! -e "$ROOT/stage.receipt" ] && [ ! -L "$ROOT/stage.receipt" ] || return 2
    [ ! -e "$ROOT/transfer.sha256" ] && [ ! -L "$ROOT/transfer.sha256" ] || return 2
    regular "$ROOT/transfer.pending" || return 2
    sort -k2 "$ROOT/transfer.pending" >"$ROOT/transfer.sha256" || return 2
    [ "$(digest "$ROOT/transfer.sha256")" = "$2" ] && verify_members || return 2
    # The archive digest is the host's validated canonical archive identity.
    # The camera independently recomputes the complete member-manifest digest.
    cat "$ROOT/stage.pending" >"$ROOT/stage.receipt.next" || return 2
    printf 'member_manifest_sha256=%s\n' "$2" >>"$ROOT/stage.receipt.next" || return 2
    mv "$ROOT/stage.receipt.next" "$ROOT/stage.receipt" || return 2
    release_transfer_lock || return 2
    printf 'sealed %s %s\n' "$1" "$2"
)
# receiver-section: invoke
invoke() {
    [ "$#" -eq 3 ] && bound "$1" && hex "$2" 64 || return 2
    regular "$ROOT/stage.receipt" && verify_members || return 2
    [ "$(digest "$ROOT/transfer.sha256")" = "$2" ] || return 2
    [ "$(sed -n 's/^member_manifest_sha256=//p' "$ROOT/stage.receipt")" = "$2" ] || return 2
    case "$3" in preflight|start|renew|status|rollback) /bin/sh "$ROOT/supervisor.sh" "$3";; *) return 2;; esac
}
# receiver-section: diagnose
diagnose() {
    local name state lock_state lifecycle rmdir_state
    [ "$#" -eq 3 ] && hex "$1" 32 && hex "$2" 64 && hex "$3" 64 && bound "$1" || return 2
    [ "$(stat -c '%u:%a' "$ROOT")" = 0:700 ] || return 2
    # Compare full canonical bytes, including final newline and exact fields.
    [ "$(wc -c <"$ROOT/stage.pending")" -eq 214 ] || return 2
    [ "$(cat "$ROOT/stage.pending")" = "$(printf 'schema=1\nnonce=%s\narchive_sha256=%s\nbaseline_mtd3_sha256=%s\n' "$1" "$2" "$3")" ] || return 2
    lock_state=absent
    if [ -L "$ROOT/.transfer-lock" ]; then lock_state=symlink
    elif [ -d "$ROOT/.transfer-lock" ]; then
        if empty_directory "$ROOT/.transfer-lock"; then lock_state=empty; else lock_state=nonempty; fi
    elif [ -e "$ROOT/.transfer-lock" ]; then lock_state=other; fi
    lifecycle=absent
    if [ -e "$ROOT/state" ] || [ -L "$ROOT/state" ]; then lifecycle=present; fi
    rmdir_state=missing
    command -v rmdir >/dev/null 2>&1 && rmdir_state=available
    printf 'raptor_diagnostic=1\ntransfer_lock=%s\nlifecycle_state=%s\nrmdir=%s\n' "$lock_state" "$lifecycle" "$rmdir_state"
    for name in $(members | sort); do
        state=absent
        if [ -L "$ROOT/$name" ]; then state=symlink
        elif [ -f "$ROOT/$name" ]; then state=regular
        elif [ -e "$ROOT/$name" ]; then state=other; fi
        printf 'member=%s %s\n' "$name" "$state"
    done
}
# receiver-section: partial_inventory
partial_inventory() {
    local directory entry relative
    bound "$1" || return $?
    hex "$2" 64 && hex "$3" 64 && hex "$4" 64 || return 38
    [ "$(stat -c '%u:%a' "$ROOT")" = 0:700 ] || return 39
    [ "$(wc -c <"$ROOT/stage.pending")" -eq 214 ] || return 40
    [ "$(digest "$ROOT/stage.pending")" = "$(printf 'schema=1\nnonce=%s\narchive_sha256=%s\nbaseline_mtd3_sha256=%s\n' "$1" "$2" "$3" | sha256sum | cut -d ' ' -f 1)" ] || return 41
    regular "$ROOT/baseline.absent" || return 42
    [ "$(wc -c <"$ROOT/baseline.absent")" -eq 76 ] || return 43
    [ "$(digest "$ROOT/baseline.absent")" = "$4" ] || return 44
    regular "$ROOT/transfer.pending" || return 45
    [ "$(wc -c <"$ROOT/transfer.pending")" -eq 82 ] || return 46
    [ "$(digest "$ROOT/transfer.pending")" = "$(printf '%s  baseline.absent\n' "$4" | sha256sum | cut -d ' ' -f 1)" ] || return 47
    [ -d "$ROOT/.transfer-lock" ] && [ ! -L "$ROOT/.transfer-lock" ] || return 48
    empty_directory "$ROOT/.transfer-lock" || return 49
    for directory in "$ROOT" "$ROOT/etc" "$ROOT/usr" "$ROOT/usr/bin" "$ROOT/usr/lib" "$ROOT/usr/sbin" "$ROOT/.transfer-lock"; do
        [ -d "$directory" ] && [ ! -L "$directory" ] || return 50
        for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
            [ -e "$entry" ] || [ -L "$entry" ] || continue
            [ ! -L "$entry" ] || return 51
            relative=${entry#"$ROOT/"}
            case "$relative" in stage.pending|transfer.pending|baseline.absent|.transfer-lock|etc|usr|usr/bin|usr/lib|usr/sbin) :;; *) return 52;; esac
        done
    done
}
# receiver-section: proc_stat
proc_stat() {
    local line tail numeric f3 f4 f5 f6 f8 f9 f10 f11 f12 f13 f14 f15 f16 f17 f18 f19 rest
    IFS= read -r line <"$1" || return 60
    PARSED_PID=${line%% *}
    case "$line" in *') '*) tail=${line##*) };; *) return 61;; esac
    IFS=' ' read -r PARSED_STATE PARSED_PPID f3 f4 f5 f6 PARSED_FLAGS f8 f9 f10 f11 f12 f13 f14 f15 f16 f17 f18 f19 PARSED_START rest <<EOF
$tail
EOF
    case "$PARSED_PID:$PARSED_PPID:$PARSED_START:$PARSED_FLAGS" in *[!0-9:]*|:*|*::*|*:) return 62;; esac
    for numeric in "$PARSED_PID" "$PARSED_PPID" "$PARSED_START" "$PARSED_FLAGS"; do
        case "$numeric" in 0[0-9]*) return 63;; esac
    done
    case "$PARSED_STATE" in [A-Za-z]) :;; *) return 64;; esac
}
# receiver-section: quarantine_ancestors
quarantine_ancestors() {
    local pid count
    # $$ is the invoking shell in command substitution. This builtin read
    # opens /proc/self in the actual executing shell, without a child process.
    Q_PID=none
    proc_stat "$PROC/self/stat" || return $?
    pid=$PARSED_PID count=0 EXCLUDED_PROCESSES=' '
    while [ "$pid" -ne 0 ]; do
        Q_PID=$pid
        count=$((count + 1)); [ "$count" -le 32 ] || return 65
        proc_stat "$PROC/$pid/stat" || return $?
        [ "$PARSED_PID" = "$pid" ] || return 66
        case "$EXCLUDED_PROCESSES" in *" $pid:"*) return 67;; esac
        EXCLUDED_PROCESSES="$EXCLUDED_PROCESSES$pid:$PARSED_START "
        pid=$PARSED_PPID
    done
}
# receiver-section: quarantine_process_scan
quarantine_process_scan() {
    local directory pid born first_flags first_state exe cmd cmd_bytes exe_missing code
    for directory in "$PROC"/[0-9]*; do
        [ -d "$directory" ] || continue
        pid=${directory##*/} Q_PID=${directory##*/}
        if proc_stat "$directory/stat"; then :; else code=$?; [ ! -d "$directory" ] && continue; return "$code"; fi
        [ "$PARSED_PID" = "$pid" ] || return 68
        born=$PARSED_START first_flags=$PARSED_FLAGS first_state=$PARSED_STATE
        case "$EXCLUDED_PROCESSES" in *" $pid:$born "*) continue;; esac
        exe_missing=0
        if ! exe=$(readlink "$directory/exe"); then
            [ ! -d "$directory" ] && continue
            [ ! -e "$directory/exe" ] || return 69
            exe=
            exe_missing=1
            [ ! -L "$directory/exe" ] || exe_missing=2
        fi
        if ! cmd=$(tr '\000' '\n' <"$directory/cmdline"); then [ ! -d "$directory" ] && continue; return 70; fi
        if [ "$exe_missing" -ne 0 ]; then
            if ! cmd_bytes=$(wc -c <"$directory/cmdline"); then [ ! -d "$directory" ] && continue; return 70; fi
        fi
        if proc_stat "$directory/stat"; then :; else code=$?; [ ! -d "$directory" ] && continue; return "$code"; fi
        [ "$PARSED_PID:$PARSED_START" = "$pid:$born" ] || return 71
        case "$exe:$cmd" in *"$ROOT"*|*'/run/raptor-full'*) return 72;; esac
        if [ "$exe_missing" -ne 0 ]; then
            [ -z "$cmd" ] && [ "$cmd_bytes" -eq 0 ] || return 73
            # Linux proc magic links can exist while readlink returns ENOENT.
            # Require a kernel thread or an ended zombie in both stat reads.
            if [ "$exe_missing" -eq 2 ]; then
                { [ "$((first_flags & 2097152))" -ne 0 ] && [ "$((PARSED_FLAGS & 2097152))" -ne 0 ]; } ||
                    { [ "$first_state" = Z ] && [ "$PARSED_STATE" = Z ]; } || return 69
            fi
            # Explicit kernel-thread flag or an ended zombie, never a generic
            # missing-executable exception for a live userspace process.
            [ "$PARSED_STATE" = Z ] || [ "$((PARSED_FLAGS & 2097152))" -ne 0 ] || return 74
        fi
    done
}
# receiver-section: inspection
inspection_gates() {
    local original_id original_dev destination
    [ "$#" -eq 4 ] || return 80
    partial_inventory "$@" || return $?
    original_id=$(stat -c '%d:%i' "$ROOT") || return 81
    original_dev=$(stat -c '%d' "$ROOT") || return 82
    [ "$(stat -c '%d' "${ROOT%/*}")" = "$original_dev" ] || return 83
    quarantine_ancestors || return $?
    Q_SCAN=1
    quarantine_process_scan || return $?
    sleep 1 || return 84
    Q_SCAN=2
    quarantine_process_scan || return $?
    Q_PID=none
    destination=${ROOT%/*}/raptor-full-quarantine-$1
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || return 85
    partial_inventory "$@" || return $?
    [ "$(stat -c '%d:%i' "$ROOT")" = "$original_id" ] || return 86
}
inspect_quarantine() {
    local code result
    Q_PID=none Q_SCAN=0
    inspection_gates "$@" 2>/dev/null
    code=$? result=blocked
    [ "$code" -ne 0 ] || result=ready
    case "$Q_PID" in ''|0*|*[!0-9]*) Q_PID=none;; esac
    [ "${#Q_PID}" -le 10 ] || Q_PID=none
    printf 'raptor_quarantine_diagnostic=1\nresult=%s\ncode=%s\npid=%s\nscan=%s\n' "$result" "$code" "$Q_PID" "$Q_SCAN"
}
# receiver-section: inspection_entry
if [ "${1-}" = inspect-quarantine ]; then
    shift
    inspect_quarantine "$@"
    exit $?
fi
# receiver-section: quarantine
quarantine() (
    [ "$#" -eq 4 ] || return 2
    partial_inventory "$@" || return 2
    original_id=$(stat -c '%d:%i' "$ROOT") || return 2
    original_dev=$(stat -c '%d' "$ROOT") || return 2
    [ "$(stat -c '%d' "${ROOT%/*}")" = "$original_dev" ] || return 2
    quarantine_ancestors && quarantine_process_scan && sleep 1 && quarantine_process_scan || return 2
    destination=${ROOT%/*}/raptor-full-quarantine-$1
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || return 2
    # One authorized writer is required. /proc scans are not a defence against
    # a rogue root process racing filesystem checks or creating new processes.
    mkdir -m 700 "$destination" || return 2
    [ -d "$destination" ] && [ ! -L "$destination" ] && [ "$(stat -c '%u:%a' "$destination")" = 0:700 ] || return 2
    [ "$(stat -c '%d' "$destination")" = "$original_dev" ] || return 2
    partial_inventory "$@" && [ "$(stat -c '%d:%i' "$ROOT")" = "$original_id" ] || return 2
    original_root=$ROOT
    mv "$ROOT" "$destination/payload" || return 2
    [ ! -e "$original_root" ] && [ ! -L "$original_root" ] || return 2
    ROOT=$destination/payload
    [ "$(stat -c '%d:%i' "$ROOT")" = "$original_id" ] && partial_inventory "$@" || return 2
    printf 'quarantined %s %s\n' "$1" "$2"
)

# receiver-section: library
if [ "${1-}" = --library-test ]; then
    case "${RAPTOR_TRANSFER_TEST_ROOT-}" in "${TMPDIR:?}"/*) :;; *) exit 2;; esac
    [ ! -L "$RAPTOR_TRANSFER_TEST_ROOT" ] && regular "$RAPTOR_TRANSFER_TEST_ROOT/HOST-ONLY-FIXTURE" || exit 2
    ROOT=$RAPTOR_TRANSFER_TEST_ROOT/run/raptor-full
    MOUNTS=$RAPTOR_TRANSFER_TEST_ROOT/mounts
    PROC=$RAPTOR_TRANSFER_TEST_ROOT/proc
    invoke() { return 2; }
    return 0 2>/dev/null || exit 0
fi
# receiver-section: dispatch
action=${1-}; [ "$#" -gt 0 ] && shift
case "$action" in initialize|receive|seal|invoke|diagnose|quarantine) :;; *) echo 'raptor_error=unknown-rejected' >&2; exit 2;; esac
# Buffer bounded replies: no partial ACK or raw utility diagnostics on failure.
dispatch() {
    case "$action" in initialize) initialize "$@";; receive) receive_member "$@";; seal) seal "$@";; invoke) invoke "$@";; diagnose) diagnose "$@";; quarantine) quarantine "$@";; esac
}
reply=$(dispatch "$@" 2>/dev/null)
result=$?
if [ "$result" -ne 0 ]; then printf 'raptor_error=%s-rejected\n' "$action" >&2; exit "$result"; fi
printf '%s\n' "$reply"
