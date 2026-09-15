#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

MTD3_SIZE=8126464
MTD3_FOOTER_OFFSET=8125952
MTD3_FOOTER_SIZE=512

# recoveryctl runs as a fresh forced-command shell, so it cannot inherit the
# init process's shell functions.  Keep the small LED transition helper local
# to the process that performs the verified mtd3 write.
led() {
	color=$1
	green=0
	red=0
	[ "$color" = green ] && green=1
	[ "$color" = red ] && red=1
	[ "$color" = amber ] && green=1 && red=1
	[ -e /sys/class/leds/led_g/brightness ] && echo "$green" >/sys/class/leds/led_g/brightness
	[ -e /sys/class/leds/led_r/brightness ] && echo "$red" >/sys/class/leds/led_r/brightness
	return 0
}

exact_layout() {
	[ "$(wc -l </proc/mtd)" -eq 7 ] || return 1
	grep -Fqx 'mtd0: 00040000 00008000 "boot"' /proc/mtd || return 1
	grep -Fqx 'mtd1: 001c0000 00008000 "kernel"' /proc/mtd || return 1
	grep -Fqx 'mtd2: 00480000 00008000 "rootfs"' /proc/mtd || return 1
	grep -Fqx 'mtd3: 007c0000 00008000 "userdata"' /proc/mtd || return 1
	grep -Fqx 'mtd4: 00180000 00008000 "userdata2"' /proc/mtd || return 1
	grep -Fqx 'mtd5: 00040000 00008000 "userdata3"' /proc/mtd || return 1
}

personal_mtd3_identity() {
	rm -f /run/mtd3.inspect.footer
	dd if=/dev/mtd3 bs=1 skip="$MTD3_FOOTER_OFFSET" count="$MTD3_FOOTER_SIZE" 2>/dev/null |
		tr -d '\377' >/run/mtd3.inspect.footer || return 1
	[ "$(sed -n '1p' /run/mtd3.inspect.footer)" = DCS6100-MTD3-V1 ] || return 1
	[ "$(sed -n '2p' /run/mtd3.inspect.footer)" = target=DCS-6100LHV2-A1 ] || return 1
	[ "$(sed -n '5p' /run/mtd3.inspect.footer)" = image_size="$MTD3_SIZE" ] || return 1
	[ "$(sed -n '6p' /run/mtd3.inspect.footer)" = END ] || return 1
	[ "$(wc -l </run/mtd3.inspect.footer)" -eq 6 ] || return 1
	personal_payload_size=$(sed -n 's/^payload_size=//p' /run/mtd3.inspect.footer)
	personal_payload_sha256=$(sed -n 's/^payload_sha256=//p' /run/mtd3.inspect.footer)
	case "$personal_payload_size" in ''|*[!0-9]*) return 1 ;; esac
	[ "$personal_payload_size" -ge 4096 ] &&
		[ "$personal_payload_size" -le "$MTD3_FOOTER_OFFSET" ] || return 1
	[ "${#personal_payload_sha256}" -eq 64 ] || return 1
	case "$personal_payload_sha256" in *[!0-9a-f]*) return 1 ;; esac
	actual_payload_sha256=$(head -c "$personal_payload_size" /dev/mtd3 | sha256sum)
	actual_payload_sha256=${actual_payload_sha256%% *}
	[ "$actual_payload_sha256" = "$personal_payload_sha256" ] || return 1
	gap_size=$((MTD3_FOOTER_OFFSET - personal_payload_size))
	[ "$(head -c "$MTD3_FOOTER_OFFSET" /dev/mtd3 |
		tail -c "$gap_size" | tr -d '\377' | wc -c)" -eq 0 ] || return 1
	[ "$(head -c 4 /dev/mtd3)" = hsqs ] || return 1
	personal_image_sha256=$(sha256sum /dev/mtd3)
	personal_image_sha256=${personal_image_sha256%% *}
}

