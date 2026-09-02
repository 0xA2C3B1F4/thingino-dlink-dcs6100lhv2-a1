#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

MTD3_SIZE=8126464
MTD3_FOOTER_OFFSET=8125952
MTD3_FOOTER_SIZE=512
THINGINO_HEALTH_TIMEOUT=90
THINGINO_ROOT=/mnt/thingino
THINGINO_ETC=/run/thingino-etc

fail() {
	echo "RECOVERY_AP FAIL $1"
	blink_failure "$1"
}

led() {
	color=$1
	green=0
	red=0
	[ "$color" = green ] && green=1
	[ "$color" = red ] && red=1
	[ "$color" = amber ] && green=1 && red=1
	[ -e /sys/class/leds/led_g/brightness ] && echo "$green" >/sys/class/leds/led_g/brightness
	[ -e /sys/class/leds/led_r/brightness ] && echo "$red" >/sys/class/leds/led_r/brightness
	# A board may expose neither LED through the LED class.  Do not let the
	# final optional probe become this function's status under `set -e`.
	return 0
}

blink_failure() {
	reason=$1
	case "$reason" in
	proc|sysfs|dev|run|root_tmpfs|tmp_tmpfs|devpts) code=1 ;;
	mtd*) code=2 ;;
	dtrng_module|entropy_seed|entropy_count|entropy_low|entropy_reseed) code=3 ;;
	missing_*|copy_*|setup_media*|mmc_module|ap_psk_*|authorized_key*|hostname) code=4 ;;
	wifi_power*|wifi_module) code=5 ;;
	wlan0_missing|hostapd) code=6 ;;
	ap_address|udhcpd|mdnsd*|dropbear) code=7 ;;
	*) code=8 ;;
	esac
	while :; do
		led red
		sleep 2
		led off
		sleep 2
		pulse=0
		while [ "$pulse" -lt "$code" ]; do
			led amber
			sleep 1
			led off
			sleep 1
			pulse=$((pulse + 1))
		done
		led off
		sleep 3
	done
}

reset_pressed() {
	[ "$(cat /sys/class/gpio/gpio60/value 2>/dev/null || echo 1)" = 0 ]
}

stop_wifi_processes() {
	killall udhcpc udhcpd hostapd wpa_supplicant mdnsd 2>/dev/null || true
	sleep 1
}

start_mdnsd() {
	rm -f /run/mdnsd.pid
	/usr/sbin/mdnsd -H "$setup_host" -i wlan0 -s /etc/mdns.d/recovery.service || fail mdnsd
	sleep 1
	mdns_pid=$(cat /run/mdnsd.pid) || fail mdnsd_pid
	kill -0 "$mdns_pid" 2>/dev/null || fail mdnsd
}

start_dropbear() {
	killall dropbear 2>/dev/null || true
	/usr/sbin/dropbear -E -F -s -g -j -k -p 22 -r /run/setup/HOST.KEY &
	dropbear_pid=$!
	sleep 1
	kill -0 "$dropbear_pid" 2>/dev/null || fail dropbear
}

start_ap() {
	stop_wifi_processes
	wlan_attempt=0
	while [ ! -e /sys/class/net/wlan0 ] && [ "$wlan_attempt" -lt 15 ]; do
		sleep 1
		wlan_attempt=$((wlan_attempt + 1))
	done
	[ -e /sys/class/net/wlan0 ] || fail wlan0_missing
	ifconfig wlan0 down 2>/dev/null || true
	hostapd_attempt=0
	while [ "$hostapd_attempt" -lt 3 ]; do
		ifconfig wlan0 up || {
			hostapd_attempt=$((hostapd_attempt + 1))
			sleep 2
			continue
		}
		/usr/sbin/entropy-seed || fail entropy_reseed
		if /usr/sbin/hostapd -B /run/setup/hostapd.conf; then
			break
		fi
		killall hostapd 2>/dev/null || true
		ifconfig wlan0 down 2>/dev/null || true
		hostapd_attempt=$((hostapd_attempt + 1))
		sleep 2
	done
	[ "$hostapd_attempt" -lt 3 ] || fail hostapd
	ifconfig wlan0 192.168.88.1 netmask 255.255.255.0 up || fail ap_address
	udhcpd /run/setup/udhcpd.conf || fail udhcpd
	start_mdnsd
	start_dropbear
	echo ap >/run/recovery.state
	led green
	echo "RECOVERY_AP READY $setup_ssid 192.168.88.1"
}

