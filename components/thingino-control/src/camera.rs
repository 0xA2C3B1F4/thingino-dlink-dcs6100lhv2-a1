use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::net::{TcpStream, ToSocketAddrs, UdpSocket};
#[cfg(target_os = "linux")]
use std::os::raw::{c_char, c_int, c_void};
use std::os::unix::fs::{FileTypeExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
#[cfg(test)]
use std::sync::Arc;
use std::sync::Mutex;
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU32, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::decode::percent_decode_form;
use crate::json::{self, Value};
use crate::{BackendError, BackendResponse};

mod actions;
mod config;
mod diagnostics;
pub(crate) mod ha;
mod host;
mod maintenance;
pub(crate) mod motion_datagram;
pub(crate) mod motion_events;
pub(crate) mod motion_state;
mod network;
mod platform;
mod runtime;
mod storage;

use maintenance::*;
use network::*;
use platform::*;
#[cfg(feature = "raptor-backend")]
pub(crate) use storage::RaptorStorageWorkerError;
pub(crate) use storage::base64_encode;
use storage::*;

#[derive(Clone, Debug)]
pub struct CameraPaths {
    pub prudynt_socket: PathBuf,
    pub prudynt_config: PathBuf,
    pub thingino_config: PathBuf,
    pub onvif_config: PathBuf,
    pub timelapse_config: PathBuf,
    pub send2_config: PathBuf,
    pub imaging_state: PathBuf,
    pub imaging_ctl: PathBuf,
    pub daynight_mode: PathBuf,
    pub daynight_brightness: PathBuf,
    pub daynight_sensors: PathBuf,
    pub recorder_ch0_active: PathBuf,
    pub recorder_ch1_active: PathBuf,
    pub recorder_ctl: PathBuf,
    pub motion_active: PathBuf,
    pub motion_detected: PathBuf,
    pub motion_alarm: PathBuf,
    pub motion_event_socket: PathBuf,
    pub motion_clip_manifest: PathBuf,
    pub audio_output: PathBuf,
    pub microphone_active: PathBuf,
    pub speaker_active: PathBuf,
    pub prudynt_pid: PathBuf,
    pub daynight_pid: PathBuf,
    pub media_ready: PathBuf,
    pub privacy_active: PathBuf,
    pub prudynt_executable: PathBuf,
    pub daynight_executable: PathBuf,
    pub streaming_init: PathBuf,
    pub hostname: PathBuf,
    pub uptime: PathBuf,
    pub loadavg: PathBuf,
    pub meminfo: PathBuf,
    pub fib_trie: PathBuf,
    pub proc_net_route: PathBuf,
    pub proc_net_wireless: PathBuf,
    pub network_dir: PathBuf,
    pub resolv_config: PathBuf,
    pub wpa_config: PathBuf,
    pub wpa_control: PathBuf,
    pub sys_class_net: PathBuf,
    pub timezone: PathBuf,
    pub tz: PathBuf,
    pub timezone_catalog: PathBuf,
    pub ntp_config: PathBuf,
    pub sync_status: PathBuf,
    pub sys_class_gpio: PathBuf,
    pub sys_class_leds: PathBuf,
    pub ircut_state: PathBuf,
    pub proc_mounts: PathBuf,
    pub overlay: PathBuf,
    pub extras: PathBuf,
    pub sensor_name: PathBuf,
    pub os_release: PathBuf,
    pub proc_modules: PathBuf,
    pub proc_root: PathBuf,
    pub proc_tcp: PathBuf,
    pub proc_udp: PathBuf,
    pub proc_unix: PathBuf,
    pub var_log_messages: PathBuf,
    pub log_main: PathBuf,
    pub crontab: PathBuf,
    pub sys_bus_mmc: PathBuf,
    pub sys_class_block: PathBuf,
    pub storage_worker_socket: PathBuf,
    pub storage_mountpoint: PathBuf,
    pub media_roots: Vec<PathBuf>,
}

impl Default for CameraPaths {
    fn default() -> Self {
        Self {
            prudynt_socket: "/run/prudynt/prudynt.sock".into(),
            prudynt_config: "/etc/prudynt.json".into(),
            thingino_config: "/etc/thingino.json".into(),
            onvif_config: "/etc/onvif.json".into(),
            timelapse_config: "/etc/timelapse.json".into(),
            send2_config: "/etc/send2.json".into(),
            imaging_state: "/run/prudynt/imaging.json".into(),
            imaging_ctl: "/run/prudynt/imagingctl".into(),
            daynight_mode: "/run/thingino/daynight_mode".into(),
            daynight_brightness: "/run/thingino/daynight_brightness".into(),
            daynight_sensors: "/run/thingino/daynight_sensors".into(),
            recorder_ch0_active: "/run/prudynt/mp4ctl-ch0.active".into(),
            recorder_ch1_active: "/run/prudynt/mp4ctl-ch1.active".into(),
            recorder_ctl: "/run/prudynt/mp4ctl".into(),
            motion_active: "/run/prudynt/motion.active".into(),
            motion_detected: "/run/prudynt/motion_detected.active".into(),
            motion_alarm: "/run/motion/motion_alarm".into(),
            motion_event_socket: "/run/thingino-control/motion.sock".into(),
            motion_clip_manifest: "/run/thingino-control/motion-clips-v1.json".into(),
            audio_output: "/run/prudynt/audio_out".into(),
            microphone_active: "/run/prudynt/mic.active".into(),
            speaker_active: "/run/prudynt/spk.active".into(),
            prudynt_pid: "/run/prudynt.pid".into(),
            daynight_pid: "/run/daynightd.pid".into(),
            media_ready: "/run/prudynt-dlink-media.ready".into(),
            privacy_active: "/run/prudynt/privacy.active".into(),
            prudynt_executable: "/usr/bin/prudynt".into(),
            daynight_executable: "/usr/bin/daynightd".into(),
            streaming_init: "/etc/init.d/S12prudynt".into(),
            hostname: "/etc/hostname".into(),
            uptime: "/proc/uptime".into(),
            loadavg: "/proc/loadavg".into(),
            meminfo: "/proc/meminfo".into(),
            fib_trie: "/proc/net/fib_trie".into(),
            proc_net_route: "/proc/net/route".into(),
            proc_net_wireless: "/proc/net/wireless".into(),
            network_dir: "/etc/network/interfaces.d".into(),
            resolv_config: "/etc/default/resolv.conf".into(),
            wpa_config: "/etc/wpa_supplicant.conf".into(),
            wpa_control: "/run/wpa_supplicant/wlan0".into(),
            sys_class_net: "/sys/class/net".into(),
            timezone: "/etc/timezone".into(),
            tz: "/etc/TZ".into(),
            timezone_catalog: "/usr/share/tz.json".into(),
            ntp_config: "/etc/default/ntp.conf".into(),
            sync_status: "/run/sync_status".into(),
            sys_class_gpio: "/sys/class/gpio".into(),
            sys_class_leds: "/sys/class/leds".into(),
            ircut_state: "/run/thingino/ircut_mode".into(),
            proc_mounts: "/proc/mounts".into(),
            overlay: "/overlay".into(),
            extras: "/opt".into(),
            sensor_name: "/proc/jz/sensor/name".into(),
            os_release: "/etc/os-release".into(),
            proc_modules: "/proc/modules".into(),
            proc_root: "/proc".into(),
            proc_tcp: "/proc/net/tcp".into(),
            proc_udp: "/proc/net/udp".into(),
            proc_unix: "/proc/net/unix".into(),
            var_log_messages: "/var/log/messages".into(),
            log_main: "/dev/log_main".into(),
            crontab: "/etc/cron/crontabs/root".into(),
            sys_bus_mmc: "/sys/bus/mmc/devices".into(),
            sys_class_block: "/sys/class/block".into(),
            storage_worker_socket: "/run/thingino-control/storage-v1.sock".into(),
            storage_mountpoint: "/mnt/mmcblk0p1".into(),
            media_roots: vec!["/mnt".into(), "/media".into()],
        }
    }
}

#[doc(hidden)]
pub struct HostBackend {
    pub(in crate::camera) paths: CameraPaths,
    config_lock: Mutex<()>,
    timezone_lock: Mutex<()>,
}

impl HostBackend {
    #[cfg_attr(not(feature = "raptor-backend"), allow(dead_code))]
    pub(crate) fn new(paths: CameraPaths) -> Self {
        Self {
            paths,
            config_lock: Mutex::new(()),
            timezone_lock: Mutex::new(()),
        }
    }
}

fn form_param(body: &[u8], key: &str) -> Option<String> {
    let body = std::str::from_utf8(body).ok()?;
    body.split('&').find_map(|field| {
        let (name, value) = field.split_once('=')?;
        (name == key).then(|| percent_decode_form(value)).flatten()
    })
}

fn reset_link(id: &str, title: &str, description: &str) -> Value {
    object([
        ("id", Value::String(id.to_owned())),
        ("title", Value::String(title.to_owned())),
        ("description_html", Value::String(description.to_owned())),
        (
            "cta",
            object([
                ("type", Value::String("link".to_owned())),
                (
                    "href",
                    Value::String(format!("/firmware-reset.html?action={id}")),
                ),
                ("text", Value::String(title.to_owned())),
                ("variant", Value::String("danger".to_owned())),
            ]),
        ),
    ])
}

fn json_response(value: Value) -> Result<BackendResponse, BackendError> {
    let mut body = value.to_json().into_bytes();
    body.push(b'\n');
    Ok(BackendResponse::json(body))
}

fn secret_field(name: &str) -> bool {
    matches!(
        name,
        "password" | "token" | "client_secret" | "refresh_token" | "api_key" | "secret"
    )
}

fn mask_secret_fields(value: &mut Value) {
    match value {
        Value::Object(fields) => {
            let names = fields.keys().cloned().collect::<Vec<_>>();
            for name in names {
                if secret_field(&name) {
                    let is_set = fields.get(&name).is_some_and(|value| match value {
                        Value::String(value) => !value.is_empty(),
                        Value::Null => false,
                        _ => true,
                    });
                    fields.insert(name.clone(), Value::Null);
                    fields.insert(format!("{name}_set"), Value::Bool(is_set));
                } else if let Some(value) = fields.get_mut(&name) {
                    mask_secret_fields(value);
                }
            }
        }
        Value::Array(values) => {
            for value in values {
                mask_secret_fields(value);
            }
        }
        _ => {}
    }
}

fn remove_unchanged_secret_fields(value: &mut Value) {
    match value {
        Value::Object(fields) => {
            fields.retain(|name, value| {
                !name.ends_with("_set")
                    && (!secret_field(name)
                        || matches!(value, Value::String(value) if !value.is_empty()))
            });
            for value in fields.values_mut() {
                remove_unchanged_secret_fields(value);
            }
        }
        Value::Array(values) => {
            for value in values {
                remove_unchanged_secret_fields(value);
            }
        }
        _ => {}
    }
}

pub(in crate::camera) fn value_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(value) => value.parse().ok(),
        Value::String(value) => value.parse().ok(),
        _ => None,
    }
}