stock_mtd3_matches() {
	rm -rf /run/stock-inspect
	mkdir -p /run/stock-inspect
	stock_mounted=0
	cleanup_stock_inspect() {
		[ "$stock_mounted" -eq 0 ] || umount /run/stock-inspect 2>/dev/null || true
	}
	trap cleanup_stock_inspect EXIT HUP INT TERM
	if ! mount -t jffs2 -o ro,nosuid,nodev,noexec /dev/mtdblock3 /run/stock-inspect; then
		trap - EXIT HUP INT TERM
		return 1
	fi
	stock_mounted=1
	check_stock_file() {
		stock_path=/run/stock-inspect/lib/$1
		[ -f "$stock_path" ] && [ ! -L "$stock_path" ] || return 1
		[ "$(wc -c <"$stock_path")" -eq "$2" ] || return 1
		stock_sha256=$(sha256sum "$stock_path")
		stock_sha256=${stock_sha256%% *}
		[ "$stock_sha256" = "$3" ]
	}
	stock_valid=0
	if grep -q '^/dev/mtdblock3 /run/stock-inspect jffs2 ro[, ]' /proc/mounts &&
		check_stock_file libimp.so 1146852 14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe &&
		check_stock_file libalog.so 36044 40fd7eb9237772f705a92e9792325f07f0fe022479923f8dc67653cc11450ea1 &&
		check_stock_file libsysutils.so 30020 befca6166d2e25b749cc9fff4798332f42a2d997b00d4aee713358d803353b79; then
		stock_valid=1
	fi
	umount /run/stock-inspect
	stock_mounted=0
	trap - EXIT HUP INT TERM
	[ "$stock_valid" -eq 1 ]
}

if [ "${1:-}" = dispatch ]; then
	set -f
	previous_ifs=$IFS
	IFS=' '
	set -- ${SSH_ORIGINAL_COMMAND:-}
	IFS=$previous_ifs
fi

case "${1:-}" in
status)
	cat /run/recovery.state
	;;
inspect-nor)
	printf 'schema=1\n'
	if ! exact_layout; then
		printf 'layout=unknown\n'
		exit 0
	fi
	printf 'layout=dcs6100lhv2-a1-six-partition\n'
	mtd1_sha256=$(sha256sum /dev/mtd1)
	mtd1_sha256=${mtd1_sha256%% *}
	mtd2_sha256=$(sha256sum /dev/mtd2)
	mtd2_sha256=${mtd2_sha256%% *}
	mtd1_magic=$(head -c 4 /dev/mtd1 | hexdump -v -e '1/1 "%02x"')
	mtd2_magic=$(head -c 4 /dev/mtd2)
	[ "$mtd1_magic" = 27051956 ] && mtd1_kind=uimage || mtd1_kind=unknown
	[ "$mtd2_magic" = hsqs ] && mtd2_kind=squashfs || mtd2_kind=unknown
	printf 'mtd1_kind=%s\nmtd1_sha256=%s\n' "$mtd1_kind" "$mtd1_sha256"
	printf 'mtd2_kind=%s\nmtd2_sha256=%s\n' "$mtd2_kind" "$mtd2_sha256"
	if grep -q '^/dev/mtdblock3 ' /proc/mounts; then
		printf 'mtd3_mounted=yes\n'
	else
		printf 'mtd3_mounted=no\n'
	fi
	if personal_mtd3_identity; then
		printf 'mtd3_kind=personal\n'
		printf 'mtd3_sha256=%s\n' "$personal_image_sha256"
		printf 'mtd3_payload_sha256=%s\n' "$personal_payload_sha256"
	elif stock_mtd3_matches; then
		mtd3_sha256=$(sha256sum /dev/mtd3)
		mtd3_sha256=${mtd3_sha256%% *}
		printf 'mtd3_kind=stock\nmtd3_sha256=%s\nmtd3_payload_sha256=-\n' "$mtd3_sha256"
	else
		mtd3_sha256=$(sha256sum /dev/mtd3)
		mtd3_sha256=${mtd3_sha256%% *}
		printf 'mtd3_kind=unknown\nmtd3_sha256=%s\nmtd3_payload_sha256=-\n' "$mtd3_sha256"
	fi
	;;