start_station() {
	stop_wifi_processes
	led amber
	ifconfig wlan0 0.0.0.0 down 2>/dev/null || true
	ifconfig wlan0 up || return 1
	/usr/sbin/wpa_supplicant -B -D nl80211 -i wlan0 -c /run/station.conf || return 1
	rm -f /run/station.bound
	udhcpc -n -q -t 5 -T 3 -i wlan0 -s /run/recovery-udhcpc || return 1
	[ -e /run/station.bound ] || return 1
	start_mdnsd
	start_dropbear
	echo station >/run/recovery.state
	led green
	echo "RECOVERY_AP STATION_READY ${setup_host}.local"
}

read_mtd3_footer() {
	rm -f /run/mtd3.footer
	dd if=/dev/mtd3 bs=1 skip="$MTD3_FOOTER_OFFSET" count="$MTD3_FOOTER_SIZE" 2>/dev/null |
		tr -d '\377' >/run/mtd3.footer || return 1
	[ "$(sed -n '1p' /run/mtd3.footer)" = DCS6100-MTD3-V1 ] || return 1
	[ "$(sed -n '2p' /run/mtd3.footer)" = target=DCS-6100LHV2-A1 ] || return 1
	[ "$(sed -n '5p' /run/mtd3.footer)" = image_size="$MTD3_SIZE" ] || return 1
	[ "$(sed -n '6p' /run/mtd3.footer)" = END ] || return 1
	[ "$(wc -l </run/mtd3.footer)" -eq 6 ] || return 1
	payload_size=$(sed -n 's/^payload_size=//p' /run/mtd3.footer)
	payload_sha256=$(sed -n 's/^payload_sha256=//p' /run/mtd3.footer)
	case "$payload_size" in ''|*[!0-9]*) return 1 ;; esac
	[ "$payload_size" -ge 4096 ] && [ "$payload_size" -le "$MTD3_FOOTER_OFFSET" ] || return 1
	[ "${#payload_sha256}" -eq 64 ] || return 1
	case "$payload_sha256" in *[!0-9a-f]*) return 1 ;; esac
	actual_payload_sha256=$(head -c "$payload_size" /dev/mtd3 | sha256sum)
	actual_payload_sha256=${actual_payload_sha256%% *}
	[ "$actual_payload_sha256" = "$payload_sha256" ] || return 1
}

thingino_mounts_present() {
	grep -q " $THINGINO_ROOT" /proc/mounts
}

