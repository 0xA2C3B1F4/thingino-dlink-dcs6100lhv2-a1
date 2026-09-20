use std::fs;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

use crate::json::Value;
use crate::raptor::{RAPTOR_PROTOCOL_VERSION, RaptorClient, RaptorDaemon, RaptorReply};
use crate::{
    Backend, BackendError, BackendResponse, BackendRoute, DayNightMode, MAX_HEADER_BYTES,
    MAX_SNAPSHOT_BYTES,
};

mod access;
mod audio;
#[cfg(test)]
mod browser_bridge;
mod daynight;
mod files;
mod ha;
mod imaging;
mod motion;
mod motion_actions;
mod motion_email;
mod motion_ftp;
mod motion_gotify;
mod motion_lifecycle;
mod motion_ntfy;
mod motion_roi;
mod motion_telegram;
mod motion_webhook;
mod osd;
mod osd_metadata;
mod privacy;
mod recorder;
mod recorder_config;
mod streams;
mod timelapse;
mod timezone;

const RVD_STATUS_FIELDS: &[&str] = &["status", "streams", "configured"];
const RVD_STREAM_FIELDS: &[&str] = &[
    "chn",
    "w",
    "h",
    "codec",
    "bitrate",
    "avg_bitrate",
    "gop",
    "fps",
    "available",
    "stream_id",
    "jpeg",
    "fps_recovery_required",
    "fps_persistence_pending",
];
const RHD_STATUS_FIELDS: &[&str] = &[
    "status",
    "clients",
    "mjpeg",
    "audio",
    "port",
    "jpeg_rings",
    "jpeg_available",
    "exif_timestamp",
    "sign_snapshots",
    "privacy",
    "tls",
];
const RSD_STATUS_FIELDS: &[&str] = &["status", "clients", "port", "tls", "endpoints"];
const RAPTOR_HTTP_PORT: u64 = 8080;
// Leave room for camera-side scheduling and the three serial privacy readers.
// The caller's absolute deadline still caps both observations.
const HEARTBEAT_RIC_BUDGET: Duration = Duration::from_millis(300);
const HEARTBEAT_PRIVACY_BUDGET: Duration = Duration::from_millis(600);

#[derive(Clone, Copy)]
struct RaptorStream {
    width: u64,
    height: u64,
    fps: Option<u64>,
    format: &'static str,
    available: bool,
}
struct RaptorMediaState {
    streams: [Option<RaptorStream>; 2],
    configured: [bool; 2],
    jpeg_requestable: [bool; 2],
}
// MIPS32 has no native AtomicU64. This counter is diagnostic-only and may wrap.
static SNAPSHOT_IO_FAILURES: AtomicU32 = AtomicU32::new(0);
static HEARTBEAT_RIC_FAILURES: AtomicU32 = AtomicU32::new(0);
static HEARTBEAT_PRIVACY_FAILURES: AtomicU32 = AtomicU32::new(0);

fn heartbeat_failure_record(
    counter: &AtomicU32,
    field: &'static str,
    stage: &'static str,
    error: &BackendError,
    elapsed: Duration,
) -> Option<String> {
    // Saturation prevents counter wrap from repeatedly reopening the initial burst.
    let previous = counter
        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |count| {
            Some(count.saturating_add(1))
        })
        .unwrap();
    let count = previous.saturating_add(1);
    if previous == u32::MAX || (count > 8 && !count.is_power_of_two()) {
        return None;
    }
    // Never format error payloads, upstream status bodies or daemon replies.
    let category = match error {
        BackendError::Connection => "connection",
        BackendError::Timeout => "timeout",
        BackendError::Protocol => "protocol",
        BackendError::Unavailable => "unavailable",
        BackendError::Busy => "busy",
        BackendError::Unsupported(_) => "unsupported",
        BackendError::PartialApply(_) => "partial",
        BackendError::Upstream(_) => "upstream",
    };
    Some(format!(
        "heartbeat-read field={field} stage={stage} error={category} elapsed_ms={} count={count}",
        elapsed.as_millis()
    ))
}

pub struct RaptorBackend {
    ha: std::sync::Arc<crate::camera::ha::HaService>,
    motion_actions: std::sync::Arc<motion_actions::Service>,
    motion_email: std::sync::Arc<motion_email::Service>,
    motion_ftp: std::sync::Arc<motion_ftp::Service>,
    motion_gotify: std::sync::Arc<motion_gotify::Service>,
    motion_ntfy: std::sync::Arc<motion_ntfy::Service>,
    motion_webhook: std::sync::Arc<motion_webhook::Service>,
    motion_telegram: std::sync::Arc<motion_telegram::Service>,
    motion_lifecycle: std::sync::Arc<motion_lifecycle::Service>,
    host: crate::camera::HostBackend,
    client: RaptorClient,
    mutation_lock: std::sync::Mutex<()>,
    daynight_history: std::sync::Mutex<std::collections::VecDeque<Vec<u8>>>,
    ha_privacy_generation: AtomicU32,
    timelapse_user_generation: AtomicU32,
    recording_user_generation: [AtomicU32; 2],
    timelapse: std::sync::Arc<timelapse::Service>,
    file_reads: files::Reads,
    snapshot_address: SocketAddr,
    conflicting_owner_socket: PathBuf,
    conflicting_owner_pid: PathBuf,
}

impl RaptorBackend {
    pub fn new(
        run_dir: PathBuf,
        snapshot_address: SocketAddr,
        conflicting_owner_socket: PathBuf,
        conflicting_owner_pid: PathBuf,
    ) -> Result<Self, BackendError> {
        if !snapshot_address.ip().is_loopback() {
            return Err(BackendError::Protocol);
        }
        let ha = std::sync::Arc::new(crate::camera::ha::HaService::new());
        let motion_gotify = std::sync::Arc::new(motion_gotify::Service::new());
        let motion_email = std::sync::Arc::new(motion_email::Service::new());
        let motion_ftp = std::sync::Arc::new(motion_ftp::Service::new());
        let motion_ntfy = std::sync::Arc::new(motion_ntfy::Service::new());
        let motion_telegram = std::sync::Arc::new(motion_telegram::Service::new());
        let motion_webhook = std::sync::Arc::new(motion_webhook::Service::new());
        let backend = Self {
            motion_actions: std::sync::Arc::new(motion_actions::Service::new(
                ha.clone(),
                motion_webhook.clone(),
                motion_ntfy.clone(),
                motion_email.clone(),
                motion_ftp.clone(),
                motion_gotify.clone(),
                motion_telegram.clone(),
            )),
            motion_gotify,
            motion_email,
            motion_ftp,
            motion_ntfy,
            motion_webhook,
            motion_telegram,
            ha,
            motion_lifecycle: std::sync::Arc::new(motion_lifecycle::Service::new()),
            host: crate::camera::HostBackend::new(crate::camera::CameraPaths::default()),
            client: RaptorClient::new(run_dir, RAPTOR_PROTOCOL_VERSION)?,
            mutation_lock: std::sync::Mutex::new(()),
            daynight_history: std::sync::Mutex::new(std::collections::VecDeque::new()),
            ha_privacy_generation: AtomicU32::new(0),
            timelapse_user_generation: AtomicU32::new(0),
            recording_user_generation: [AtomicU32::new(0), AtomicU32::new(0)],
            timelapse: std::sync::Arc::new(timelapse::Service::default()),
            file_reads: files::Reads::default(),
            snapshot_address,
            conflicting_owner_socket,
            conflicting_owner_pid,
        };
        backend.ensure_exclusive_owner()?;
        Ok(backend)
    }

    pub fn camera_defaults() -> Result<Self, BackendError> {
        Self::new(
            PathBuf::from("/run/rss"),
            SocketAddr::from(([127, 0, 0, 1], 8080)),
            PathBuf::from("/run/prudynt/prudynt.sock"),
            PathBuf::from("/run/prudynt.pid"),
        )
    }