receive)
	expected_size=${2:-}
	expected_sha256=${3:-}
	case "$expected_size" in ''|*[!0-9]*|0*) exit 2 ;; esac
	[ "$expected_size" -ge 1 ] && [ "$expected_size" -le 8126464 ] || exit 2
	[ "${#expected_sha256}" -eq 64 ] || exit 2
	case "$expected_sha256" in *[!0-9a-f]*) exit 2 ;; esac
	temporary=/run/transfer.bin.new
	trap 'rm -f "$temporary"' EXIT HUP INT TERM
	# Bound even a misbehaving authenticated sender before accepting stdin.
	ulimit -f $(( (expected_size + 511) / 512 ))
	cat >"$temporary"
	[ "$(wc -c <"$temporary")" -eq "$expected_size" ] || exit 2
	actual_sha256=$(sha256sum "$temporary")
	actual_sha256=${actual_sha256%% *}
	[ "$actual_sha256" = "$expected_sha256" ] || exit 2
	mv "$temporary" /run/transfer.bin
	trap - EXIT HUP INT TERM
	printf '%s\n' accepted
	;;
send)
	expected_sha256=${2:-}
	[ "${#expected_sha256}" -eq 64 ] || exit 2
	case "$expected_sha256" in *[!0-9a-f]*) exit 2 ;; esac
	[ -f /run/transfer.bin ] || exit 2
	actual_sha256=$(sha256sum /run/transfer.bin)
	actual_sha256=${actual_sha256%% *}
	[ "$actual_sha256" = "$expected_sha256" ] || exit 2
	cat /run/transfer.bin
	;;
vendor-export)
	[ "$(cat /run/recovery.state)" != thingino ] || exit 2
	rm -rf /run/stock-mtd3 /run/vendor-export
	mkdir -p /run/stock-mtd3 /run/vendor-export
	vendor_mounted=0
	cleanup_vendor() {
		[ "$vendor_mounted" -eq 0 ] || umount /run/stock-mtd3 2>/dev/null || true
	}
	trap cleanup_vendor EXIT HUP INT TERM
	mount -t jffs2 -o ro,nosuid,nodev,noexec /dev/mtdblock3 /run/stock-mtd3
	vendor_mounted=1
	grep -q '^/dev/mtdblock3 /run/stock-mtd3 jffs2 ro[, ]' /proc/mounts || exit 2
	check_vendor() {
		source_relative=$1
		expected_size=$2
		expected_sha256=$3
		output_name=$4
		source=/run/stock-mtd3/$source_relative
		[ -f "$source" ] && [ ! -L "$source" ] || exit 2
		[ "$(wc -c <"$source")" -eq "$expected_size" ] || exit 2
		actual_sha256=$(sha256sum "$source")
		actual_sha256=${actual_sha256%% *}
		[ "$actual_sha256" = "$expected_sha256" ] || exit 2
		cp "$source" "/run/vendor-export/$output_name"
	}
	check_vendor lib/libimp.so 1146852 14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe libimp.so
	check_vendor lib/libalog.so 36044 40fd7eb9237772f705a92e9792325f07f0fe022479923f8dc67653cc11450ea1 libalog.so
	check_vendor lib/libsysutils.so 30020 befca6166d2e25b749cc9fff4798332f42a2d997b00d4aee713358d803353b79 libsysutils.so
	check_vendor lib/libaudioProcess.so 697757 0f03bee6156b3a570c4af8cc199a53b1302fb4450570a53222107ff498a3ae72 libaudioProcess.so
	check_vendor lib/modules/tx-isp-t31.ko 1106726 d13b5654e858155d07ce10dce97477549974227a90d02c37737907221fc2a573 tx-isp-t31.ko
	check_vendor lib/modules/sensor_os02g10_t31.ko 16218 4b034950cd9450f9c1cfdff19bcf0d92a1cf69fd412218c1841706ef9cdde8be sensor_os02g10_t31.ko
	check_vendor etc/sensor/os02g10-t31.bin 159736 dce8af706b8663bcefe47b38418fbec469d7b3704665a45544f8bb1845e5f78e os02g10-t31.bin
	umount /run/stock-mtd3
	vendor_mounted=0
	trap - EXIT HUP INT TERM
	tar -cf /run/transfer.bin -C /run/vendor-export .
	transfer_size=$(wc -c </run/transfer.bin)
	transfer_sha256=$(sha256sum /run/transfer.bin)
	transfer_sha256=${transfer_sha256%% *}
	printf 'vendor %s %s\n' "$transfer_size" "$transfer_sha256"
	;;