kill_thingino_processes() {
	signal=$1
	for process_dir in /proc/[0-9]*; do
		process_pid=${process_dir#/proc/}
		[ "$process_pid" = "$$" ] && continue
		process_root=$(/bin/readlink "$process_dir/root" 2>/dev/null || true)
		process_cwd=$(/bin/readlink "$process_dir/cwd" 2>/dev/null || true)
		targeted=0
		case "$process_root" in
		"$THINGINO_ROOT"|"$THINGINO_ROOT"/*) targeted=1 ;;
		esac
		case "$process_cwd" in
		"$THINGINO_ROOT"|"$THINGINO_ROOT"/*) targeted=1 ;;
		esac
		[ "$targeted" -eq 0 ] || kill "-$signal" "$process_pid" 2>/dev/null || true
	done
}

unmount_thingino() {
	attempt=0
	while [ "$attempt" -lt 3 ]; do
		# Unmount children before their bind-mounted parent.  This also recovers
		# older personal images that mounted their own tmpfs at /dev/shm.
		for path in dev/shm etc run tmp sys proc dev; do
			umount "$THINGINO_ROOT/$path" 2>/dev/null || true
		done
		umount "$THINGINO_ROOT" 2>/dev/null || true
		thingino_mounts_present || return 0
		kill_thingino_processes KILL
		sleep 1
		attempt=$((attempt + 1))
	done
	return 1
}

stop_thingino() {
	thingino_pid=${thingino_pid:-}
	[ -z "$thingino_pid" ] || kill "$thingino_pid" 2>/dev/null || true
	# Kill every process rooted in, or retaining a working directory under,
	# the supervised final image, including daemonized descendants.
	kill_thingino_processes TERM
	killall prudynt dropbear mdnsd wpa_supplicant udhcpc 2>/dev/null || true
	sleep 1
	kill_thingino_processes KILL
	if ! unmount_thingino; then
		: >/run/thingino-cleanup.failed
		return 0
	fi
	rm -f /run/thingino-cleanup.failed
	rm -rf "$THINGINO_ETC"
	rm -f /run/thingino-health
}

run_thingino() {
	# A retry must derive its state from the actual mounts, not from an old
	# phase marker. Refuse a new mount until a previous attempt is fully gone.
	if thingino_mounts_present; then
		stop_thingino
	fi
	thingino_mounts_present && return 1
	rm -f /run/prudynt.log
	read_mtd3_footer || return 1
	mkdir -p "$THINGINO_ROOT"
	mount -t squashfs -o ro,nosuid,nodev /dev/mtdblock3 "$THINGINO_ROOT" || return 1
	[ -f "$THINGINO_ROOT/etc/dcs6100-personal-image.json" ] || {
		umount "$THINGINO_ROOT"
		return 1
	}
	# The personal SquashFS remains read-only in NOR. Thingino expects a
	# writable /etc for generated runtime state, so copy only that small tree
	# into volatile RAM and bind it over the immutable source.
	rm -rf "$THINGINO_ETC"
	mkdir -p "$THINGINO_ETC" || {
		stop_thingino
		return 1
	}
	cp -a "$THINGINO_ROOT/etc/." "$THINGINO_ETC/" || {
		stop_thingino
		return 1
	}
	mount -o bind "$THINGINO_ETC" "$THINGINO_ROOT/etc" || {
		stop_thingino
		return 1
	}
	for path in dev proc sys run tmp; do
		[ -d "$THINGINO_ROOT/$path" ] || {
			stop_thingino
			return 1
		}
		mount -o bind "/$path" "$THINGINO_ROOT/$path" || {
			stop_thingino
			return 1
		}
	done
	stop_wifi_processes
	ifconfig wlan0 0.0.0.0 down 2>/dev/null || true
	hostname "$setup_host" || return 1
	rm -f /run/thingino-health
	/usr/sbin/thingino-enter &
	thingino_pid=$!
	led amber
	waited=0
	while [ "$waited" -lt "$THINGINO_HEALTH_TIMEOUT" ]; do
		kill -0 "$thingino_pid" 2>/dev/null || {
			stop_thingino
			return 1
		}
		reset_pressed && {
			stop_thingino
			return 1
		}
		if [ -f /run/thingino-health ]; then
			echo thingino >/run/recovery.state
			led green
			echo "RECOVERY_AP THINGINO_READY ${setup_host}.local"
			return 0
		fi
		sleep 1
		waited=$((waited + 1))
	done
	stop_thingino
	return 1
}

mount -t proc proc /proc || fail proc
mount -t sysfs sysfs /sys || fail sysfs
mount -t devtmpfs devtmpfs /dev 2>/dev/null || [ -c /dev/null ] || fail dev
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs /run || fail run
mount -t tmpfs -o mode=0700,nosuid,nodev tmpfs /root || fail root_tmpfs
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp || fail tmp_tmpfs
mkdir -p /dev/pts /media /mnt/thingino /root/.ssh /run/setup
mount -t devpts devpts /dev/pts || fail devpts

cat >/run/recovery-udhcpc <<'EOF'
#!/bin/sh
set -eu
[ "${interface:-}" = wlan0 ] || exit 1
case "${1:-}" in
deconfig)
	ifconfig wlan0 0.0.0.0
	rm -f /run/station.bound
	;;
bound|renew)
	[ -n "${ip:-}" ] && [ -n "${subnet:-}" ] || exit 1
	ifconfig wlan0 "$ip" netmask "$subnet" up
	: >/run/station.bound
	;;
*) exit 1 ;;
esac
EOF
chmod 0700 /run/recovery-udhcpc

for index in 0 1 2 3 4 5; do
	flags=$(cat "/sys/class/mtd/mtd$index/flags") || fail "mtd${index}_missing"
	if [ "$index" -eq 3 ]; then
		[ $((flags & 0x400)) -ne 0 ] || fail mtd3_not_writeable
	else
		[ $((flags & 0x400)) -eq 0 ] || fail "mtd${index}_writeable"
	fi
done

insmod /modules/ingenic_t31_dtrng.ko || fail dtrng_module
/usr/sbin/entropy-seed || fail entropy_seed
entropy_available=$(cat /proc/sys/kernel/random/entropy_avail) || fail entropy_count
[ "$entropy_available" -ge 256 ] || fail entropy_low

if [ -d /etc/recovery-session ]; then
	for file in AP.PSK AUTHORIZED.KEY HOST.KEY; do
		[ -f "/etc/recovery-session/$file" ] || fail "missing_$file"
		cp "/etc/recovery-session/$file" "/run/setup/$file" || fail "copy_$file"
	done
else
	insmod /modules/jzmmc_v12.ko cd_gpio_pin=59 || fail mmc_module
	mmc_wait=0
	while [ ! -b /dev/mmcblk0p1 ] && [ "$mmc_wait" -lt 10 ]; do
		sleep 1
		mmc_wait=$((mmc_wait + 1))
	done
	[ -b /dev/mmcblk0p1 ] || fail setup_media_device
	mkdir -p /media/setup
	mount -t vfat -o ro,nosuid,nodev,noexec /dev/mmcblk0p1 /media/setup || fail setup_media
	for file in AP.PSK AUTHORIZED.KEY HOST.KEY; do
		[ -f "/media/setup/RECOVERY/$file" ] || fail "missing_$file"
		cp "/media/setup/RECOVERY/$file" "/run/setup/$file" || fail "copy_$file"
	done
	umount /media/setup || fail setup_media_umount
fi

ap_psk=$(tr -d '\r\n' </run/setup/AP.PSK)
[ "${#ap_psk}" -eq 64 ] || fail ap_psk_length
case "$ap_psk" in *[!0-9a-f]*) fail ap_psk_format ;; esac
setup_ssid="DCS6100-${ap_psk%${ap_psk#????????}}"
setup_host="dcs6100-${ap_psk%${ap_psk#????????}}"
hostname "$setup_host" || fail hostname
authorized_key=
extra_key=
{
	IFS= read -r authorized_key || fail authorized_key
	if IFS= read -r extra_key; then
		fail authorized_key_count
	fi
} </run/setup/AUTHORIZED.KEY
case "$authorized_key" in ssh-ed25519\ *) ;; *) fail authorized_key ;; esac
printf '%s %s\n' \
	'command="/usr/sbin/recoveryctl dispatch",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty' \
	"$authorized_key" >/root/.ssh/authorized_keys
chmod 0700 /root/.ssh
chmod 0600 /root/.ssh/authorized_keys /run/setup/HOST.KEY

cat >/run/setup/hostapd.conf <<EOF
interface=wlan0
driver=nl80211
ssid=$setup_ssid
hw_mode=g
channel=6
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_psk=$ap_psk
EOF
cat >/run/setup/udhcpd.conf <<EOF
start 192.168.88.2
end 192.168.88.2
interface wlan0
option subnet 255.255.255.0
option router 192.168.88.1
lease_file /run/udhcpd.leases
pidfile /run/udhcpd.pid
EOF

echo 57 >/sys/class/gpio/export 2>/dev/null || true
echo out >/sys/class/gpio/gpio57/direction || fail wifi_power_direction
echo 1 >/sys/class/gpio/gpio57/value || fail wifi_power
sleep 1
insmod /modules/8188fu.ko || fail wifi_module

echo 60 >/sys/class/gpio/export 2>/dev/null || true
echo in >/sys/class/gpio/gpio60/direction || fail reset_direction

if ! reset_pressed && run_thingino; then
	:
else
	start_ap
fi

reset_count=0
while :; do
	state=$(cat /run/recovery.state)
	if [ -e /run/transition.activate ]; then
		rm -f /run/transition.activate
		if ! run_thingino; then
			start_ap
			echo "RECOVERY_AP THINGINO_FAILED_RETURNED_TO_AP"
		fi
		state=$(cat /run/recovery.state)
	fi
	if [ "$state" != thingino ] && [ -e /run/transition.station ]; then
		mv /run/transition.station /run/transition.active
		if ! start_station; then
			rm -f /run/station.conf /run/transition.active
			start_ap
			echo "RECOVERY_AP STATION_FAILED_RETURNED_TO_AP"
		else
			rm -f /run/transition.active
		fi
	fi
	if reset_pressed; then
		reset_count=$((reset_count + 1))
	else
		reset_count=0
	fi
	if [ "$reset_count" -ge 3 ]; then
		[ "$state" != thingino ] || stop_thingino
		rm -f /run/station.conf /run/transition.station /run/transition.active
		start_ap
		echo "RECOVERY_AP RESET_RETURNED_TO_AP"
		reset_count=0
	fi
	if [ "$state" = thingino ] && ! kill -0 "$thingino_pid" 2>/dev/null; then
		stop_thingino
		start_ap
		echo "RECOVERY_AP THINGINO_EXITED_RETURNED_TO_AP"
	fi
	sleep 1
done