fn parse_form(body: &[u8]) -> Result<BTreeMap<String, String>, BackendError> {
    let text = std::str::from_utf8(body).map_err(|_| BackendError::Protocol)?;
    let mut fields = BTreeMap::new();
    for pair in text.split('&') {
        let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
        let key = url_decode(key)?;
        let value = url_decode(value)?;
        if key.is_empty() || fields.insert(key, value).is_some() {
            return Err(BackendError::Protocol);
        }
    }
    Ok(fields)
}

fn url_decode(value: &str) -> Result<String, BackendError> {
    percent_decode_form(value).ok_or(BackendError::Protocol)
}

fn form_bool(form: &BTreeMap<String, String>, key: &str) -> bool {
    form.get(key)
        .is_some_and(|value| matches!(value.as_str(), "1" | "true" | "on" | "yes"))
}

fn raw_or(value: &Value, path: &str, fallback: &str) -> String {
    value
        .get_path(path)
        .map(Value::to_json)
        .unwrap_or_else(|| fallback.to_owned())
}

fn object<const N: usize>(entries: [(&str, Value); N]) -> Value {
    Value::Object(
        entries
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect::<BTreeMap<_, _>>(),
    )
}

fn number(value: u64) -> Value {
    Value::Number(value.to_string())
}

fn bool_json(value: bool) -> &'static str {
    if value { "true" } else { "false" }
}

#[cfg(test)]
#[path = "camera_tests.rs"]
mod tests;

/// Query the filesystem of an already held directory, not a replaceable path.
#[cfg(feature = "raptor-backend")]
pub(crate) fn recording_filesystem_stats(fd: i32) -> Option<(u64, u64, u64)> {
    let stats = filesystem_stats(Path::new(&format!("/proc/self/fd/{fd}")))?;
    Some((stats.total, stats.used, stats.free))
}
