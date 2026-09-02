use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{TcpStream, ToSocketAddrs, UdpSocket};
#[cfg(target_os = "linux")]
use std::os::raw::{c_char, c_int, c_void};
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::decode::percent_decode_form;
use crate::json::{self, Value};
use crate::{
    Backend, BackendError, BackendResponse, BackendRoute, DayNightMode, MAX_SNAPSHOT_BYTES,
};

mod actions;
mod api;
mod config;
mod diagnostics;
mod files;
mod ha;
mod maintenance;
mod motion;
mod motion_datagram;
pub(in crate::camera) mod motion_events;
mod motion_queue;
mod motion_state;
mod network;
mod platform;
mod prudynt;
mod runtime;
mod storage;

use ha::HaService;
use maintenance::*;
use network::*;
use platform::*;
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

pub struct PrudyntBackend {
    paths: CameraPaths,
    motion: Arc<motion::MotionService>,
    config_lock: Mutex<()>,
    daynight_history: Mutex<Vec<Value>>,
    audio_state: Mutex<AudioRuntimeState>,
    daynight_reapply: Mutex<Option<Instant>>,
    timezone_lock: Mutex<()>,
    storage_format: Mutex<StorageFormatState>,
    ha: Arc<HaService>,
}

impl PrudyntBackend {
    pub fn new(paths: CameraPaths) -> Self {
        Self::with_motion_sink(paths, Arc::new(motion_events::DisabledMotionEventSink))
    }

    pub(in crate::camera) fn with_motion_sink(
        paths: CameraPaths,
        sink: Arc<dyn motion_events::MotionEventSink>,
    ) -> Self {
        let audio_state = configured_audio_state(&paths);
        let motion = motion::MotionService::with_sink(paths.clone(), sink);
        Self {
            paths,
            motion,
            config_lock: Mutex::new(()),
            daynight_history: Mutex::new(Vec::new()),
            audio_state: Mutex::new(audio_state),
            daynight_reapply: Mutex::new(None),
            timezone_lock: Mutex::new(()),
            storage_format: Mutex::new(StorageFormatState::default()),
            ha: Arc::new(HaService::new()),
        }
    }

    pub fn start_maintenance(self: &Arc<Self>) {
        let _ = self.motion.start();
        let backend = Arc::clone(self);
        thread::Builder::new()
            .name("control-maint".to_owned())
            .spawn(move || maintenance_loop(backend))
            .expect("Thingino Control maintenance thread must start");
    }

    // The HA adapter consumes this seam in its separate integration commit.
    #[allow(dead_code)]
    pub(in crate::camera) fn set_motion_event_sink(
        &self,
        sink: Arc<dyn motion_events::MotionEventSink>,
    ) {
        self.motion.set_sink(sink);
    }

    pub fn start_ha(self: &Arc<Self>) {
        self.ha.start(Arc::clone(self));
        let sink: Arc<dyn motion_events::MotionEventSink> = self.ha.clone();
        self.set_motion_event_sink(sink);
    }