install-mtd3)
	expected_sha256=${2:-}
	[ "${#expected_sha256}" -eq 64 ] || exit 2
	case "$expected_sha256" in *[!0-9a-f]*) exit 2 ;; esac
	# Never erase a live filesystem. A failed supervisor cleanup must be
	# reconciled by reboot/bootstrap recovery before another mtd3 write.
	grep -q '^/dev/mtdblock3 ' /proc/mounts && exit 2
	[ -f /run/transfer.bin ] || exit 2
	[ "$(wc -c </run/transfer.bin)" -eq 8126464 ] || exit 2
	actual_sha256=$(sha256sum /run/transfer.bin)
	actual_sha256=${actual_sha256%% *}
	[ "$actual_sha256" = "$expected_sha256" ] || exit 2
	dd if=/run/transfer.bin bs=1 skip=8125952 count=512 2>/dev/null |
		tr -d '\377' >/run/transfer.footer
	[ "$(sed -n '1p' /run/transfer.footer)" = DCS6100-MTD3-V1 ] || exit 2
	[ "$(sed -n '2p' /run/transfer.footer)" = target=DCS-6100LHV2-A1 ] || exit 2
	[ "$(sed -n '5p' /run/transfer.footer)" = image_size=8126464 ] || exit 2
	[ "$(sed -n '6p' /run/transfer.footer)" = END ] || exit 2
	[ "$(wc -l </run/transfer.footer)" -eq 6 ] || exit 2
	payload_size=$(sed -n 's/^payload_size=//p' /run/transfer.footer)
	payload_sha256=$(sed -n 's/^payload_sha256=//p' /run/transfer.footer)
	case "$payload_size" in ''|*[!0-9]*) exit 2 ;; esac
	[ "$payload_size" -ge 4096 ] && [ "$payload_size" -le 8125952 ] || exit 2
	[ "${#payload_sha256}" -eq 64 ] || exit 2
	case "$payload_sha256" in *[!0-9a-f]*) exit 2 ;; esac
	actual_payload_sha256=$(head -c "$payload_size" /run/transfer.bin | sha256sum)
	actual_payload_sha256=${actual_payload_sha256%% *}
	[ "$actual_payload_sha256" = "$payload_sha256" ] || exit 2
	[ "$(head -c 4 /run/transfer.bin)" = hsqs ] || exit 2
	led amber
	/usr/sbin/flashcp -v /run/transfer.bin /dev/mtd3 >/run/flashcp.log 2>&1
	readback_sha256=$(sha256sum /dev/mtd3)
	readback_sha256=${readback_sha256%% *}
	[ "$readback_sha256" = "$expected_sha256" ] || exit 2
	printf '%s\n' "$expected_sha256" >/run/installed-mtd3.sha256
	led green
	printf '%s\n' installed
	;;
activate-mtd3)
	expected_sha256=${2:-}
	[ "${#expected_sha256}" -eq 64 ] || exit 2
	case "$expected_sha256" in *[!0-9a-f]*) exit 2 ;; esac
	# Reconstruct activation authority from persistent NOR state.  The /run
	# receipt is only a cache and may legitimately disappear after power loss.
	personal_mtd3_identity || exit 2
	[ "$personal_image_sha256" = "$expected_sha256" ] || exit 2
	printf '%s\n' "$expected_sha256" >/run/installed-mtd3.sha256
	# Activation keeps the recovery tmpfs mounted while it transitions into the
	# installed root. Drop the verified 8 MiB transfer copy before normal
	# services start so it cannot starve TISP's contiguous Linux allocation.
	rm -f /run/transfer.bin /run/transfer.footer
	sync
	: >/run/transition.activate
	printf '%s\n' activating
	;;