    fn lock_mutation(&self) -> Result<std::sync::MutexGuard<'_, ()>, BackendError> {
        self.mutation_lock.try_lock().map_err(|error| match error {
            std::sync::TryLockError::WouldBlock => BackendError::Busy,
            std::sync::TryLockError::Poisoned(_) => BackendError::Unavailable,
        })
    }

    fn ensure_exclusive_owner(&self) -> Result<(), BackendError> {
        for path in [&self.conflicting_owner_socket, &self.conflicting_owner_pid] {
            match fs::symlink_metadata(path) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                _ => return Err(BackendError::Unavailable),
            }
        }
        Ok(())
    }

    fn command(
        &self,
        daemon: RaptorDaemon,
        request: &[u8],
        deadline: Instant,
    ) -> Result<RaptorReply, BackendError> {
        self.ensure_exclusive_owner()?;
        self.client.command(daemon, request, deadline)
    }

    fn media_state(&self, deadline: Instant) -> Result<RaptorMediaState, BackendError> {
        let rvd = self.command(RaptorDaemon::Rvd, br#"{"cmd":"status"}"#, deadline)?;
        validate_rvd_status(&rvd)
    }

    fn http_snapshot(
        &self,
        stream_id: u8,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        self.http_snapshot_bounded(stream_id, deadline, MAX_SNAPSHOT_BYTES)
    }

    fn http_snapshot_bounded(
        &self,
        stream_id: u8,
        deadline: Instant,
        limit: usize,
    ) -> Result<BackendResponse, BackendError> {
        self.ensure_exclusive_owner()?;
        if stream_id > 1 {
            return Err(BackendError::Protocol);
        }
        let snapshot_address = self.snapshot_address;
        let connect_timeout = remaining(deadline)?.min(Duration::from_millis(500));
        let mut stream =
            TcpStream::connect_timeout(&snapshot_address, connect_timeout).map_err(|error| {
                classify_snapshot_io_error(stream_id, "connect", snapshot_address, &error)
            })?;
        let request = format!(
            "GET /snap.jpg?stream={stream_id} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\nAccept: image/jpeg\r\n\r\n",
            snapshot_address
        );
        write_all_deadline(
            &mut stream,
            request.as_bytes(),
            deadline,
            stream_id,
            snapshot_address,
        )?;
        let mut response = Vec::with_capacity(8192);
        let header_end = loop {
            if response.len() >= MAX_HEADER_BYTES {
                return Err(BackendError::Protocol);
            }
            set_read_timeout(&stream, deadline, stream_id, snapshot_address)?;
            let mut chunk = [0_u8; 4096];
            match stream.read(&mut chunk) {
                Ok(0) => return Err(BackendError::Protocol),
                Ok(count) => {
                    response.extend_from_slice(&chunk[..count]);
                    if let Some(index) = find_bytes(&response, b"\r\n\r\n") {
                        break index + 4;
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => {
                    return Err(classify_snapshot_io_error(
                        stream_id,
                        "read-header",
                        snapshot_address,
                        &error,
                    ));
                }
            }
        };
        let header =
            std::str::from_utf8(&response[..header_end]).map_err(|_| BackendError::Protocol)?;
        if header_end > MAX_HEADER_BYTES {
            return Err(BackendError::Protocol);
        }
        let content_length = parse_snapshot_headers(header)?;
        if content_length > limit || response.len() - header_end > content_length {
            return Err(BackendError::Protocol);
        }
        let mut body = Vec::with_capacity(content_length);
        body.extend_from_slice(&response[header_end..]);
        while body.len() < content_length {
            set_read_timeout(&stream, deadline, stream_id, snapshot_address)?;
            let mut chunk = [0_u8; 8192];
            let wanted = (content_length - body.len()).min(chunk.len());
            match stream.read(&mut chunk[..wanted]) {
                Ok(0) => return Err(BackendError::Protocol),
                Ok(count) => body.extend_from_slice(&chunk[..count]),
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => {
                    return Err(classify_snapshot_io_error(
                        stream_id,
                        "read-body",
                        snapshot_address,
                        &error,
                    ));
                }
            }
        }
        if body.len() < 4 || body[..2] != [0xff, 0xd8] || body[body.len() - 2..] != [0xff, 0xd9] {
            return Err(BackendError::Protocol);
        }
        Ok(BackendResponse::jpeg(body))
    }
}

impl RaptorBackend {
    fn media_health(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let streams = self.media_state(deadline)?;
        let rhd = self.command(RaptorDaemon::Rhd, br#"{"cmd":"status"}"#, deadline)?;
        let jpeg = validate_rhd_status(&rhd)?;
        let running = (0..2).any(|id| {
            streams.configured[id] && streams.streams[id].is_some_and(|stream| stream.available)
        });
        let healthy = (0..2).any(|id| {
            streams.configured[id]
                && streams.streams[id].is_some_and(|stream| stream.available)
                && streams.jpeg_requestable[id]
                && jpeg[id]
        });
        let streams_enabled = streams
            .configured
            .iter()
            .filter(|enabled| **enabled)
            .count();
        Ok(BackendResponse::json(format!(
            "{{\"control_api\":{{\"name\":\"Thingino Control\",\"version\":1}},\"status\":\"ok\",\"healthy\":{healthy},\"backend\":{{\"name\":\"raptor\",\"available\":true}},\"checks\":{{\"system\":{{\"streamer_running\":{running}}},\"streaming\":{{\"running\":{running},\"healthy\":{healthy},\"streams_enabled\":{streams_enabled}}}}}}}\n"
        ).into_bytes()))
    }

    fn media_runtime(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let streams = self.media_state(deadline)?;
        let jpeg_rings = self
            .command(
                RaptorDaemon::Rhd,
                br#"{"cmd":"status"}"#,
                deadline.min(Instant::now() + Duration::from_millis(150)),
            )
            .and_then(|reply| validate_rhd_status(&reply))
            .ok();
        let rtsp_state = self
            .command(
                RaptorDaemon::Rsd,
                br#"{"cmd":"status"}"#,
                deadline.min(Instant::now() + Duration::from_millis(150)),
            )
            .and_then(|reply| validate_rsd_status(&reply))
            .ok();
        let rtsp = rtsp_state
            .as_ref()
            .map(|(port, _)| format!(",\"rtsp\":{{\"port\":{port}}}"))
            .unwrap_or_default();
        let stream = |stream_id: usize| -> Result<String, BackendError> {
            let metadata = streams.streams[stream_id];
            let enabled = streams.configured[stream_id];
            // Video readiness is independent of the separate JPEG encoder/reader.
            let available = enabled && metadata.is_some_and(|value| value.available);
            let snapshot_available = available
                && streams.jpeg_requestable[stream_id]
                && jpeg_rings.is_some_and(|rings| rings[stream_id]);
            let snapshot = if snapshot_available {
                format!("\"/api/v1/actions/snapshot?stream_id={stream_id}\"")
            } else {
                "null".to_owned()
            };
            let Some(metadata) = metadata else {
                return Ok(format!(
                    "{{\"available\":false,\"enabled\":{enabled},\"snapshot_url\":null}}"
                ));
            };
            let endpoint = rtsp_state
                .as_ref()
                .map(|(_, endpoints)| {
                    format!(
                        ",\"rtsp_endpoint\":{}",
                        Value::String(endpoints[stream_id].clone()).to_json()
                    )
                })
                .unwrap_or_default();
            Ok(format!(
                "{{\"available\":{available},\"enabled\":{enabled},\"snapshot_url\":{snapshot},\"width\":{},\"height\":{},\"format\":\"{}\"{}{endpoint}}}",
                metadata.width,
                metadata.height,
                metadata.format,
                metadata
                    .fps
                    .map(|fps| format!(",\"fps\":{fps}"))
                    .unwrap_or_default(),
            ))
        };
        Ok(BackendResponse::json(
            format!(
                "{{\"streams\":{{\"ch0\":{},\"ch1\":{}}}{rtsp}}}\n",
                stream(0)?,
                stream(1)?
            )
            .into_bytes(),
        ))
    }

    fn heartbeat(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let mut fields = std::collections::BTreeMap::new();
        for name in [
            "daynight_brightness",
            "total_gain",
            "rec_ch0",
            "rec_ch1",
            "timelapse_enabled",
            "motion_enabled",
            "motion_active",
            "motion_ingress_ready",
            "privacy_enabled",
            "color_mode",
            "mic_enabled",
            "spk_enabled",
            "daynight_enabled",
            "ircut_state",
            "ir850_state",
            "ir940_state",
            "white_state",
        ] {
            fields.insert(name.to_owned(), Value::Null);
        }
        fields.insert(
            "timelapse_enabled".to_owned(),
            self.timelapse
                .enabled_observation()
                .map_or(Value::Null, Value::Bool),
        );
        for channel in 0..=1 {
            if let Ok(state) = self.recording_observation(
                channel,
                deadline.min(Instant::now() + Duration::from_millis(150)),
            ) {
                fields.insert(
                    format!("rec_ch{channel}"),
                    state.value.get_path("recording").unwrap().clone(),
                );
                fields.insert(
                    format!("rec_ch{channel}_available"),
                    state.value.get_path("available").unwrap().clone(),
                );
                fields.insert(
                    format!("rec_ch{channel}_file_closed"),
                    state.value.get_path("file_closed").unwrap().clone(),
                );
                fields.insert(
                    format!("rec_ch{channel}_reason"),
                    state.value.get_path("reason").unwrap().clone(),
                );
            }
        }
        let (now, uptime) = self.host.host_clock()?;
        fields.insert("time_now".to_owned(), Value::Number(now.to_string()));
        fields.insert("uptime".to_owned(), Value::Number(uptime.to_string()));
        fields.insert(
            "daynight_mode".to_owned(),
            Value::String("unknown".to_owned()),
        );
        // Optional daemon failure leaves only its state unknown. Budget each
        // read so one daemon cannot consume the entire heartbeat deadline.
        let ric_started = Instant::now();
        let ric = self.command(
            RaptorDaemon::Ric,
            br#"{"cmd":"mode"}"#,
            deadline.min(Instant::now() + HEARTBEAT_RIC_BUDGET),
        );
        let ric_result = ric.and_then(|reply| {
            let mode = reply.value.get_path("mode").and_then(Value::as_str);
            let parsed = match mode {
                Some("auto") => Some(DayNightMode::Auto),
                Some("day") => Some(DayNightMode::Day),
                Some("night") => Some(DayNightMode::Night),
                _ => None,
            };
            if let Some(mode) = parsed {
                validate_ric_mode(&reply, mode)?;
                fields.insert(
                    "daynight_mode".to_owned(),
                    reply.value.get_path("state").unwrap().clone(),
                );
                fields.insert(
                    "daynight_enabled".to_owned(),
                    Value::Bool(mode == DayNightMode::Auto),
                );
                Ok(())
            } else {
                Err(BackendError::Protocol)
            }
        });
        if let Err(error) = ric_result
            && let Some(record) = heartbeat_failure_record(
                &HEARTBEAT_RIC_FAILURES,
                "daynight",
                "query-or-validation",
                &error,
                ric_started.elapsed(),
            )
        {
            eprintln!("{record}");
        }
        let mut supported = std::collections::BTreeMap::new();
        supported.insert("daynight".to_owned(), Value::Bool(true));
        supported.insert("motion".to_owned(), Value::Bool(true));
        let lifecycle = self.motion_lifecycle.snapshot();
        if lifecycle.running {
            supported.insert("motion".to_owned(), Value::Bool(lifecycle.supported));
            if lifecycle.available {
                fields.insert(
                    "motion_enabled".to_owned(),
                    Value::Bool(lifecycle.monitoring),
                );
                fields.insert("motion_active".to_owned(), Value::Bool(lifecycle.active));
            }
        } else if let Ok(reply) =
            self.motion_status(deadline.min(Instant::now() + Duration::from_millis(150)))
        {
            supported.insert(
                "motion".to_owned(),
                reply.value.get_path("supported").unwrap().clone(),
            );
            let (raw_available, _) = Self::observed_motion(&reply);
            if raw_available {
                fields.insert(
                    "motion_enabled".to_owned(),
                    reply.value.get_path("active").unwrap().clone(),
                );
                fields.insert(
                    "motion_active".to_owned(),
                    Value::Bool(Self::observed_motion(&reply).1),
                );
            }
        }
        supported.insert("privacy".to_owned(), Value::Bool(true));
        let privacy_started = Instant::now();
        let privacy = self.privacy_state(deadline.min(Instant::now() + HEARTBEAT_PRIVACY_BUDGET));
        match privacy {
            Ok(enabled) => {
                fields.insert("privacy_enabled".to_owned(), Value::Bool(enabled));
            }
            Err(BackendError::Unsupported(_)) => {
                supported.insert("privacy".to_owned(), Value::Bool(false));
            }
            Err(error) => {
                if let Some(record) = heartbeat_failure_record(
                    &HEARTBEAT_PRIVACY_FAILURES,
                    "privacy",
                    "participants",
                    &error,
                    privacy_started.elapsed(),
                ) {
                    eprintln!("{record}");
                }
            }
        }
        if let Ok(color) =
            self.color_state(deadline.min(Instant::now() + Duration::from_millis(100)))
        {
            fields.insert(
                "color_mode".to_owned(),
                Value::Number(if color { "0" } else { "1" }.to_owned()),
            );
        }
        if let Ok(audio) =
            self.live_audio_owner_states(deadline.min(Instant::now() + Duration::from_millis(150)))
        {
            for name in ["mic_enabled", "spk_enabled"] {
                fields.insert(name.to_owned(), audio.get_path(name).unwrap().clone());
            }
        }
        if let Ok(outputs) =
            self.ir_output_state(deadline.min(Instant::now() + Duration::from_millis(150)))
        {
            for (name, (_, enabled)) in ["ircut_state", "ir850_state"].into_iter().zip(outputs) {
                fields.insert(
                    name.to_owned(),
                    enabled.map_or(Value::Null, |on| {
                        Value::Number(if on { "1" } else { "0" }.to_owned())
                    }),
                );
            }
        }
        if let Ok(exposure) =
            self.daynight_exposure(deadline.min(Instant::now() + Duration::from_millis(150)))
        {
            fields.insert(
                "daynight_brightness".to_owned(),
                exposure.get_path("brightness_pct").unwrap().clone(),
            );
            fields.insert(
                "total_gain".to_owned(),
                exposure.get_path("total_gain").unwrap().clone(),
            );
        }
        fields.insert("controls_supported".to_owned(), Value::Object(supported));
        let body = Value::Object(fields).to_json().into_bytes();
        self.retain_daynight_sample(&body);
        Ok(BackendResponse::json(body))
    }

    fn media_config(&self, _deadline: Instant) -> Result<BackendResponse, BackendError> {
        Err(BackendError::Unavailable)
    }
}

impl RaptorBackend {
    fn capture_snapshot(
        &self,
        stream_id: u8,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        self.http_snapshot(stream_id, deadline)
    }
}

impl RaptorBackend {
    fn set_daynight_mode(
        &self,
        mode: DayNightMode,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let _mutation = self.lock_mutation()?;
        self.timelapse_user_generation
            .fetch_add(1, Ordering::AcqRel);
        let request = format!("{{\"cmd\":\"mode\",\"value\":\"{}\"}}", mode.as_str());
        let reply = self.command(RaptorDaemon::Ric, request.as_bytes(), deadline)?;
        validate_ric_mode(&reply, mode)?;
        // The pinned RIC mode command without a value reads its current mode.
        // This confirms daemon state only, not the physical filter or ISP effect.
        let readback = self.command(RaptorDaemon::Ric, br#"{"cmd":"mode"}"#, deadline)?;
        validate_ric_mode(&readback, mode)?;
        Ok(BackendResponse::json(
            format!(
                "{{\"status\":\"accepted\",\"mode\":\"{}\"}}\n",
                mode.as_str()
            )
            .into_bytes(),
        ))
    }
}

fn validate_ric_mode(reply: &RaptorReply, mode: DayNightMode) -> Result<(), BackendError> {
    reply.require_only_fields(&["status", "mode", "state"])?;
    require_ok(reply)?;
    let state = reply.value.get_path("state").and_then(Value::as_str);
    if reply.value.get_path("mode").and_then(Value::as_str) != Some(mode.as_str())
        || !matches!(state, Some("day" | "night"))
        || (mode != DayNightMode::Auto && state != Some(mode.as_str()))
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

impl Backend for RaptorBackend {
    fn media_file_identity(
        &self,
        target: &str,
        deadline: Instant,
    ) -> Result<Option<crate::MediaFileIdentity>, BackendError> {
        self.recording_identity(target, deadline)
    }

    fn authorize_media(&self, target: &str) -> bool {
        // Existing uhttpd forwards these fixed routes to IPv4 loopback:8080.
        // A fixture/custom snapshot destination must never authorize another service.
        self.ensure_exclusive_owner().is_ok()
            && self.snapshot_address == SocketAddr::from(([127, 0, 0, 1], 8080))
            && raptor_proxy_media_target(target)
    }

    fn update_management_credential(
        &self,
        username: &str,
        password: &str,
        deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        Some(
            self.host
                .update_management_credential(username, password, deadline),
        )
    }

    fn allows_persistent_auth_mutations(&self) -> bool {
        true
    }

    fn request(
        &self,
        route: BackendRoute,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        self.ensure_exclusive_owner()?;
        match route {
            BackendRoute::Health => self.media_health(deadline),
            BackendRoute::RuntimeMedia => self.media_runtime(deadline),
            BackendRoute::Config => self.media_config(deadline),
            BackendRoute::Snapshot(stream) => self.capture_snapshot(stream, deadline),
            BackendRoute::DayNight(mode) => self.set_daynight_mode(mode, deadline),
        }
        .map_err(|error| match error {
            BackendError::Protocol => BackendError::Upstream(502),
            other => other,
        })
    }

    fn api_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        if target.starts_with("/api/v1/files?") || target.starts_with("/api/v1/files/text?") {
            return Some(self.recording_files(method, target, body, deadline));
        }
        if target == "/api/v1/storage/sd" {
            return Some(match method {
                "GET" if body.is_empty() => self.recording_sd(deadline),
                "POST" if !body.is_empty() => self.queue_storage_format(body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if target == "/api/v1/recorder?domain=timelapse" {
            return Some(self.timelapse_request(method, body, deadline));
        }
        if target == "/api/v1/recorder" || target.starts_with("/api/v1/recorder?") {
            let selected = match target {
                "/api/v1/recorder" => None,
                "/api/v1/recorder?channel=0" => Some(0),
                "/api/v1/recorder?channel=1" => Some(1),
                _ => return Some(Err(BackendError::Protocol)),
            };
            return Some(match method {
                "GET" if body.is_empty() => self.recorder_config(selected.unwrap_or(0), deadline),
                "POST" if !body.is_empty() => self.update_recorder_config(selected, body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if let Some(result) = self.ha_request(method, target, body) {
            return Some(result);
        }
        if let Some(result) = self.motion_webhook_request(method, target, body) {
            return Some(result);
        }
        if let Some(result) = self.motion_ntfy_request(method, target, body) {
            return Some(result);
        }
        if let Some(result) = self.motion_email_request(method, target, body) {
            return Some(result);
        }
        if let Some(result) = self.motion_ftp_request(method, target, body) {
            return Some(result);
        }
        if let Some(result) = self.motion_gotify_request(method, target, body) {
            return Some(result);
        }
        if let Some(result) = self.motion_telegram_request(method, target, body) {
            return Some(result);
        }
        if target == "/api/v1/config/time" {
            return Some(match method {
                "GET" if body.is_empty() => self.time_config(deadline),
                "POST" if !body.is_empty() => self.update_time_config(body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if target == "/api/v1/runtime/daynight/history" {
            return Some(match method {
                "GET" if body.is_empty() => self.daynight_history(),
                _ => Err(BackendError::Protocol),
            });
        }
        if target == "/api/v1/runtime/daynight/sensors" {
            return Some(match method {
                "GET" if body.is_empty() => self.daynight_sensors(deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if let Some(result) = self.host.api_request(method, target, body, deadline) {
            return Some(result);
        }
        if target == "/api/v1/config/daynight" {
            return Some(match method {
                "GET" if body.is_empty() => self.daynight_config(deadline),
                "POST" if !body.is_empty() => self.update_daynight_config(body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if target == "/api/v1/config/access" {
            return Some(match method {
                "GET" if body.is_empty() => self.access_config(deadline),
                "POST" if !body.is_empty() => self.update_access(body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if let Some(id) = match target {
            "/api/v1/prudynt/stream0" => Some(0),
            "/api/v1/prudynt/stream1" => Some(1),
            _ => None,
        } {
            return Some(if method == "GET" && body.is_empty() {
                self.stream_config(id, deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/prudynt/osd" {
            return Some(if method == "GET" && body.is_empty() {
                self.osd_config(deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/config/osd-metadata" {
            return Some(match method {
                "GET" if body.is_empty() => self.osd_metadata_config(deadline),
                "POST" if !body.is_empty() => self.update_osd_metadata_config(body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if target == "/api/v1/prudynt/motion" {
            return Some(if method == "GET" && body.is_empty() {
                self.motion_config(deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/prudynt/privacy" {
            return Some(if method == "GET" && body.is_empty() {
                self.privacy_config(deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/prudynt/audio" {
            return Some(if method == "GET" && body.is_empty() {
                self.audio_config(deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/prudynt" && method == "POST" {
            if crate::json::parse(body)
                .ok()
                .is_some_and(|v| v.get_path("stream0").is_some() || v.get_path("stream1").is_some())
            {
                return Some(self.update_streams(body, deadline));
            }
            if crate::json::parse(body)
                .ok()
                .is_some_and(|value| value.get_path("privacy").is_some())
            {
                return Some(self.update_privacy_config(body, deadline));
            }
            if crate::json::parse(body)
                .ok()
                .is_some_and(|value| value.get_path("osd").is_some())
            {
                return Some(self.update_osd_config(body, deadline));
            }
            if crate::json::parse(body)
                .ok()
                .is_some_and(|value| value.get_path("motion").is_some())
            {
                return Some(self.update_motion_config(body, deadline));
            }
            return Some(self.update_audio(body, deadline));
        }
        if target == "/api/v1/imaging" {
            return Some(match method {
                "GET" if body.is_empty() => self.imaging(deadline),
                "POST" if !body.is_empty() => self.update_imaging(body, deadline),
                _ => Err(BackendError::Protocol),
            });
        }
        if target == "/api/v1/runtime/motion" {
            return Some(if method == "GET" && body.is_empty() {
                self.motion_runtime(deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/actions/control" {
            return Some(if method == "POST" && !body.is_empty() {
                self.live_control(body, deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/runtime/heartbeat" {
            return Some(if method == "GET" && body.is_empty() {
                self.heartbeat(deadline)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/runtime/system" {
            return Some(if method == "GET" && body.is_empty() {
                self.host.runtime_system_with_media(Value::Null)
            } else {
                Err(BackendError::Protocol)
            });
        }
        if target == "/api/v1/services/send/config" {
            return Some(match method {
                "GET" if body.is_empty() => {
                    Err(BackendError::Unsupported(unsupported_reason(target)))
                }
                "POST" if !body.is_empty() => {
                    Err(BackendError::Unsupported(unsupported_reason(target)))
                }
                _ => Err(BackendError::Protocol),
            });
        }
        unsupported_control_route(target)
            .then_some(Err(BackendError::Unsupported(unsupported_reason(target))))
    }
}

fn raptor_proxy_media_target(target: &str) -> bool {
    if matches!(
        target,
        "/api/v1/actions/snapshot?stream_id=0" | "/api/v1/actions/snapshot?stream_id=1"
    ) {
        return true;
    }
    let Some(query) = target.strip_prefix("/media/v1/mjpeg?stream=") else {
        return false;
    };
    let (stream, tail) = query.split_once('&').unwrap_or((query, ""));
    if !matches!(stream, "0" | "1") {
        return false;
    }
    if tail.is_empty() {
        return !query.ends_with('&');
    }
    let Some(fps) = tail.strip_prefix("f=") else {
        return false;
    };
    !fps.starts_with('0')
        && fps.len() <= 2
        && fps.bytes().all(|b| b.is_ascii_digit())
        && fps
            .parse::<u8>()
            .is_ok_and(|value| (1..=30).contains(&value))
}

fn unsupported_reason(target: &str) -> &'static str {
    match target {
        "/api/v1/config/access" => "Raptor RTSP credential configuration is not implemented",
        "/api/v1/imaging" => "Raptor ISP controls require checked SDK readback support",
        "/api/v1/actions/prudynt/restart" => {
            "Prudynt restart does not apply to the selected Raptor backend"
        }
        "/api/v1/runtime/media/metrics" => "Raptor does not provide the Prudynt metrics contract",
        "/api/v1/config/daynight" => {
            "RIC live day/night is supported; persistent schedule configuration is not integrated"
        }
        _ => "This media configuration operation is not implemented by the Raptor adapter",
    }
}

pub(crate) fn unsupported_control_route(target: &str) -> bool {
    matches!(
        target,
        "/api/v1/config/crontab"
            | "/api/v1/config/gpio"
            | "/api/v1/config/time"
            | "/api/v1/config/network"
            | "/api/v1/config/access"
            | "/api/v1/services/send/config"
            | "/api/v1/imaging"
            | "/api/v1/runtime/system"
            | "/api/v1/runtime/media/metrics"
            | "/api/v1/prudynt"
            | "/api/v1/runtime/motion"
            | "/api/v1/storage/overlay"
            | "/api/v1/storage/sd"
            | "/api/v1/diagnostics"
            | "/api/v1/runtime/daynight/history"
            | "/api/v1/runtime/daynight/sensors"
            | "/api/v1/sensor/iq"
            | "/api/v1/runtime/sensor"
            | "/api/v1/actions/reset"
            | "/api/v1/actions/factory-reset"
            | "/api/v1/actions/reboot"
            | "/api/v1/actions/prudynt/restart"
            | "/api/v1/runtime/heartbeat"
            | "/api/v1/actions/time/sync"
            | "/api/v1/network/probe"
            | "/api/v1/network/wifi-scan"
            | "/api/v1/actions/control"
    ) || target
        .strip_prefix("/api/v1/config/")
        .is_some_and(|domain| {
            matches!(
                domain,
                "admin" | "webui" | "rsyslog" | "mqtt_sub" | "daynight"
            )
        })
        || target.starts_with("/api/v1/prudynt/")
        || target.starts_with("/api/v1/files?")
        || target.starts_with("/api/v1/files/text?")
        || target.starts_with("/api/v1/diagnostics/info?")
}

fn validate_rvd_status(reply: &RaptorReply) -> Result<RaptorMediaState, BackendError> {
    reply.require_only_fields(RVD_STATUS_FIELDS)?;
    require_ok(reply)?;
    let streams = reply
        .value
        .get_path("streams")
        .and_then(Value::as_array)
        .ok_or(BackendError::Protocol)?;
    let mut result = [None; 2];
    let mut jpeg_seen = [false; 2];
    let mut jpeg_requestable = [false; 2];
    let mut hardware_channels = [false; 4];
    if streams.len() > 4 {
        return Err(BackendError::Protocol);
    }
    for stream in streams {
        let fields = stream.as_object().ok_or(BackendError::Protocol)?;
        if fields
            .keys()
            .any(|name| !RVD_STREAM_FIELDS.iter().any(|allowed| name == allowed))
        {
            return Err(BackendError::Protocol);
        }
        let wire_channel = value_u64(fields.get("chn").ok_or(BackendError::Protocol)?)?;
        if wire_channel > 3 || hardware_channels[wire_channel as usize] {
            return Err(BackendError::Protocol);
        }
        hardware_channels[wire_channel as usize] = true;
        let (channel, is_jpeg) = match (fields.get("stream_id"), fields.get("jpeg")) {
            (None, None) => ((wire_channel % 2) as usize, wire_channel >= 2),
            (Some(id), Some(Value::Bool(jpeg))) => {
                let id = value_u64(id)?;
                if id > 1 {
                    return Err(BackendError::Protocol);
                }
                (id as usize, *jpeg)
            }
            _ => return Err(BackendError::Protocol),
        };
        let width = value_u64(fields.get("w").ok_or(BackendError::Protocol)?)?;
        let height = value_u64(fields.get("h").ok_or(BackendError::Protocol)?)?;
        let fps_blocked = match (
            fields.get("fps_recovery_required"),
            fields.get("fps_persistence_pending"),
        ) {
            (None, None) => false,
            (Some(Value::Bool(recovery)), Some(Value::Bool(pending))) => *recovery || *pending,
            _ => return Err(BackendError::Protocol),
        };
        let fps = match fields.get("fps") {
            Some(Value::Null) if fps_blocked => None,
            Some(value) if !fps_blocked => Some(value_u64(value)?),
            _ => return Err(BackendError::Protocol),
        };
        let codec = value_u64(fields.get("codec").ok_or(BackendError::Protocol)?)?;
        let bitrate = value_u64(fields.get("bitrate").ok_or(BackendError::Protocol)?)?;
        let average_bitrate = value_u64(fields.get("avg_bitrate").ok_or(BackendError::Protocol)?)?;
        let gop = value_u64(fields.get("gop").ok_or(BackendError::Protocol)?)?;
        let available = match fields.get("available") {
            Some(Value::Bool(value)) => *value,
            None => false,
            _ => return Err(BackendError::Protocol),
        };
        // DCS-6100LHV2 A1 uses a 1920x1080 sensor. Retain the existing
        // 1..30 fps configuration contract and even chroma dimensions.
        if average_bitrate > 100_000_000
            || !(32..=1920).contains(&width)
            || width % 2 != 0
            || !(32..=1080).contains(&height)
            || height % 2 != 0
            || fps.is_some_and(|fps| !(1..=30).contains(&fps))
            || (fps_blocked && available)
        {
            return Err(BackendError::Protocol);
        }
        if is_jpeg {
            let jpeg_channel = channel;
            if jpeg_seen[jpeg_channel] || codec != 2 || bitrate != 0 || gop != 0 {
                return Err(BackendError::Protocol);
            }
            jpeg_seen[jpeg_channel] = true;
            // RVD stops idle JPEG encoders until RHD acquires a per-slot reader.
            // Explicit semantic identity and confirmed FPS state permit a bounded
            // request; they do not claim that a fresh JPEG has already been made.
            let idle_requestable = fields.get("stream_id").is_some()
                && fields.get("available") == Some(&Value::Bool(false))
                && fields.get("fps_recovery_required") == Some(&Value::Bool(false))
                && fields.get("fps_persistence_pending") == Some(&Value::Bool(false));
            jpeg_requestable[jpeg_channel] = available || idle_requestable;
            continue;
        }
        if result[channel].is_some() {
            return Err(BackendError::Protocol);
        }
        if codec > 1 || bitrate == 0 || bitrate > 100_000_000 || gop == 0 || gop > 100_000_000 {
            return Err(BackendError::Protocol);
        }
        result[channel] = Some(RaptorStream {
            width,
            height,
            fps,
            format: if codec == 0 { "H264" } else { "H265" },
            available,
        });
    }
    let configured = match reply.value.get_path("configured") {
        Some(value) => bool_pair(value)?,
        None => [result[0].is_some(), result[1].is_some()],
    };
    Ok(RaptorMediaState {
        streams: result,
        configured,
        jpeg_requestable,
    })
}

fn bool_pair(value: &Value) -> Result<[bool; 2], BackendError> {
    let values = value
        .as_array()
        .filter(|values| values.len() == 2)
        .ok_or(BackendError::Protocol)?;
    Ok([
        values[0].as_bool().ok_or(BackendError::Protocol)?,
        values[1].as_bool().ok_or(BackendError::Protocol)?,
    ])
}

fn validate_rhd_status(reply: &RaptorReply) -> Result<[bool; 2], BackendError> {
    reply.require_only_fields(RHD_STATUS_FIELDS)?;
    require_ok(reply)?;
    if value_u64(reply.value.get_path("port").ok_or(BackendError::Protocol)?)? != RAPTOR_HTTP_PORT
        || reply.value.get_path("tls").and_then(Value::as_bool) != Some(false)
    {
        return Err(BackendError::Protocol);
    }
    for key in ["clients", "mjpeg", "audio"] {
        let _ = value_u64(reply.value.get_path(key).ok_or(BackendError::Protocol)?)?;
    }
    for key in ["exif_timestamp", "sign_snapshots", "privacy"] {
        if reply.value.get_path(key).and_then(Value::as_bool).is_none() {
            return Err(BackendError::Protocol);
        }
    }
    let rings = value_u64(
        reply
            .value
            .get_path("jpeg_rings")
            .ok_or(BackendError::Protocol)?,
    )?;
    if rings > 2 {
        return Err(BackendError::Protocol);
    }
    // The legacy aggregate count does not identify a surviving JPEG slot.
    // Do not guess that a single remaining ring belongs to the main stream.
    match reply.value.get_path("jpeg_available") {
        Some(value) => bool_pair(value),
        None => Ok([false; 2]),
    }
}

fn validate_rsd_status(reply: &RaptorReply) -> Result<(u64, [String; 2]), BackendError> {
    reply.require_only_fields(RSD_STATUS_FIELDS)?;
    require_ok(reply)?;
    let port = value_u64(reply.value.get_path("port").ok_or(BackendError::Protocol)?)?;
    if !(1..=65535).contains(&port)
        || reply.value.get_path("tls").and_then(Value::as_bool) != Some(false)
    {
        return Err(BackendError::Protocol);
    }
    let _ = value_u64(
        reply
            .value
            .get_path("clients")
            .ok_or(BackendError::Protocol)?,
    )?;
    let endpoints = reply
        .value
        .get_path("endpoints")
        .and_then(Value::as_array)
        .filter(|values| values.len() == 2)
        .ok_or(BackendError::Protocol)?;
    let mut paths = [String::new(), String::new()];
    for id in 0..2 {
        let path = endpoints[id]
            .as_str()
            .filter(|path| access::endpoint_valid(path, id))
            .ok_or(BackendError::Protocol)?;
        paths[id] = path.to_owned();
    }
    if paths[0] == paths[1] {
        return Err(BackendError::Protocol);
    }
    Ok((port, paths))
}

fn require_ok(reply: &RaptorReply) -> Result<(), BackendError> {
    match reply.value.get_path("status").and_then(Value::as_str) {
        Some("ok") => Ok(()),
        Some("error") => Err(BackendError::Unavailable),
        _ => Err(BackendError::Protocol),
    }
}

fn value_u64(value: &Value) -> Result<u64, BackendError> {
    match value {
        Value::Number(value) => value.parse().map_err(|_| BackendError::Protocol),
        _ => Err(BackendError::Protocol),
    }
}

fn parse_snapshot_headers(header: &str) -> Result<usize, BackendError> {
    let mut lines = header.split("\r\n");
    let status = lines.next().ok_or(BackendError::Protocol)?;
    if status != "HTTP/1.1 200 OK" && status != "HTTP/1.0 200 OK" {
        return Err(BackendError::Unavailable);
    }
    let mut content_length = None;
    let mut jpeg = false;
    let mut content_type_seen = false;
    for line in lines {
        if line.is_empty() {
            continue;
        }
        let (name, value) = line.split_once(':').ok_or(BackendError::Protocol)?;
        let name = name.trim();
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err(BackendError::Protocol);
            }
            content_length = Some(value.parse::<usize>().map_err(|_| BackendError::Protocol)?);
        } else if name.eq_ignore_ascii_case("content-type") {
            if content_type_seen {
                return Err(BackendError::Protocol);
            }
            content_type_seen = true;
            jpeg = value.eq_ignore_ascii_case("image/jpeg");
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(BackendError::Protocol);
        }
    }
    let length = content_length
        .filter(|length| *length > 0 && *length <= MAX_SNAPSHOT_BYTES)
        .ok_or(BackendError::Protocol)?;
    if !jpeg {
        return Err(BackendError::Protocol);
    }
    Ok(length)
}

fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn write_all_deadline(
    stream: &mut TcpStream,
    mut bytes: &[u8],
    deadline: Instant,
    stream_id: u8,
    address: SocketAddr,
) -> Result<(), BackendError> {
    while !bytes.is_empty() {
        stream
            .set_write_timeout(Some(remaining(deadline)?))
            .map_err(|_| BackendError::Connection)?;
        match stream.write(bytes) {
            Ok(0) => return Err(BackendError::Connection),
            Ok(count) => bytes = &bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                return Err(classify_snapshot_io_error(
                    stream_id,
                    "write-request",
                    address,
                    &error,
                ));
            }
        }
    }
    Ok(())
}

fn set_read_timeout(
    stream: &TcpStream,
    deadline: Instant,
    stream_id: u8,
    address: SocketAddr,
) -> Result<(), BackendError> {
    stream
        .set_read_timeout(Some(remaining(deadline)?))
        .map_err(|error| classify_snapshot_io_error(stream_id, "set-read-timeout", address, &error))
}

fn remaining(deadline: Instant) -> Result<Duration, BackendError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or(BackendError::Timeout)
}

fn classify_connect_error(error: io::Error) -> BackendError {
    match error.kind() {
        io::ErrorKind::ConnectionRefused | io::ErrorKind::NotFound => BackendError::Unavailable,
        _ => classify_io_error(&error),
    }
}

fn classify_snapshot_io_error(
    stream_id: u8,
    stage: &str,
    address: SocketAddr,
    error: &io::Error,
) -> BackendError {
    let count = SNAPSHOT_IO_FAILURES
        .fetch_add(1, Ordering::Relaxed)
        .wrapping_add(1);
    if count <= 8 || count.is_power_of_two() {
        eprintln!(
            "raptor snapshot I/O failure #{count}: stream={stream_id} stage={stage} address={address} kind={:?} errno={:?}",
            error.kind(),
            error.raw_os_error()
        );
    }
    if stage == "connect" {
        classify_connect_error(io::Error::from(error.kind()))
    } else {
        classify_io_error(error)
    }
}

fn classify_io_error(error: &io::Error) -> BackendError {
    match error.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => BackendError::Timeout,
        _ => BackendError::Connection,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heartbeat_diagnostics_are_bounded_and_redact_error_details() {
        let counter = AtomicU32::new(0);
        let mut emitted = Vec::new();
        for count in 1..=32 {
            if let Some(record) = heartbeat_failure_record(
                &counter,
                "privacy",
                "participants",
                &BackendError::PartialApply("DO_NOT_LOG_PRIVATE_DETAIL"),
                Duration::from_millis(151),
            ) {
                assert!(!record.contains("DO_NOT_LOG"));
                assert!(record.contains("error=partial elapsed_ms=151"));
                emitted.push(count);
            }
        }
        assert_eq!(emitted, vec![1, 2, 3, 4, 5, 6, 7, 8, 16, 32]);
        counter.store(u32::MAX, Ordering::Relaxed);
        assert!(
            heartbeat_failure_record(
                &counter,
                "daynight",
                "query-or-validation",
                &BackendError::Timeout,
                Duration::ZERO,
            )
            .is_none()
        );
        assert_eq!(counter.load(Ordering::Relaxed), u32::MAX);
        let timeout = heartbeat_failure_record(
            &AtomicU32::new(0),
            "daynight",
            "query-or-validation",
            &BackendError::Timeout,
            Duration::from_millis(150),
        )
        .unwrap();
        assert!(timeout.contains("error=timeout elapsed_ms=150 count=1"));
    }
    use std::net::TcpListener;
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::Path;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    pub(super) fn task_temp(_name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = fs::canonicalize(root).unwrap().join(format!(
            "rb-{}-{}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    pub(super) fn framed(body: &[u8]) -> Vec<u8> {
        let mut frame = Vec::with_capacity(body.len() + 2);
        frame.extend_from_slice(&(body.len() as u16).to_be_bytes());
        frame.extend_from_slice(body);
        frame
    }

    pub(super) fn read_request(stream: &mut UnixStream) -> Vec<u8> {
        // macOS may inherit the fixture listener's nonblocking flag on accept.
        stream.set_nonblocking(false).unwrap();
        let mut header = [0_u8; 2];
        stream.read_exact(&mut header).unwrap();
        let mut body = vec![0_u8; usize::from(u16::from_be_bytes(header))];
        stream.read_exact(&mut body).unwrap();
        let mut end = [0_u8; 1];
        assert_eq!(stream.read(&mut end).unwrap(), 0);
        body
    }

    pub(super) fn serve_daemon(
        root: &Path,
        socket_name: &str,
        expected: &'static [u8],
        response: &[u8],
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join(socket_name)).unwrap();
        let response = response.to_vec();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut stream), expected);
            let frame = framed(&response);
            for chunk in frame.chunks(frame.len().div_ceil(2)) {
                stream.write_all(chunk).unwrap();
            }
            stream.shutdown(std::net::Shutdown::Write).unwrap();
        })
    }

    type DaemonExchange = (&'static [u8], Option<&'static [u8]>, Duration);

    fn serve_daemon_sequence(
        root: &Path,
        socket_name: &str,
        exchanges: Vec<DaemonExchange>,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join(socket_name)).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            for (expected, response, delay) in exchanges {
                let until = Instant::now() + Duration::from_secs(2);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                            assert!(Instant::now() < until, "expected fixture request absent");
                            thread::sleep(Duration::from_millis(1));
                        }
                        Err(error) => panic!("fixture accept: {error}"),
                    }
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                stream
                    .set_write_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                assert_eq!(read_request(&mut stream), expected);
                thread::sleep(delay);
                if let Some(response) = response {
                    match stream.write_all(&framed(response)) {
                        Ok(()) => stream.shutdown(std::net::Shutdown::Write).unwrap(),
                        // A deadline test deliberately closes before the valid reply.
                        Err(error) => assert!(matches!(
                            error.kind(),
                            io::ErrorKind::BrokenPipe | io::ErrorKind::ConnectionReset
                        )),
                    }
                }
            }
        })
    }

    fn heartbeat_backend(root: &Path) -> RaptorBackend {
        let uptime = root.join("uptime");
        fs::write(&uptime, b"123.5 12.0\n").unwrap();
        let mut backend = backend(root, "127.0.0.1:9".parse().unwrap());
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            uptime,
            ..crate::camera::CameraPaths::default()
        });
        backend
    }

    fn heartbeat_value(backend: &RaptorBackend, deadline: Instant) -> Value {
        let response = backend.heartbeat(deadline).unwrap();
        crate::json::parse(&response.body).unwrap()
    }

    pub(super) fn backend(root: &Path, snapshot_address: SocketAddr) -> RaptorBackend {
        RaptorBackend::new(
            root.to_path_buf(),
            snapshot_address,
            root.join("prudynt.sock"),
            root.join("prudynt.pid"),
        )
        .unwrap()
    }

    pub(super) const RVD_OK: &[u8] = br#"{"status":"ok","streams":[{"available":true,"chn":0,"w":1920,"h":1080,"codec":0,"bitrate":3000000,"avg_bitrate":1,"gop":25,"fps":15},{"available":true,"chn":1,"w":640,"h":360,"codec":0,"bitrate":750000,"avg_bitrate":1,"gop":25,"fps":15},{"available":true,"chn":2,"w":1920,"h":1080,"codec":2,"bitrate":0,"avg_bitrate":1,"gop":0,"fps":1},{"available":true,"chn":3,"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":1,"gop":0,"fps":1}]}"#;
    pub(super) const RHD_OK: &[u8] = br#"{"status":"ok","clients":0,"mjpeg":0,"audio":0,"port":8080,"jpeg_rings":2,"jpeg_available":[true,true],"exif_timestamp":false,"sign_snapshots":false,"privacy":false,"tls":false}"#;
    const RSD_OK: &[u8] =
        br#"{"status":"ok","clients":0,"port":8554,"tls":false,"endpoints":["stream0","stream1"]}"#;
    const HEARTBEAT_MOTION_OK: &[u8] =
        br#"{"status":"ok","active":false,"motion":false,"supported":true,"receiving":false}"#;
    const HEARTBEAT_PRIVACY_AUDIO: &[u8] =
        br#"{"status":"ok","supported":true,"video":[true,true],"audio_required":true,"jpeg_required":false}"#;
    const HEARTBEAT_PRIVACY_ALL: &[u8] =
        br#"{"status":"ok","supported":true,"video":[true,true],"audio_required":true,"jpeg_required":true}"#;
    const HEARTBEAT_PRIVACY_VIDEO_ONLY: &[u8] =
        br#"{"status":"ok","supported":true,"video":[true,true],"audio_required":false,"jpeg_required":false}"#;
    const HEARTBEAT_RHD_PRIVACY: &[u8] = br#"{"status":"ok","privacy":true}"#;
    const HEARTBEAT_RAD_MUTED: &[u8] = br#"{"status":"ok","muted":true}"#;
    const HEARTBEAT_RAD_UNMUTED: &[u8] = br#"{"status":"ok","muted":false}"#;

    fn serve_heartbeat_rvd(
        root: &Path,
        privacy_response: Option<&'static [u8]>,
        privacy_delay: Duration,
    ) -> thread::JoinHandle<()> {
        serve_daemon_sequence(
            root,
            "rvd.sock",
            vec![
                (
                    br#"{"cmd":"ivs-status"}"#,
                    Some(HEARTBEAT_MOTION_OK),
                    Duration::ZERO,
                ),
                (
                    br#"{"cmd":"privacy-status"}"#,
                    privacy_response,
                    privacy_delay,
                ),
            ],
        )
    }

    #[test]
    fn status_capabilities_are_mapped_from_strict_rvd_and_rhd_replies() {
        let root = task_temp("status");
        let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, RVD_OK);
        let rhd = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, RHD_OK);
        let rsd = serve_daemon(&root, "rsd.sock", br#"{"cmd":"status"}"#, RSD_OK);
        let result = backend(&root, "127.0.0.1:9".parse().unwrap())
            .media_runtime(Instant::now() + Duration::from_secs(1))
            .unwrap();
        assert_eq!(
            result.body,
            b"{\"streams\":{\"ch0\":{\"available\":true,\"enabled\":true,\"snapshot_url\":\"/api/v1/actions/snapshot?stream_id=0\",\"width\":1920,\"height\":1080,\"format\":\"H264\",\"fps\":15,\"rtsp_endpoint\":\"stream0\"},\"ch1\":{\"available\":true,\"enabled\":true,\"snapshot_url\":\"/api/v1/actions/snapshot?stream_id=1\",\"width\":640,\"height\":360,\"format\":\"H264\",\"fps\":15,\"rtsp_endpoint\":\"stream1\"}},\"rtsp\":{\"port\":8554}}\n"
        );
        rvd.join().unwrap();
        rhd.join().unwrap();
        rsd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rtsp_uses_observed_port_and_paths_without_affecting_rhd_images() {
        for reply in [
            br#"{"status":"ok","clients":0,"port":9554,"tls":false,"endpoints":["front-door","low-res"]}"#.as_slice(),
            br#"{"status":"error"}"#,
            br#"{"status":"ok","clients":0,"port":8554,"tls":false}"#,
        ] {
            let root = task_temp("rtsp-runtime");
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, RVD_OK);
            let rhd = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, RHD_OK);
            let rsd = serve_daemon(&root, "rsd.sock", br#"{"cmd":"status"}"#, reply);
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .media_runtime(Instant::now() + Duration::from_secs(2)).unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(value.get_path("streams.ch0.available"), Some(&Value::Bool(true)));
            assert_eq!(value.get_path("streams.ch1.available"), Some(&Value::Bool(true)));
            if reply.windows(4).any(|window| window == b"9554") {
                assert_eq!(value.get_path("rtsp.port"), Some(&Value::Number("9554".into())));
                assert_eq!(value.get_path("streams.ch0.rtsp_endpoint").and_then(Value::as_str), Some("front-door"));
            } else {
                assert!(value.get_path("rtsp").is_none());
                assert!(value.get_path("streams.ch0.rtsp_endpoint").is_none());
            }
            rvd.join().unwrap();
            rhd.join().unwrap();
            rsd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn legacy_aggregate_status_does_not_invent_observed_stream_availability() {
        let root = task_temp("legacy-availability");
        let video = String::from_utf8(RVD_OK.to_vec())
            .unwrap()
            .replace("\"available\":true,", "");
        let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, video.as_bytes());
        let state = backend(&root, "127.0.0.1:9".parse().unwrap())
            .media_state(Instant::now() + Duration::from_secs(1))
            .unwrap();
        assert!(
            state
                .streams
                .into_iter()
                .flatten()
                .all(|stream| !stream.available)
        );
        rvd.join().unwrap();
        for count in 0..=2 {
            let body = String::from_utf8(RHD_OK.to_vec())
                .unwrap()
                .replace(",\"jpeg_available\":[true,true]", "")
                .replace("\"jpeg_rings\":2", &format!("\"jpeg_rings\":{count}"));
            let rhd = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, body.as_bytes());
            let reply = backend(&root, "127.0.0.1:9".parse().unwrap())
                .command(
                    RaptorDaemon::Rhd,
                    br#"{"cmd":"status"}"#,
                    Instant::now() + Duration::from_secs(1),
                )
                .unwrap();
            assert_eq!(validate_rhd_status(&reply).unwrap(), [false; 2]);
            rhd.join().unwrap();
            fs::remove_file(root.join("rhd.sock")).unwrap();
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn media_health_reports_zero_or_one_working_stream_without_inventing_activity() {
        for (available, jpeg_available) in [(false, false), (true, false), (true, true)] {
            let root = task_temp("health-availability");
            let video = format!(
                r#"{{"status":"ok","configured":[false,true],"streams":[{{"chn":1,"stream_id":1,"jpeg":false,"available":{available},"w":640,"h":360,"codec":0,"bitrate":750000,"avg_bitrate":0,"gop":25,"fps":15}},{{"chn":3,"stream_id":1,"jpeg":true,"available":{jpeg_available},"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":0,"gop":0,"fps":1}}]}}"#
            );
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, video.as_bytes());
            let rhd = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, RHD_OK);
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .media_health(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            for key in ["checks.system.streamer_running", "checks.streaming.running"] {
                assert_eq!(
                    value.get_path(key).and_then(Value::as_bool),
                    Some(available)
                );
            }
            for key in ["healthy", "checks.streaming.healthy"] {
                assert_eq!(
                    value.get_path(key).and_then(Value::as_bool),
                    Some(available && jpeg_available)
                );
            }
            assert_eq!(
                value.get_path("checks.streaming.streams_enabled"),
                Some(&Value::Number("1".into()))
            );
            rvd.join().unwrap();
            rhd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn rvd_fps_flags_hide_unknown_rate_without_losing_other_streams() {
        for (flags, fps, available, valid) in [
            (
                ",\"fps_recovery_required\":true,\"fps_persistence_pending\":false",
                "null",
                false,
                true,
            ),
            (
                ",\"fps_recovery_required\":false,\"fps_persistence_pending\":true",
                "null",
                false,
                true,
            ),
            (
                ",\"fps_recovery_required\":true,\"fps_persistence_pending\":false",
                "15",
                false,
                false,
            ),
            (
                ",\"fps_recovery_required\":true,\"fps_persistence_pending\":false",
                "null",
                true,
                false,
            ),
            (",\"fps_recovery_required\":true", "null", false, false),
        ] {
            let body = String::from_utf8(RVD_OK.to_vec())
                .unwrap()
                .replacen("\"fps\":15", &format!("\"fps\":{fps}{flags}"), 1)
                .replacen(
                    "\"available\":true",
                    &format!("\"available\":{available}"),
                    1,
                );
            let root = task_temp("fps-runtime");
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, body.as_bytes());
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .media_state(Instant::now() + Duration::from_secs(1));
            assert_eq!(result.is_ok(), valid);
            if let Ok(state) = result {
                assert!(state.streams[0].unwrap().fps.is_none());
                assert!(!state.streams[0].unwrap().available);
                assert_eq!(state.streams[1].unwrap().fps, Some(15));
            }
            rvd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn rvd_status_rejects_invalid_codec_and_frame_rate() {
        for reply in [
            br#"{"status":"ok","streams":[{"chn":0,"w":1920,"h":1080,"codec":"h264","bitrate":3000000,"avg_bitrate":1,"gop":25,"fps":15},{"chn":1,"w":640,"h":360,"codec":0,"bitrate":750000,"avg_bitrate":1,"gop":25,"fps":15},{"chn":2,"w":1920,"h":1080,"codec":2,"bitrate":0,"avg_bitrate":1,"gop":0,"fps":1},{"chn":3,"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":1,"gop":0,"fps":1}]}"#.as_slice(),
            br#"{"status":"ok","streams":[{"chn":0,"w":1920,"h":1080,"codec":0,"bitrate":3000000,"avg_bitrate":1,"gop":25,"fps":31},{"chn":1,"w":640,"h":360,"codec":0,"bitrate":750000,"avg_bitrate":1,"gop":25,"fps":15}]}"#.as_slice(),
        ] {
            let root = task_temp("strict-rvd-profile");
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, reply);
            assert!(matches!(
                backend(&root, "127.0.0.1:9".parse().unwrap())
                    .media_health(Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
            rvd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn daynight_uses_the_exact_ric_mode_command() {
        for readback_case in [0, 1, 2] {
            let root = task_temp("daynight");
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let ric = thread::spawn(move || {
                for (index, expected) in [
                    br#"{"cmd":"mode","value":"night"}"#.as_slice(),
                    br#"{"cmd":"mode"}"#.as_slice(),
                ]
                .iter()
                .enumerate()
                {
                    let (mut stream, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut stream), *expected);
                    let body = if index == 1 && readback_case == 1 {
                        br#"{"status":"ok","mode":"day","state":"day"}"#.as_slice()
                    } else if index == 1 && readback_case == 2 {
                        br#"{"status":"ok","mode":"night","state":"day"}"#.as_slice()
                    } else {
                        br#"{"status":"ok","mode":"night","state":"night"}"#.as_slice()
                    };
                    stream.write_all(&framed(body)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .set_daynight_mode(DayNightMode::Night, Instant::now() + Duration::from_secs(1));
            if readback_case == 0 {
                assert_eq!(
                    result.unwrap().body,
                    b"{\"status\":\"accepted\",\"mode\":\"night\"}\n"
                );
            } else {
                assert_eq!(result, Err(BackendError::Protocol));
            }
            ric.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn automatic_daynight_readback_does_not_require_isp_target_equality() {
        let root = task_temp("automatic-daynight-independent-outputs");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let ric = thread::spawn(move || {
            for expected in [
                br#"{"cmd":"mode","value":"auto"}"#.as_slice(),
                br#"{"cmd":"mode"}"#.as_slice(),
            ] {
                let (mut stream, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut stream), expected);
                stream
                    .write_all(&framed(br#"{"status":"ok","mode":"auto","state":"night"}"#))
                    .unwrap();
            }
        });

        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .set_daynight_mode(DayNightMode::Auto, Instant::now() + Duration::from_secs(1))
            .unwrap();
        assert_eq!(
            response.body,
            b"{\"status\":\"accepted\",\"mode\":\"auto\"}\n"
        );
        ric.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn snapshot_is_bounded_in_memory_and_validates_the_http_reply() {
        for stream_id in [0, 1] {
            let root = task_temp("snapshot");
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let server = thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = Vec::new();
                while find_bytes(&request, b"\r\n\r\n").is_none() {
                    let mut byte = [0_u8; 1];
                    stream.read_exact(&mut byte).unwrap();
                    request.push(byte[0]);
                }
                assert!(request.starts_with(
                    format!("GET /snap.jpg?stream={stream_id} HTTP/1.1\r\n").as_bytes()
                ));
                let response = b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 6\r\nConnection: close\r\n\r\n\xff\xd8ab\xff\xd9";
                for chunk in response.chunks(3) {
                    stream.write_all(chunk).unwrap();
                }
            });
            let result = backend(&root, address)
                .capture_snapshot(stream_id, Instant::now() + Duration::from_secs(1))
                .unwrap();
            assert_eq!(result.content_type, "image/jpeg");
            assert_eq!(result.body, b"\xff\xd8ab\xff\xd9");
            server.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn conflicting_media_owner_and_unsupported_config_fail_closed() {
        let root = task_temp("ownership");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        fs::write(root.join("prudynt.pid"), b"123\n").unwrap();
        assert!(matches!(
            backend.media_health(Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Unavailable)
        ));
        assert!(matches!(
            backend.media_config(Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Unavailable)
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn proxy_media_allowlist_matches_proven_ipv4_mapping_only() {
        let root = task_temp("proxy-mapping");
        let mapped = backend(&root, "127.0.0.1:8080".parse().unwrap());
        for target in [
            "/api/v1/actions/snapshot?stream_id=0",
            "/api/v1/actions/snapshot?stream_id=1",
            "/media/v1/mjpeg?stream=0",
            "/media/v1/mjpeg?stream=1&f=1",
            "/media/v1/mjpeg?stream=1&f=30",
        ] {
            assert!(mapped.authorize_media(target), "{target}");
        }
        for target in [
            "/onvif/image.cgi",
            "/media/v1/file?path=/mnt/a",
            "/media/v1/osd-sei",
            "/api/v1/actions/snapshot?stream_id=1&download=1",
            "/media/v1/mjpeg?stream=2",
            "/media/v1/mjpeg?stream=01",
            "/media/v1/mjpeg?stream=1&",
            "/media/v1/mjpeg?stream=1&f=0",
            "/media/v1/mjpeg?stream=1&f=31",
            "/media/v1/mjpeg?stream=1&f=01",
            "/media/v1/mjpeg?stream=1&f=2&f=3",
            "/media/v1/mjpeg?stream=1&q=51",
            "/media/v1/mjpeg?stream=%31",
            "/media/v1/mjpeg?stream=1&f=+1",
        ] {
            assert!(!mapped.authorize_media(target), "{target}");
        }
        let custom = backend(&root, "127.0.0.1:8088".parse().unwrap());
        assert!(!custom.authorize_media("/media/v1/mjpeg?stream=0"));
        fs::write(root.join("prudynt.pid"), b"123\n").unwrap();
        assert!(!mapped.authorize_media("/media/v1/mjpeg?stream=0"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn typed_status_and_snapshot_headers_reject_unknown_or_oversized_inputs() {
        let root = task_temp("strict");
        let rvd = serve_daemon(
            &root,
            "rvd.sock",
            br#"{"cmd":"status"}"#,
            br#"{"status":"ok","streams":[],"unexpected":true}"#,
        );
        assert!(matches!(
            backend(&root, "127.0.0.1:9".parse().unwrap())
                .media_health(Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Protocol)
        ));
        rvd.join().unwrap();
        assert!(matches!(
            parse_snapshot_headers(&format!(
                "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                MAX_SNAPSHOT_BYTES + 1
            )),
            Err(BackendError::Protocol)
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unsupported_capabilities_are_unavailable_but_unknown_routes_remain_unknown() {
        let root = task_temp("unsupported");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for target in [
            "/api/v1/runtime/media/metrics",
            "/api/v1/actions/prudynt/restart",
        ] {
            assert!(matches!(
                backend.api_request("GET", target, b"", Instant::now() + Duration::from_secs(1)),
                Some(Err(BackendError::Unsupported(_)))
            ));
        }
        assert!(
            backend
                .api_request(
                    "GET",
                    "/api/v1/not-a-route",
                    b"",
                    Instant::now() + Duration::from_secs(1)
                )
                .is_none()
        );
        assert!(!backend.authorize_media("/api/v1/actions/snapshot?stream_id=0"));
        assert!(!backend.authorize_media("/media/v1/mjpeg?stream=0"));
        assert!(!backend.authorize_media("/media/v1/mjpeg?stream=1"));
        for rejected in [
            "/media/v1/mjpeg?stream=2",
            "/media/v1/mjpeg?stream=0&stream=1",
            "/media/v1/mjpeg?stream=0&q=51",
            "/media/v1/mjpeg?stream=0&unknown=1",
            "/media/v1/mjpeg?stream=0000000000000000000000000000000000000000000",
        ] {
            assert!(!backend.authorize_media(rejected), "{rejected}");
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn constructor_and_later_calls_reject_conflicts_and_remote_snapshots() {
        let root = task_temp("exclusive");
        assert!(matches!(
            RaptorBackend::new(
                root.clone(),
                "192.0.2.1:8088".parse().unwrap(),
                root.join("p.sock"),
                root.join("p.pid")
            ),
            Err(BackendError::Protocol)
        ));
        fs::write(root.join("prudynt.pid"), b"123\n").unwrap();
        assert!(matches!(
            RaptorBackend::new(
                root.clone(),
                "127.0.0.1:8088".parse().unwrap(),
                root.join("prudynt.sock"),
                root.join("prudynt.pid")
            ),
            Err(BackendError::Unavailable)
        ));
        fs::remove_file(root.join("prudynt.pid")).unwrap();
        let backend = backend(&root, "127.0.0.1:8088".parse().unwrap());
        fs::write(root.join("prudynt.sock"), b"conflict").unwrap();
        for route in [
            BackendRoute::Health,
            BackendRoute::RuntimeMedia,
            BackendRoute::Config,
            BackendRoute::Snapshot(0),
            BackendRoute::DayNight(DayNightMode::Night),
        ] {
            assert_eq!(
                backend.request(route, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Unavailable)
            );
        }
        assert!(!backend.authorize_media("/media/v1/mjpeg?stream=0"));
        assert!(
            backend
                .api_request("GET", "/api/v1/config/unknown", b"", Instant::now())
                .is_none()
        );
        assert!(
            backend
                .api_request("GET", "/api/v1/prudynt-unknown", b"", Instant::now())
                .is_none()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_rhd_metadata_and_duplicate_snapshot_headers_fail_closed() {
        for body in [
            br#"{"status":"ok","port":8088,"jpeg_rings":2,"tls":false}"#.as_slice(),
            br#"{"status":"ok","clients":0,"mjpeg":0,"audio":0,"port":8088,"jpeg_rings":2,"jpeg_available":[true,true],"exif_timestamp":false,"sign_snapshots":false,"privacy":"false","tls":false}"#.as_slice(),
        ] {
            let root = task_temp("rhd");
            let server = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, body);
            let reply = backend(&root, "127.0.0.1:8088".parse().unwrap())
                .command(RaptorDaemon::Rhd, br#"{"cmd":"status"}"#, Instant::now() + Duration::from_secs(1)).unwrap();
            assert!(validate_rhd_status(&reply).is_err());
            server.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
        assert!(parse_snapshot_headers("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n").is_err());
    }

    #[test]
    fn router_maps_malformed_raptor_to_502_and_deadline_to_504() {
        use std::sync::{Arc, atomic::AtomicBool};
        struct ShortBudget(RaptorBackend);
        impl Backend for ShortBudget {
            fn request(
                &self,
                route: BackendRoute,
                deadline: Instant,
            ) -> Result<BackendResponse, BackendError> {
                self.0.request(
                    route,
                    deadline.min(Instant::now() + Duration::from_millis(80)),
                )
            }
        }
        for (stall, expected) in [(false, "HTTP/1.1 502"), (true, "HTTP/1.1 504")] {
            let root = task_temp("http-errors");
            let upstream = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let daemon = thread::spawn(move || {
                let (mut stream, _) = upstream.accept().unwrap();
                let _ = read_request(&mut stream);
                if stall {
                    thread::sleep(Duration::from_millis(150));
                } else {
                    stream
                        .write_all(&framed(br#"{"status":"ok","streams":"invalid"}"#))
                        .unwrap();
                }
            });
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let stopped = Arc::new(AtomicBool::new(false));
            let flag = stopped.clone();
            let control_backend = Arc::new(ShortBudget(backend(
                &root,
                "127.0.0.1:8088".parse().unwrap(),
            )));
            let control = thread::spawn(move || {
                crate::serve(listener, control_backend, b"fixture".to_vec(), flag).unwrap()
            });
            let mut client = TcpStream::connect(address).unwrap();
            client
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            client.write_all(b"GET /api/v1/health HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer fixture\r\nConnection: close\r\n\r\n").unwrap();
            let mut reply = String::new();
            client.read_to_string(&mut reply).unwrap();
            stopped.store(true, Ordering::Release);
            control.join().unwrap();
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
            assert!(reply.starts_with(expected), "{reply}");
        }
    }
    #[test]
    fn optional_status_timeout_preserves_both_snapshot_streams() {
        let root = task_temp("optional");
        let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, RVD_OK);
        let rhd = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, RHD_OK);
        let listener = UnixListener::bind(root.join("rsd.sock")).unwrap();
        let rsd = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut socket), br#"{"cmd":"status"}"#);
            thread::sleep(Duration::from_millis(350));
        });
        let start = Instant::now();
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .media_runtime(start + Duration::from_secs(2))
            .unwrap();
        assert!(start.elapsed() < Duration::from_millis(300));
        let value = crate::json::parse(&response.body).unwrap();
        assert!(value.get_path("rtsp").is_none());
        for channel in ["ch0", "ch1"] {
            assert_eq!(
                value
                    .get_path(&format!("streams.{channel}.available"))
                    .and_then(Value::as_bool),
                Some(true)
            );
        }
        rvd.join().unwrap();
        rhd.join().unwrap();
        rsd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_delayed_ric_readback_over_150ms_still_succeeds() {
        let root = task_temp("heartbeat-ric-budget");
        let ric = serve_daemon_sequence(
            &root,
            "ric.sock",
            vec![(
                br#"{"cmd":"mode"}"#,
                Some(br#"{"status":"ok","mode":"day","state":"day"}"#),
                Duration::from_millis(220),
            )],
        );
        let value = heartbeat_value(
            &heartbeat_backend(&root),
            Instant::now() + Duration::from_secs(2),
        );
        assert_eq!(
            value.get_path("daynight_mode").and_then(Value::as_str),
            Some("day")
        );
        ric.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_privacy_allows_sequential_required_peer_delays() {
        let root = task_temp("heartbeat-privacy-budget");
        let rvd = serve_heartbeat_rvd(
            &root,
            Some(HEARTBEAT_PRIVACY_ALL),
            Duration::from_millis(100),
        );
        let rhd = serve_daemon_sequence(
            &root,
            "rhd.sock",
            vec![(
                br#"{"cmd":"status"}"#,
                Some(HEARTBEAT_RHD_PRIVACY),
                Duration::from_millis(100),
            )],
        );
        let rad = serve_daemon_sequence(
            &root,
            "rad.sock",
            vec![(
                br#"{"cmd":"status"}"#,
                Some(HEARTBEAT_RAD_MUTED),
                Duration::from_millis(100),
            )],
        );
        let value = heartbeat_value(
            &heartbeat_backend(&root),
            Instant::now() + Duration::from_secs(2),
        );
        assert_eq!(value.get_path("privacy_enabled"), Some(&Value::Bool(true)));
        rvd.join().unwrap();
        rhd.join().unwrap();
        rad.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_stalled_ric_keeps_privacy_observable() {
        let root = task_temp("heartbeat-ric-stalled");
        let ric = serve_daemon_sequence(
            &root,
            "ric.sock",
            vec![(br#"{"cmd":"mode"}"#, None, Duration::from_millis(350))],
        );
        let rvd = serve_heartbeat_rvd(&root, Some(HEARTBEAT_PRIVACY_VIDEO_ONLY), Duration::ZERO);
        let value = heartbeat_value(
            &heartbeat_backend(&root),
            Instant::now() + Duration::from_secs(2),
        );
        assert_eq!(
            value.get_path("daynight_mode").and_then(Value::as_str),
            Some("unknown")
        );
        assert_eq!(value.get_path("privacy_enabled"), Some(&Value::Bool(true)));
        ric.join().unwrap();
        rvd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_stalled_privacy_stays_unknown() {
        let root = task_temp("heartbeat-privacy-stalled");
        let rvd = serve_heartbeat_rvd(&root, None, Duration::from_millis(700));
        let value = heartbeat_value(
            &heartbeat_backend(&root),
            Instant::now() + Duration::from_secs(2),
        );
        assert_eq!(value.get_path("privacy_enabled"), Some(&Value::Null));
        rvd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_mismatching_privacy_stays_unknown() {
        let root = task_temp("heartbeat-privacy-mismatch");
        let rvd = serve_heartbeat_rvd(&root, Some(HEARTBEAT_PRIVACY_AUDIO), Duration::ZERO);
        let rad = serve_daemon_sequence(
            &root,
            "rad.sock",
            vec![(
                br#"{"cmd":"status"}"#,
                Some(HEARTBEAT_RAD_UNMUTED),
                Duration::ZERO,
            )],
        );
        let value = heartbeat_value(
            &heartbeat_backend(&root),
            Instant::now() + Duration::from_secs(2),
        );
        assert_eq!(value.get_path("privacy_enabled"), Some(&Value::Null));
        rvd.join().unwrap();
        rad.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_short_global_deadline_bounds_extended_read_budgets() {
        let root = task_temp("heartbeat-short-deadline");
        let ric = serve_daemon_sequence(
            &root,
            "ric.sock",
            vec![(
                br#"{"cmd":"mode"}"#,
                Some(br#"{"status":"ok","mode":"day","state":"day"}"#),
                Duration::from_millis(220),
            )],
        );
        let backend = heartbeat_backend(&root);
        let started = Instant::now();
        let value = heartbeat_value(&backend, started + Duration::from_millis(50));
        assert!(started.elapsed() < Duration::from_millis(500));
        assert_eq!(
            value.get_path("daynight_mode").and_then(Value::as_str),
            Some("unknown")
        );
        assert_eq!(value.get_path("privacy_enabled"), Some(&Value::Null));
        ric.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn heartbeat_short_global_deadline_bounds_privacy_read() {
        let root = task_temp("heartbeat-privacy-deadline");
        let rvd = serve_heartbeat_rvd(
            &root,
            Some(HEARTBEAT_PRIVACY_VIDEO_ONLY),
            Duration::from_millis(220),
        );
        let backend = heartbeat_backend(&root);
        let started = Instant::now();
        let value = heartbeat_value(&backend, started + Duration::from_millis(100));
        assert!(started.elapsed() < Duration::from_millis(500));
        assert_eq!(value.get_path("privacy_enabled"), Some(&Value::Null));
        rvd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn video_availability_is_independent_of_jpeg_producer_and_reader() {
        for (video, jpeg, reader) in [
            (true, false, true),
            (true, true, false),
            (false, true, true),
            (true, true, true),
        ] {
            let root = task_temp("video-jpeg-independent");
            let body = format!(
                r#"{{"status":"ok","configured":[false,true],"streams":[{{"chn":1,"stream_id":1,"jpeg":false,"available":{video},"w":640,"h":360,"codec":0,"bitrate":750000,"avg_bitrate":0,"gop":25,"fps":15}},{{"chn":3,"stream_id":1,"jpeg":true,"available":{jpeg},"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":0,"gop":0,"fps":1}}]}}"#
            );
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let rvd = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"status"}"#);
                socket.write_all(&framed(body.as_bytes())).unwrap();
            });
            let body = String::from_utf8(RHD_OK.to_vec())
                .unwrap()
                .replace("[true,true]", &format!("[true,{reader}]"));
            let listener = UnixListener::bind(root.join("rhd.sock")).unwrap();
            let rhd = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"status"}"#);
                socket.write_all(&framed(body.as_bytes())).unwrap();
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .media_runtime(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(
                value.get_path("streams.ch1.available"),
                Some(&Value::Bool(video))
            );
            let expected = if video && jpeg && reader {
                Value::String("/api/v1/actions/snapshot?stream_id=1".into())
            } else {
                Value::Null
            };
            assert_eq!(value.get_path("streams.ch1.snapshot_url"), Some(&expected));
            assert_eq!(
                value.get_path("streams.ch0.available"),
                Some(&Value::Bool(false))
            );
            rvd.join().unwrap();
            rhd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn media_stream_failures_and_supported_configuration_changes_are_independent() {
        for (name, sub_enabled, sub_active, jpeg, fps, width, height) in [
            ("main-only", false, false, [true, false], 15, 1920, 1080),
            ("failed-sub", true, false, [true, false], 15, 1920, 1080),
            (
                "missing-main-jpeg",
                true,
                true,
                [false, true],
                15,
                1920,
                1080,
            ),
            (
                "missing-sub-jpeg",
                true,
                true,
                [true, false],
                15,
                1920,
                1080,
            ),
            (
                "configured-h265-20fps",
                true,
                true,
                [true, true],
                20,
                1280,
                720,
            ),
        ] {
            let root = task_temp(name);
            let mut entries = vec![format!(
                r#"{{"chn":0,"stream_id":0,"jpeg":false,"available":true,"w":{width},"h":{height},"codec":1,"bitrate":3000000,"avg_bitrate":1,"gop":40,"fps":{fps}}}"#
            )];
            if sub_enabled {
                entries.push(format!(r#"{{"chn":1,"stream_id":1,"jpeg":false,"available":{sub_active},"w":640,"h":360,"codec":0,"bitrate":750000,"avg_bitrate":1,"gop":25,"fps":15}}"#));
            }
            for (id, present) in jpeg.into_iter().enumerate() {
                if present {
                    // With only main enabled, JPEG's hardware channel is 1.
                    // Its semantic stream id still remains 0.
                    let channel = if !sub_enabled { 1 } else { 2 + id };
                    entries.push(format!(r#"{{"chn":{channel},"stream_id":{id},"jpeg":true,"available":true,"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":0,"gop":0,"fps":1}}"#));
                }
            }
            let body = format!(
                r#"{{"status":"ok","configured":[true,{sub_enabled}],"streams":[{}]}}"#,
                entries.join(",")
            );
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let rvd = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"status"}"#);
                socket.write_all(&framed(body.as_bytes())).unwrap();
            });
            let body = String::from_utf8(RHD_OK.to_vec())
                .unwrap()
                .replace("[true,true]", &format!("[{},{}]", jpeg[0], jpeg[1]));
            let listener = UnixListener::bind(root.join("rhd.sock")).unwrap();
            let rhd = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"status"}"#);
                socket.write_all(&framed(body.as_bytes())).unwrap();
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .media_runtime(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let observed = crate::json::parse(&response.body).unwrap();
            for (id, enabled, available) in
                [(0, true, true), (1, sub_enabled, sub_enabled && sub_active)]
            {
                assert_eq!(
                    observed.get_path(&format!("streams.ch{id}.enabled")),
                    Some(&Value::Bool(enabled)),
                    "{name}"
                );
                assert_eq!(
                    observed.get_path(&format!("streams.ch{id}.available")),
                    Some(&Value::Bool(available)),
                    "{name}"
                );
                if !available || !jpeg[id] {
                    assert_eq!(
                        observed.get_path(&format!("streams.ch{id}.snapshot_url")),
                        Some(&Value::Null),
                        "{name}"
                    );
                }
            }
            assert_eq!(
                observed.get_path("streams.ch0.fps"),
                Some(&Value::Number(fps.to_string()))
            );
            assert_eq!(
                observed.get_path("streams.ch0.format"),
                Some(&Value::String("H265".into()))
            );
            rvd.join().unwrap();
            rhd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn motion_requires_matching_independent_readback() {
        for agrees in [true, false] {
            let root = task_temp("motion");
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let daemon = thread::spawn(move || {
                let exchanges = [
                    (br#"{"cmd":"ivs-status"}"#.to_vec(), br#"{"status":"ok","active":false,"motion":false,"supported":true,"receiving":false}"#.to_vec()),
                    (br#"{"cmd":"ivs-enable","value":true}"#.to_vec(), br#"{"status":"ok","active":true}"#.to_vec()),
                    (br#"{"cmd":"ivs-status"}"#.to_vec(), format!("{{\"status\":\"ok\",\"active\":{agrees},\"motion\":false,\"supported\":true,\"receiving\":{agrees}}}").into_bytes()),
                ];
                for (request, response) in exchanges {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).live_control(
                br#"{"motion":{"enabled":true}}"#,
                Instant::now() + Duration::from_secs(2),
            );
            assert_eq!(result.is_ok(), agrees);
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn imaging_readback_preserves_unavailable_without_forwarding_extra_fields() {
        let root = task_temp("imaging");
        let daemon = serve_daemon(&root, "rvd.sock", br#"{"cmd":"get-imaging"}"#,
            br#"{"status":"ok","fields":{"brightness":{"supported":true,"available":false,"value":42,"min":0,"max":255},"contrast":{"supported":true,"available":true,"value":42,"min":0,"max":255},"saturation":{"supported":false,"available":false,"value":null,"min":0,"max":255},"sharpness":{"supported":true,"available":true,"value":42,"min":0,"max":255},"backlight":{"supported":true,"available":true,"value":4,"min":0,"max":10},"wide_dynamic_range":{"supported":true,"available":true,"value":150,"min":0,"max":255},"tone":{"supported":true,"available":true,"value":20,"min":0,"max":255},"defog":{"supported":true,"available":true,"value":130,"min":0,"max":255},"noise_reduction":{"supported":true,"available":true,"value":110,"min":0,"max":255},"unexpected":{"secret":"do not forward"}}}"#);
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .imaging(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("persistent"), Some(&Value::Bool(false)));
        assert_eq!(
            value.get_path("message.fields.brightness.value"),
            Some(&Value::Null)
        );
        assert!(value.get_path("message.fields.unexpected").is_none());
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn privacy_requires_all_configured_participants_to_agree() {
        for agrees in [true, false] {
            let root = task_temp("privacy");
            let video = serve_daemon(&root, "rvd.sock", br#"{"cmd":"privacy-status"}"#,
                br#"{"status":"ok","supported":true,"video":[true,true],"audio_required":true,"jpeg_required":false}"#);
            let audio = serve_daemon(
                &root,
                "rad.sock",
                br#"{"cmd":"status"}"#,
                if agrees {
                    br#"{"status":"ok","muted":true}"#
                } else {
                    br#"{"status":"ok","muted":false}"#
                },
            );
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .privacy_state(Instant::now() + Duration::from_secs(1));
            assert_eq!(result.is_ok(), agrees);
            video.join().unwrap();
            audio.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
        let root = task_temp("privacy-absent");
        let video = serve_daemon(&root, "rvd.sock", br#"{"cmd":"privacy-status"}"#,
            br#"{"status":"ok","supported":true,"video":[true,true],"audio_required":true,"jpeg_required":false}"#);
        assert!(
            backend(&root, "127.0.0.1:9".parse().unwrap())
                .privacy_state(Instant::now() + Duration::from_secs(1))
                .is_err()
        );
        video.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn host_routes_do_not_require_a_media_owner() {
        let root = task_temp("host");
        fs::write(root.join("uptime"), b"123.5 12.0\n").unwrap();
        let mut backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            uptime: root.join("uptime"),
            ..crate::camera::CameraPaths::default()
        });
        let response = backend
            .heartbeat(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("uptime"),
            Some(&Value::Number("123".to_owned()))
        );
        assert_eq!(value.get_path("motion_enabled"), Some(&Value::Null));
        assert_eq!(
            value.get_path("controls_supported.motion"),
            Some(&Value::Bool(true))
        );
        assert!(
            backend
                .host
                .api_request("POST", "/api/v1/config/time", b"{}", Instant::now())
                .is_none()
        );
        assert!(
            backend
                .host
                .api_request(
                    "POST",
                    "/api/v1/actions/prudynt/restart",
                    b"",
                    Instant::now()
                )
                .is_none()
        );
        fs::remove_dir_all(root).unwrap();
    }
}