    pub fn shutdown_ha(&self) -> io::Result<()> {
        self.ha.shutdown()
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct AudioRuntimeState {
    microphone: bool,
    speaker: bool,
}

fn configured_audio_state(paths: &CameraPaths) -> AudioRuntimeState {
    let document = read_bounded(&paths.prudynt_config, FILE_LIMIT)
        .ok()
        .and_then(|raw| json::parse(&raw).ok());
    AudioRuntimeState {
        microphone: document
            .as_ref()
            .and_then(|value| value.get_path("audio.mic_enabled"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
        speaker: document
            .as_ref()
            .and_then(|value| value.get_path("audio.spk_enabled"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
    }
}

fn daynight_config(original: &[u8], mode: DayNightMode) -> Result<Vec<u8>, BackendError> {
    let mut config = json::parse(original).map_err(|_| BackendError::Protocol)?;
    config
        .set_path("daynight.enabled", Value::Bool(mode == DayNightMode::Auto))
        .map_err(|_| BackendError::Protocol)?;
    config
        .set_path(
            "daynight.force_mode",
            Value::String(match mode {
                DayNightMode::Auto => String::new(),
                DayNightMode::Day => "day".to_owned(),
                DayNightMode::Night => "night".to_owned(),
            }),
        )
        .map_err(|_| BackendError::Protocol)?;
    let mut updated = config.to_json().into_bytes();
    updated.push(b'\n');
    Ok(updated)
}

fn serialized_mutation(method: &str, target: &str) -> bool {
    if !matches!(method, "POST" | "PUT") {
        return false;
    }
    target.starts_with("/api/v1/config/")
        || target == "/api/v1/prudynt"
        || target == "/api/v1/imaging"
        || target == "/api/v1/recorder"
        || target == "/api/v1/services/send/config"
        || target.starts_with("/api/v1/files?")
        || target.starts_with("/api/v1/files/text?")
        || target == "/api/v1/actions/factory-reset"
}

impl Backend for PrudyntBackend {
    fn request(
        &self,
        route: BackendRoute,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        match route {
            BackendRoute::Health => self.health(),
            BackendRoute::RuntimeMedia => self.runtime_media(),
            BackendRoute::Config => self.config(),
            BackendRoute::Snapshot(stream_id) => self.snapshot(stream_id, deadline),
            BackendRoute::DayNight(mode) => self.daynight(mode, deadline),
        }
    }

    fn api_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        let _mutation_guard = if serialized_mutation(method, target) {
            match self.config_lock.lock() {
                Ok(guard) => Some(guard),
                Err(_) => return Some(Err(BackendError::Unavailable)),
            }
        } else {
            None
        };
        api::dispatch(self, method, target, body, deadline)
    }

    fn authorize_media(&self, target: &str) -> bool {
        if matches!(
            target,
            "/api/v1/actions/snapshot?stream_id=0"
                | "/api/v1/actions/snapshot?stream_id=1"
                | "/onvif/image.cgi"
                | "/onvif/image1.cgi"
        ) {
            return true;
        }
        if target == "/media/v1/osd-sei" {
            return true;
        }
        if target.starts_with("/media/v1/mjpeg?") {
            return query_param(target, "stream")
                .ok()
                .flatten()
                .is_some_and(|value| matches!(value.as_str(), "0" | "1"));
        }
        if !target.starts_with("/media/v1/file?") {
            return false;
        }
        let Some(path) = query_param(target, "path").ok().flatten() else {
            return false;
        };
        let Ok(path) = validated_absolute_path(&path) else {
            return false;
        };
        let Ok(metadata) = path.symlink_metadata() else {
            return false;
        };
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return false;
        }
        fs::canonicalize(path)
            .map(|path| path_is_within_roots(&path, &self.paths.media_roots))
            .unwrap_or(false)
    }
}

fn query_param(target: &str, key: &str) -> Result<Option<String>, BackendError> {
    let Some((_, query)) = target.split_once('?') else {
        return Ok(None);
    };
    let mut found = None;
    for pair in query.split('&') {
        let (raw_key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
        if url_decode(raw_key)? == key {
            if found.is_some() {
                return Err(BackendError::Protocol);
            }
            found = Some(url_decode(raw_value)?);
        }
    }
    Ok(found)
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

fn action_ok() -> BackendResponse {
    BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec())
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

fn retain_changed_fields(update: &mut Value, current: Option<&Value>) -> bool {
    match (update, current) {
        (Value::Object(update), Some(Value::Object(current))) => {
            update.retain(|name, value| retain_changed_fields(value, current.get(name)));
            !update.is_empty()
        }
        (update, Some(current)) => update != current,
        (_, None) => true,
    }
}

fn value_or(document: &Value, path: &str, fallback: Value) -> Value {
    document.get_path(path).cloned().unwrap_or(fallback)
}

fn read_json_or_empty(path: &Path) -> Result<Value, BackendError> {
    match read_bounded(path, FILE_LIMIT) {
        Ok(bytes) => json::parse(&bytes).map_err(|_| BackendError::Protocol),
        Err(BackendError::Unavailable) => Ok(Value::Object(BTreeMap::new())),
        Err(error) => Err(error),
    }
}

fn json_number(document: &Value, path: &str) -> Result<f64, BackendError> {
    match document.get_path(path) {
        Some(Value::Number(value)) => value.parse().map_err(|_| BackendError::Protocol),
        _ => Err(BackendError::Protocol),
    }
}

fn json_u64(document: &Value, path: &str) -> Result<u64, BackendError> {
    let direct = document.get_path(path);
    let nested = document.get_path(&format!("{path}.pin"));
    match direct
        .filter(|value| matches!(value, Value::Number(_) | Value::String(_)))
        .or(nested)
    {
        Some(Value::Number(value)) => value.parse().map_err(|_| BackendError::Protocol),
        Some(Value::String(value)) => value.parse().map_err(|_| BackendError::Protocol),
        _ => Err(BackendError::Protocol),
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

fn form_u64(form: &BTreeMap<String, String>, key: &str, minimum: u64) -> Result<u64, BackendError> {
    form.get(key)
        .ok_or(BackendError::Protocol)?
        .parse::<u64>()
        .ok()
        .filter(|value| *value >= minimum)
        .ok_or(BackendError::Protocol)
}

fn safe_path_fragment(value: &str) -> bool {
    value.len() <= 512
        && !value.contains(['\0', '\r', '\n'])
        && !Path::new(value)
            .components()
            .any(|component| component == std::path::Component::ParentDir)
}

fn raw_or(value: &Value, path: &str, fallback: &str) -> String {
    value
        .get_path(path)
        .map(Value::to_json)
        .unwrap_or_else(|| fallback.to_owned())
}

fn string_or_null(value: &Value, path: &str) -> String {
    match value.get_path(path) {
        Some(Value::String(text)) if !text.is_empty() => Value::String(text.clone()).to_json(),
        _ => "null".to_owned(),
    }
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

fn decimal(value: f64) -> Value {
    Value::Number(format!("{value:.2}"))
}

fn stream_config(config: &Value, stream_id: u8) -> String {
    let prefix = format!("stream{stream_id}");
    format!(
        "{{\"id\":{stream_id},\"enabled\":{},\"audio_enabled\":{},\"width\":{},\"height\":{},\"fps\":{},\"bitrate\":{},\"format\":{},\"mode\":{}}}",
        raw_or(config, &format!("{prefix}.enabled"), "false"),
        raw_or(config, &format!("{prefix}.audio_enabled"), "false"),
        raw_or(config, &format!("{prefix}.width"), "null"),
        raw_or(config, &format!("{prefix}.height"), "null"),
        raw_or(config, &format!("{prefix}.fps"), "null"),
        raw_or(config, &format!("{prefix}.bitrate"), "null"),
        raw_or(config, &format!("{prefix}.format"), "null"),
        raw_or(config, &format!("{prefix}.mode"), "null"),
    )
}

fn bool_json(value: bool) -> &'static str {
    if value { "true" } else { "false" }
}

#[cfg(test)]
#[path = "camera_tests.rs"]
mod tests;