install-recovery)
	expected_sha256=${2:-}
	[ "${#expected_sha256}" -eq 64 ] || exit 2
	case "$expected_sha256" in *[!0-9a-f]*) exit 2 ;; esac
	[ -f /run/transfer.bin ] || exit 2
	transfer_size=$(wc -c </run/transfer.bin)
	[ "$transfer_size" -ge 4160 ] && [ "$transfer_size" -le 4538432 ] || exit 2
	actual_sha256=$(sha256sum /run/transfer.bin)
	actual_sha256=${actual_sha256%% *}
	[ "$actual_sha256" = "$expected_sha256" ] || exit 2
	[ -b /dev/mmcblk0p1 ] || exit 2

	mounted=0
	staging=/media/setup/T4DEV.NEW
	cleanup_install() {
		rm -f "$staging" 2>/dev/null || true
		[ "$mounted" -eq 0 ] || umount /media/setup 2>/dev/null || true
	}
	trap cleanup_install EXIT HUP INT TERM
	mount -t vfat -o rw,sync,nosuid,nodev,noexec /dev/mmcblk0p1 /media/setup
	mounted=1
	rm -f "$staging"
	cat /run/transfer.bin >"$staging"
	sync
	[ "$(wc -c <"$staging")" -eq "$transfer_size" ] || exit 2
	staged_sha256=$(sha256sum "$staging")
	staged_sha256=${staged_sha256%% *}
	[ "$staged_sha256" = "$expected_sha256" ] || exit 2

	target=/media/setup/T4DEV.UIM
	if [ -f "$target" ]; then
		current_sha256=$(sha256sum "$target")
		current_sha256=${current_sha256%% *}
		if [ "$current_sha256" = "$expected_sha256" ]; then
			rm -f "$staging"
		else
			backup_suffix=$(printf '%s' "$current_sha256" | cut -c 1-5)
			backup="/media/setup/T4D${backup_suffix}.BAK"
			if [ -e "$backup" ]; then
				backup_sha256=$(sha256sum "$backup")
				backup_sha256=${backup_sha256%% *}
				[ "$backup_sha256" = "$current_sha256" ] || exit 2
				rm -f "$target"
			else
				mv "$target" "$backup"
			fi
			mv "$staging" "$target"
		fi
	else
		mv "$staging" "$target"
	fi
	sync
	installed_sha256=$(sha256sum "$target")
	installed_sha256=${installed_sha256%% *}
	[ "$installed_sha256" = "$expected_sha256" ] || exit 2
	umount /media/setup
	mounted=0
	trap - EXIT HUP INT TERM
	printf '%s\n' installed
	;;
reboot)
	sync
	printf '%s\n' rebooting
	reboot -f
	;;
provision)
	# Credentials arrive on stdin so neither value is exposed in argv or logs.
	IFS= read -r ssid_hex || exit 2
	IFS= read -r psk_hex || exit 2
	[ "${#ssid_hex}" -ge 2 ] && [ "${#ssid_hex}" -le 64 ] || exit 2
	[ $(( ${#ssid_hex} % 2 )) -eq 0 ] || exit 2
	case "$ssid_hex" in *[!0-9a-fA-F]*) exit 2 ;; esac
	[ "${#psk_hex}" -eq 64 ] || exit 2
	case "$psk_hex" in *[!0-9a-fA-F]*) exit 2 ;; esac
	cat >/run/station.conf <<EOF
ctrl_interface=/run/wpa_supplicant
update_config=0
network={
	ssid=$ssid_hex
	psk=$psk_hex
	key_mgmt=WPA-PSK
	proto=RSN
	pairwise=CCMP
	group=CCMP
}
EOF
	chmod 0600 /run/station.conf
	: >/run/transition.station
	printf '%s\n' accepted
	;;
*)
echo "usage: recoveryctl status | inspect-nor | receive SIZE SHA256 | send SHA256 | vendor-export | install-mtd3 SHA256 | activate-mtd3 SHA256 | install-recovery SHA256 | reboot | provision < credentials" >&2
	exit 2
	;;
esac
