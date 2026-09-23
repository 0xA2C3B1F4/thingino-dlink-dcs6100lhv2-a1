use super::motion_roi::{ROI_KEYS, SingleRoi};
use super::*;
use std::sync::Arc;

impl RaptorBackend {
    pub(super) fn motion_action_config(
        &self,
        deadline: Instant,
    ) -> Result<super::motion_actions::Config, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"get-motion-actions"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        reply.require_only_fields(&[
            "status",
            "persistence",
            "video_length",
            "send2storage",
            "playonspeaker",
            "speaker_repeats",
        ])?;
        if reply.value.get_path("persistence").and_then(Value::as_str)
            != Some("checked-control-actions")
        {
            return Err(BackendError::Upstream(502));
        }
        let video_length = reply
            .value
            .get_path("video_length")
            .ok_or(BackendError::Upstream(502))
            .and_then(value_u64)?;
        let send2storage = reply
            .value
            .get_path("send2storage")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let playonspeaker = reply
            .value
            .get_path("playonspeaker")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let speaker_repeats = reply
            .value
            .get_path("speaker_repeats")
            .ok_or(BackendError::Upstream(502))
            .and_then(value_u64)
            .and_then(|value| u8::try_from(value).map_err(|_| BackendError::Upstream(502)))?;
        if !(1..=86_400).contains(&video_length) || !(1..=3).contains(&speaker_repeats) {
            return Err(BackendError::Upstream(502));
        }
        Ok(super::motion_actions::Config {
            video_length,
            send2storage,
            playonspeaker,
            speaker_repeats,
        })
    }

    fn motion_disk_actions(
        &self,
        deadline: Instant,
        allow_default: bool,
    ) -> Result<super::motion_actions::Config, BackendError> {
        let disk = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"motion"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        disk.require_only_fields(&["status", "section", "keys"])?;
        if disk.value.get_path("section").and_then(Value::as_str) != Some("motion")
            || disk
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        let video_length = match disk.value.get_path("keys.video_length") {
            None if allow_default => 10,
            Some(Value::String(value))
                if !value.is_empty() && value.bytes().all(|b| b.is_ascii_digit()) =>
            {
                value.parse().map_err(|_| BackendError::Upstream(502))?
            }
            _ => return Err(BackendError::Upstream(502)),
        };
        if !(1..=86_400).contains(&video_length) {
            return Err(BackendError::Upstream(502));
        }
        let send2storage = match disk.value.get_path("keys.send2storage") {
            None if allow_default => false,
            Some(Value::String(value)) => match value.to_ascii_lowercase().as_str() {
                "true" | "yes" | "on" | "1" => true,
                "false" | "no" | "off" | "0" => false,
                _ => return Err(BackendError::Upstream(502)),
            },
            _ => return Err(BackendError::Upstream(502)),
        };
        let playonspeaker = match disk.value.get_path("keys.playonspeaker") {
            None if allow_default => false,
            Some(Value::String(value)) => match value.to_ascii_lowercase().as_str() {
                "true" | "yes" | "on" | "1" => true,
                "false" | "no" | "off" | "0" => false,
                _ => return Err(BackendError::Upstream(502)),
            },
            _ => return Err(BackendError::Upstream(502)),
        };
        let speaker_repeats = match disk.value.get_path("keys.speaker_repeats") {
            None if allow_default => 1,
            Some(Value::String(value)) if matches!(value.as_str(), "1" | "2" | "3") => {
                value.parse().map_err(|_| BackendError::Upstream(502))?
            }
            _ => return Err(BackendError::Upstream(502)),
        };
        Ok(super::motion_actions::Config {
            video_length,
            send2storage,
            playonspeaker,
            speaker_repeats,
        })
    }

    pub(super) fn motion_event_play_speaker(
        &self,
        repeats: u8,
        deadline: Instant,
    ) -> Result<(), BackendError> {
        if !(1..=3).contains(&repeats) {
            return Err(BackendError::Protocol);
        }
        let command = format!(r#"{{"cmd":"ao-play-clip","clip":"motion","repeats":{repeats}}}"#);
        let reply = self.command(RaptorDaemon::Rad, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&[
            "status",
            "clip",
            "playback",
            "repeats",
            "sample_rate",
            "max_duration_ms",
            "talkback_active",
            "generation",
        ])?;
        if reply.value.get_path("clip").and_then(Value::as_str) != Some("motion")
            || reply.value.get_path("playback").and_then(Value::as_str) != Some("accepted")
            || reply
                .value
                .get_path("repeats")
                .and_then(|value| value_u64(value).ok())
                != Some(u64::from(repeats))
            || reply
                .value
                .get_path("sample_rate")
                .and_then(|value| value_u64(value).ok())
                != Some(16_000)
            || reply
                .value
                .get_path("max_duration_ms")
                .and_then(|value| value_u64(value).ok())
                != Some(10_000)
            || reply
                .value
                .get_path("talkback_active")
                .and_then(Value::as_bool)
                != Some(false)
            || reply
                .value
                .get_path("generation")
                .and_then(|value| value_u64(value).ok())
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(())
    }

    fn motion_speaker_status(&self, deadline: Instant) -> Result<String, BackendError> {
        let reply = self.command(RaptorDaemon::Rad, br#"{"cmd":"ao-clip-status"}"#, deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&[
            "status",
            "clip",
            "playback",
            "repeats",
            "sample_rate",
            "max_duration_ms",
            "talkback_active",
            "generation",
        ])?;
        let state = reply
            .value
            .get_path("playback")
            .and_then(Value::as_str)
            .filter(|state| {
                matches!(
                    *state,
                    "idle"
                        | "accepted"
                        | "running"
                        | "completed"
                        | "preempted"
                        | "cancelled"
                        | "error"
                )
            })
            .ok_or(BackendError::Upstream(502))?;
        if reply.value.get_path("clip").and_then(Value::as_str) != Some("motion")
            || reply
                .value
                .get_path("repeats")
                .and_then(|value| value_u64(value).ok())
                .is_none_or(|value| value > 3)
            || reply
                .value
                .get_path("sample_rate")
                .and_then(|value| value_u64(value).ok())
                != Some(16_000)
            || reply
                .value
                .get_path("max_duration_ms")
                .and_then(|value| value_u64(value).ok())
                != Some(10_000)
            || reply
                .value
                .get_path("talkback_active")
                .and_then(Value::as_bool)
                .is_none()
            || reply
                .value
                .get_path("generation")
                .and_then(|value| value_u64(value).ok())
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(state.to_owned())
    }

    fn motion_speaker_cancel(&self, deadline: Instant) -> Result<(), BackendError> {
        let reply = self.command(RaptorDaemon::Rad, br#"{"cmd":"ao-cancel-clip"}"#, deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&[
            "status",
            "clip",
            "playback",
            "repeats",
            "sample_rate",
            "max_duration_ms",
            "talkback_active",
            "generation",
        ])?;
        if reply.value.get_path("clip").and_then(Value::as_str) != Some("motion")
            || !matches!(
                reply.value.get_path("playback").and_then(Value::as_str),
                Some("idle" | "completed" | "preempted" | "cancelled" | "error")
            )
            || reply
                .value
                .get_path("sample_rate")
                .and_then(|value| value_u64(value).ok())
                != Some(16_000)
            || reply
                .value
                .get_path("max_duration_ms")
                .and_then(|value| value_u64(value).ok())
                != Some(10_000)
            || reply
                .value
                .get_path("talkback_active")
                .and_then(Value::as_bool)
                .is_none()
            || reply
                .value
                .get_path("generation")
                .and_then(|value| value_u64(value).ok())
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(())
    }

    pub fn start_motion_lifecycle(self: &Arc<Self>) -> Result<(), BackendError> {
        // Webhook delivery is an optional outbound destination. A missing
        // transport or malformed destination must fail it closed without
        // preventing detector, HA, speaker, or storage ownership from starting.
        self.start_motion_webhook();
        self.start_motion_ntfy();
        self.start_motion_email();
        self.start_motion_ftp();
        self.start_motion_gotify();
        self.start_motion_telegram();
        self.motion_actions.start(Arc::downgrade(self))?;
        let sink: Arc<dyn crate::camera::motion_events::MotionEventSink> =
            self.motion_actions.clone();
        self.motion_lifecycle.set_sink(sink);
        self.motion_lifecycle.start(Arc::downgrade(self))
    }

    pub(super) fn motion_status(&self, deadline: Instant) -> Result<RaptorReply, BackendError> {
        let upstream_error = |error| match error {
            BackendError::Protocol => BackendError::Upstream(502),
            error => error,
        };
        let reply = self
            .command(RaptorDaemon::Rvd, br#"{"cmd":"ivs-status"}"#, deadline)
            .map_err(upstream_error)?;
        require_ok(&reply).map_err(upstream_error)?;
        for field in ["active", "motion", "supported", "receiving"] {
            if reply
                .value
                .get_path(field)
                .and_then(Value::as_bool)
                .is_none()
            {
                return Err(BackendError::Upstream(502));
            }
        }
        Ok(reply)
    }

    // RVD may retain its last detection bit while ownership is stopping.
    // Availability carries unknown state; a stopped detector is never active.
    pub(super) fn observed_motion(reply: &RaptorReply) -> (bool, bool) {
        let flag = |name| reply.value.get_path(name).and_then(Value::as_bool) == Some(true);
        let monitoring = flag("active");
        let available = flag("supported") && monitoring == flag("receiving");
        (available, available && monitoring && flag("motion"))
    }

    pub(super) fn motion_runtime(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let lifecycle = self.motion_lifecycle.snapshot();
        if lifecycle.running {
            return Ok(BackendResponse::json(format!(
                "{{\"version\":1,\"source\":\"raptor\",\"supported\":{},\"monitoring\":{},\"active\":{},\"sample_active\":{},\"receiving\":{},\"available\":{},\"lifecycle_running\":true,\"polls\":{},\"poll_failures\":{},\"transitions\":{}}}",
                lifecycle.supported, lifecycle.monitoring, lifecycle.active,
                lifecycle.sample_active, lifecycle.receiving, lifecycle.available,
                lifecycle.polls, lifecycle.failures, lifecycle.transitions
            ).into_bytes()));
        }
        let reply = self.motion_status(deadline)?;
        let field = |name| reply.value.get_path(name).unwrap().to_json();
        let (raw_available, _) = Self::observed_motion(&reply);
        let active = Self::observed_motion(&reply).1;
        Ok(BackendResponse::json(format!(
            "{{\"version\":1,\"source\":\"raptor\",\"supported\":{},\"monitoring\":{},\"active\":{},\"sample_active\":{},\"receiving\":{},\"available\":{},\"lifecycle_running\":{},\"polls\":{},\"poll_failures\":{},\"transitions\":{}}}",
            field("supported"), field("active"), active, false,
            field("receiving"), raw_available, lifecycle.running, lifecycle.polls,
            lifecycle.failures, lifecycle.transitions
        ).into_bytes()))
    }

    pub(super) fn live_control(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let _mutation = self.lock_mutation()?;
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if let Some(command @ ("color" | "ircut" | "ir850")) =
            fields.get("cmd").and_then(Value::as_str)
        {
            if fields.len() != 2 {
                return Err(BackendError::Protocol);
            }
            let enabled = match fields.get("val") {
                Some(Value::Number(value)) if value == "1" => true,
                Some(Value::Number(value)) if value == "0" => false,
                _ => return Err(BackendError::Protocol),
            };
            self.timelapse_user_generation
                .fetch_add(1, Ordering::AcqRel);
            return if command == "color" {
                self.set_color(enabled, deadline)
            } else {
                self.set_ir_output(command, enabled, deadline)
            };
        }
        if let Some(recording) = fields.get("mp4") {
            if fields.len() != 1 {
                return Err(BackendError::Protocol);
            }
            let (channel, _) = super::recorder::recording_request(recording)?;
            self.recording_user_generation[channel as usize].fetch_add(1, Ordering::AcqRel);
            return self.set_recording(recording, deadline);
        }
        if let Some(audio) = fields.get("audio") {
            if fields.len() != 1 {
                return Err(BackendError::Protocol);
            }
            return self.set_live_audio(audio, deadline);
        }
        if let Some(privacy) = fields.get("privacy").and_then(Value::as_object) {
            if fields.len() != 1 || privacy.len() != 1 {
                return Err(BackendError::Protocol);
            }
            let enabled = privacy
                .get("enabled")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Protocol)?;
            return self.set_privacy(enabled, deadline);
        }
        let motion = fields
            .get("motion")
            .and_then(Value::as_object)
            .ok_or(BackendError::Protocol)?;
        if fields.len() != 1 || motion.len() != 1 {
            return Err(BackendError::Protocol);
        }
        let enabled = motion
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
        let status = self.motion_status(deadline)?;
        if status.value.get_path("supported").and_then(Value::as_bool) != Some(true) {
            return Err(BackendError::Unavailable);
        }
        let command = format!("{{\"cmd\":\"ivs-enable\",\"value\":{enabled}}}");
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply).map_err(|_| {
            BackendError::PartialApply(
                "Motion transition failed; receive ownership needs an explicit stop retry",
            )
        })?;
        if reply.value.get_path("active").and_then(Value::as_bool) != Some(enabled) {
            return Err(BackendError::Upstream(502));
        }
        let readback = self.motion_status(deadline)?;
        for field in ["active", "receiving"] {
            if readback.value.get_path(field).and_then(Value::as_bool) != Some(enabled) {
                return Err(BackendError::Upstream(502));
            }
        }
        Ok(BackendResponse::json(b"{\"status\":\"accepted\"}".to_vec()))
    }
}

impl RaptorBackend {
    pub(super) fn motion_config_observation(
        &self,
        deadline: Instant,
    ) -> Result<RaptorReply, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"get-motion-config"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-config") {
            return Err(BackendError::Unavailable);
        }
        for name in ["supported", "available", "active", "receiving", "worker"] {
            if reply
                .value
                .get_path(name)
                .and_then(Value::as_bool)
                .is_none()
            {
                return Err(BackendError::Upstream(502));
            }
        }
        let flag = |name| reply.value.get_path(name).and_then(Value::as_bool).unwrap();
        if flag("available")
            != (flag("supported")
                && flag("active") == flag("receiving")
                && flag("active") == flag("worker"))
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(reply)
    }

    pub(super) fn motion_lifecycle_config(
        reply: &RaptorReply,
    ) -> Result<crate::camera::motion_state::MotionConfig, BackendError> {
        let value = |name: &str, max: u64| {
            reply
                .value
                .get_path(&format!("lifecycle.{name}"))
                .ok_or(BackendError::Upstream(502))
                .and_then(value_u64)
                .and_then(|value| {
                    (value <= max)
                        .then_some(value)
                        .ok_or(BackendError::Upstream(502))
                })
        };
        Ok(crate::camera::motion_state::MotionConfig {
            debounce_samples: value("debounce_time", 65535)?,
            cooldown_ms: value("cooldown_time", 60)?.saturating_mul(1000),
            init_ms: value("init_time", 65535)?.saturating_mul(1000),
            min_active_ms: value("min_time", 65535)?.saturating_mul(1000),
            post_ms: value("post_time", 65535)?.saturating_mul(1000),
        })
    }

    fn motion_disk_lifecycle(
        &self,
        deadline: Instant,
        allow_default: bool,
    ) -> Result<[u64; 5], BackendError> {
        let disk = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"motion"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        let defaults = [0, 5, 5, 1, 0];
        let names = [
            "debounce_time",
            "cooldown_time",
            "init_time",
            "min_time",
            "post_time",
        ];
        let mut values = [0_u64; 5];
        for (index, name) in names.iter().enumerate() {
            let Some(value) = disk.value.get_path(&format!("keys.{name}")) else {
                if allow_default {
                    values[index] = defaults[index];
                    continue;
                }
                return Err(BackendError::Upstream(502));
            };
            let text = value.as_str().ok_or(BackendError::Upstream(502))?;
            if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err(BackendError::Upstream(502));
            }
            values[index] = text.parse().map_err(|_| BackendError::Upstream(502))?;
        }
        if values[1] > 60
            || values
                .iter()
                .enumerate()
                .any(|(i, value)| i != 1 && *value > 65535)
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(values)
    }

    fn motion_disk_enabled(
        &self,
        deadline: Instant,
        allow_default: bool,
    ) -> Result<bool, BackendError> {
        let disk = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"motion"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        disk.require_only_fields(&["status", "section", "keys"])?;
        if disk.value.get_path("section").and_then(Value::as_str) != Some("motion")
            || disk
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        match disk.value.get_path("keys.enabled") {
            None if allow_default => Ok(false), // Same default as RVD pipeline initialization.
            Some(Value::String(value)) => match value.to_ascii_lowercase().as_str() {
                "true" | "yes" | "on" | "1" => Ok(true),
                "false" | "no" | "off" | "0" => Ok(false),
                _ => Err(BackendError::Upstream(502)),
            },
            _ => Err(BackendError::Upstream(502)),
        }
    }

    fn motion_sensitivity(reply: &RaptorReply) -> Result<Option<u64>, BackendError> {
        let Some(field) = reply.value.get_path("sensitivity") else {
            return Ok(None);
        };
        let supported = field
            .get_path("supported")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let available = field
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if field.get_path("min").and_then(|v| value_u64(v).ok()) != Some(0)
            || field.get_path("max").and_then(|v| value_u64(v).ok()) != Some(4)
            || (available && !supported)
        {
            return Err(BackendError::Upstream(502));
        }
        if !available {
            return if field.get_path("value") == Some(&Value::Null) {
                Ok(None)
            } else {
                Err(BackendError::Upstream(502))
            };
        }
        for key in ["active", "receiving", "worker"] {
            if reply.value.get_path(key).and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Upstream(502));
            }
        }
        let value = field
            .get_path("value")
            .ok_or(BackendError::Upstream(502))
            .and_then(value_u64)?;
        if value > 4 {
            return Err(BackendError::Upstream(502));
        }
        Ok(Some(value))
    }

    fn motion_skip_frame_count(reply: &RaptorReply) -> Result<Option<u64>, BackendError> {
        let Some(field) = reply.value.get_path("skip_frames") else {
            return Ok(None);
        };
        let supported = field
            .get_path("supported")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let available = field
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if field.get_path("min").and_then(|v| value_u64(v).ok()) != Some(0)
            || field.get_path("max").and_then(|v| value_u64(v).ok()) != Some(65535)
            || (available && !supported)
        {
            return Err(BackendError::Upstream(502));
        }
        if !available {
            return if field.get_path("value") == Some(&Value::Null) {
                Ok(None)
            } else {
                Err(BackendError::Upstream(502))
            };
        }
        for key in ["active", "receiving", "worker"] {
            if reply.value.get_path(key).and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Upstream(502));
            }
        }
        let value = field
            .get_path("value")
            .ok_or(BackendError::Upstream(502))
            .and_then(value_u64)?;
        if value > 65535 {
            return Err(BackendError::Upstream(502));
        }
        Ok(Some(value))
    }

    fn motion_disk_sensitivity(
        &self,
        deadline: Instant,
        allow_default: bool,
    ) -> Result<u64, BackendError> {
        let disk = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"motion"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        disk.require_only_fields(&["status", "section", "keys"])?;
        if disk.value.get_path("section").and_then(Value::as_str) != Some("motion")
            || disk
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        let text = match disk.value.get_path("keys.sensitivity") {
            None if allow_default => "3",
            Some(Value::String(text)) => text,
            _ => return Err(BackendError::Upstream(502)),
        };
        match text {
            "0" => Ok(0),
            "1" => Ok(1),
            "2" => Ok(2),
            "3" => Ok(3),
            "4" => Ok(4),
            _ => Err(BackendError::Upstream(502)),
        }
    }

    fn motion_disk_skip_frame_count(
        &self,
        deadline: Instant,
        allow_default: bool,
    ) -> Result<u64, BackendError> {
        let disk = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"motion"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        disk.require_only_fields(&["status", "section", "keys"])?;
        if disk.value.get_path("section").and_then(Value::as_str) != Some("motion")
            || disk
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        let text = match disk.value.get_path("keys.skip_frames") {
            None if allow_default => "5",
            Some(Value::String(text)) => text,
            _ => return Err(BackendError::Upstream(502)),
        };
        if text.is_empty()
            || text.len() > 5
            || !text.bytes().all(|byte| byte.is_ascii_digit())
            || (text.len() > 1 && text.starts_with('0'))
        {
            return Err(BackendError::Upstream(502));
        }
        let value = text
            .parse::<u64>()
            .map_err(|_| BackendError::Upstream(502))?;
        (value <= 65535)
            .then_some(value)
            .ok_or(BackendError::Upstream(502))
    }

    pub(super) fn motion_config(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let state = self.motion_config_observation(deadline)?;
        let saved = self.motion_disk_enabled(deadline, true)?;
        let available = state.value.get_path("available").and_then(Value::as_bool) == Some(true);
        let enabled = state
            .value
            .get_path("active")
            .and_then(Value::as_bool)
            .unwrap();
        let supported = state
            .value
            .get_path("supported")
            .and_then(Value::as_bool)
            .unwrap();
        let sensitivity = Self::motion_sensitivity(&state)?;
        let saved_sensitivity = if state.value.get_path("sensitivity").is_some() {
            self.motion_disk_sensitivity(deadline, true).ok()
        } else {
            None
        };
        let roi = self.motion_roi_config_value(&state, deadline)?;
        let skip_frame_count = Self::motion_skip_frame_count(&state)?;
        let saved_skip_frame_count = if state.value.get_path("skip_frames").is_some() {
            self.motion_disk_skip_frame_count(deadline, true).ok()
        } else {
            None
        };
        let lifecycle = Self::motion_lifecycle_config(&state)?;
        let lifecycle_values = [
            lifecycle.debounce_samples,
            lifecycle.cooldown_ms / 1000,
            lifecycle.init_ms / 1000,
            lifecycle.min_active_ms / 1000,
            lifecycle.post_ms / 1000,
        ];
        let saved_lifecycle = self.motion_disk_lifecycle(deadline, true)?;
        let lifecycle_runtime = self.motion_lifecycle.snapshot();
        let actions = self.motion_action_config(deadline).ok();
        let saved_actions = actions.and_then(|_| self.motion_disk_actions(deadline, true).ok());
        let action_runtime = self.motion_actions.snapshot();
        let storage_available = self
            .recording_observation(1, deadline)
            .ok()
            .and_then(|reply| reply.value.get_path("available").and_then(Value::as_bool))
            .unwrap_or(false);
        let speaker_playback = self.motion_speaker_status(deadline).ok();
        let speaker_available = speaker_playback.is_some();
        Ok(BackendResponse::json(format!(
            r#"{{"source":"raptor","persistent":true,"supported":{supported},"available":{available},"enabled":{},"saved_enabled":{saved},"matches_saved":{},"sensitivity":{},"sensitivity_available":{},"saved_sensitivity":{},"sensitivity_matches_saved":{},"skip_frame_count":{},"skip_frame_count_available":{},"saved_skip_frame_count":{},"skip_frame_count_matches_saved":{},"debounce_time":{},"cooldown_time":{},"init_time":{},"min_time":{},"post_time":{},"lifecycle_available":{},"lifecycle_matches_saved":{},"video_length":{},"send2storage":{},"playonspeaker":{},"speaker_repeats":{},"event_actions_available":{},"saved_video_length":{},"saved_send2storage":{},"saved_playonspeaker":{},"saved_speaker_repeats":{},"event_actions_match_saved":{},"storage_action_available":{storage_available},"speaker_action_available":{speaker_available},"speaker_playback":{},"event_action_worker_running":{},"event_storage_owned_channel":{},"event_storage_starts":{},"event_storage_stops":{},"event_storage_failures":{},"event_action_queue_dropped":{},"event_speaker_requests":{},"event_speaker_failures":{},"event_speaker_rate_limited":{},"roi":{}}}"#,
            if available { enabled.to_string() } else { "null".into() }, available && enabled == saved,
            sensitivity.map_or("null".into(), |v| v.to_string()), sensitivity.is_some(),
            saved_sensitivity.map_or("null".into(), |v| v.to_string()), sensitivity.is_some() && sensitivity == saved_sensitivity,
            skip_frame_count.map_or("null".into(), |v| v.to_string()), skip_frame_count.is_some(),
            saved_skip_frame_count.map_or("null".into(), |v| v.to_string()), skip_frame_count.is_some() && skip_frame_count == saved_skip_frame_count,
            lifecycle_values[0], lifecycle_values[1], lifecycle_values[2], lifecycle_values[3], lifecycle_values[4],
            lifecycle_runtime.running && lifecycle_runtime.supported && lifecycle_runtime.available,
            lifecycle_values == saved_lifecycle,
            actions.map_or("null".into(), |value| value.video_length.to_string()),
            actions.map_or("null".into(), |value| value.send2storage.to_string()),
            actions.map_or("null".into(), |value| value.playonspeaker.to_string()),
            actions.map_or("null".into(), |value| value.speaker_repeats.to_string()),
            actions.is_some(),
            saved_actions.map_or("null".into(), |value| value.video_length.to_string()),
            saved_actions.map_or("null".into(), |value| value.send2storage.to_string()),
            saved_actions.map_or("null".into(), |value| value.playonspeaker.to_string()),
            saved_actions.map_or("null".into(), |value| value.speaker_repeats.to_string()),
            actions.is_some() && actions == saved_actions,
            speaker_playback.map_or("null".into(), |state| format!(r#""{state}""#)),
            action_runtime.running,
            action_runtime.owned_channel.map_or("null".into(), |value| value.to_string()),
            action_runtime.starts, action_runtime.stops, action_runtime.failures, action_runtime.queue_dropped,
            action_runtime.speaker_requests, action_runtime.speaker_failures,
            action_runtime.speaker_rate_limited,
            roi.to_json()).into_bytes()))
    }

    pub(super) fn update_motion_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let root = request
            .as_object()
            .filter(|root| root.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let fields = root
            .get("motion")
            .and_then(Value::as_object)
            .filter(|fields| {
                fields.keys().all(|key| {
                    key == "enabled"
                        || key == "sensitivity"
                        || key == "skip_frame_count"
                        || key == "video_length"
                        || key == "send2storage"
                        || key == "playonspeaker"
                        || key == "speaker_repeats"
                        || matches!(
                            key.as_str(),
                            "debounce_time"
                                | "cooldown_time"
                                | "init_time"
                                | "min_time"
                                | "post_time"
                        )
                        || key == "roi_count"
                        || ROI_KEYS.contains(&key.as_str())
                })
            })
            .ok_or(BackendError::Protocol)?;
        let enabled = fields
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
        let sensitivity = fields
            .get("sensitivity")
            .map(|value| {
                let value = value_u64(value).map_err(|_| BackendError::Protocol)?;
                if value > 4 || !enabled {
                    return Err(BackendError::Protocol);
                }
                Ok(value)
            })
            .transpose()?;
        let skip_frame_count = fields
            .get("skip_frame_count")
            .map(|value| {
                let value = value_u64(value).map_err(|_| BackendError::Protocol)?;
                if value > 65535 || !enabled {
                    return Err(BackendError::Protocol);
                }
                Ok(value)
            })
            .transpose()?;
        let actions_requested = fields.contains_key("video_length")
            || fields.contains_key("send2storage")
            || fields.contains_key("playonspeaker")
            || fields.contains_key("speaker_repeats");
        let actions = if actions_requested {
            let video_length = fields
                .get("video_length")
                .ok_or(BackendError::Protocol)
                .and_then(value_u64)
                .map_err(|_| BackendError::Protocol)?;
            let send2storage = fields
                .get("send2storage")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Protocol)?;
            let playonspeaker = fields
                .get("playonspeaker")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Protocol)?;
            let speaker_repeats = fields
                .get("speaker_repeats")
                .ok_or(BackendError::Protocol)
                .and_then(value_u64)
                .and_then(|value| u8::try_from(value).map_err(|_| BackendError::Protocol))?;
            if !(1..=86_400).contains(&video_length) || !(1..=3).contains(&speaker_repeats) {
                return Err(BackendError::Protocol);
            }
            Some(super::motion_actions::Config {
                video_length,
                send2storage,
                playonspeaker,
                speaker_repeats,
            })
        } else {
            None
        };
        let lifecycle_requested = [
            "debounce_time",
            "cooldown_time",
            "init_time",
            "min_time",
            "post_time",
        ]
        .iter()
        .any(|name| fields.contains_key(*name));
        let lifecycle = if lifecycle_requested {
            let read = |name: &str, max: u64| {
                fields
                    .get(name)
                    .and_then(|value| value_u64(value).ok())
                    .filter(|value| *value <= max)
                    .ok_or(BackendError::Protocol)
            };
            Some([
                read("debounce_time", 65535)?,
                read("cooldown_time", 60)?,
                read("init_time", 65535)?,
                read("min_time", 65535)?,
                read("post_time", 65535)?,
            ])
        } else {
            None
        };
        let roi_requested = fields.contains_key("roi_count")
            || ROI_KEYS.iter().any(|key| fields.contains_key(*key));
        let roi = if roi_requested {
            if !enabled
                || fields
                    .get("roi_count")
                    .and_then(|value| value_u64(value).ok())
                    != Some(1)
            {
                return Err(BackendError::Protocol);
            }
            Some(SingleRoi::from_value(request.get_path("motion").unwrap())?)
        } else {
            None
        };
        let _mutation = self.lock_mutation()?;
        let before = self.motion_config_observation(deadline)?;
        let roi_frame = if let Some(roi) = roi {
            let observed = Self::motion_roi_observation(&before)?
                .filter(|state| state.supported && state.available)
                .ok_or(BackendError::Unavailable)?;
            let frame = observed.frame.ok_or(BackendError::Unavailable)?;
            roi.validate(frame.0, frame.1)?;
            Some(frame)
        } else {
            None
        };
        if sensitivity.is_some() && Self::motion_sensitivity(&before)?.is_none() {
            return Err(BackendError::Unavailable);
        }
        if skip_frame_count.is_some() && Self::motion_skip_frame_count(&before)?.is_none() {
            return Err(BackendError::Unavailable);
        }
        if before.value.get_path("supported").and_then(Value::as_bool) != Some(true) {
            return Err(BackendError::Unavailable);
        }
        let previous_actions = if actions.is_some() {
            Some(
                self.motion_action_config(deadline)
                    .map_err(|_| BackendError::Unavailable)?,
            )
        } else {
            None
        };
        let apply = || -> Result<(), BackendError> {
            let command = format!(r#"{{"cmd":"set-motion-config","enabled":{enabled}}}"#);
            if roi.is_none() {
                require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
            }
            if let Some(value) = sensitivity {
                let command =
                    format!(r#"{{"cmd":"set-motion-sensitivity","sensitivity":{value}}}"#);
                require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
            }
            if let Some(value) = skip_frame_count {
                let command =
                    format!(r#"{{"cmd":"set-motion-skip-frames","skip_frame_count":{value}}}"#);
                require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
            }
            if let Some(values) = lifecycle {
                let command = format!(
                    r#"{{"cmd":"set-motion-lifecycle","debounce_time":{},"cooldown_time":{},"init_time":{},"min_time":{},"post_time":{}}}"#,
                    values[0], values[1], values[2], values[3], values[4]
                );
                let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
                require_ok(&reply)?;
                let observed = self.motion_config_observation(deadline)?;
                let config = Self::motion_lifecycle_config(&observed)?;
                let observed_values = [
                    config.debounce_samples,
                    config.cooldown_ms / 1000,
                    config.init_ms / 1000,
                    config.min_active_ms / 1000,
                    config.post_ms / 1000,
                ];
                if observed_values != values {
                    return Err(BackendError::Upstream(502));
                }
                self.motion_lifecycle.set_config(config);
            }
            if let Some(actions) = actions {
                let command = format!(
                    r#"{{"cmd":"set-motion-actions","video_length":{},"send2storage":{},"playonspeaker":{},"speaker_repeats":{}}}"#,
                    actions.video_length,
                    actions.send2storage,
                    actions.playonspeaker,
                    actions.speaker_repeats
                );
                require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
                if self.motion_action_config(deadline)? != actions {
                    return Err(BackendError::Upstream(502));
                }
                if previous_actions.is_some_and(|previous| previous.playonspeaker)
                    && !actions.playonspeaker
                {
                    self.motion_speaker_cancel(deadline)?;
                }
            }
            if let Some(roi) = roi {
                self.apply_motion_roi(roi, roi_frame.unwrap(), deadline)?;
                // The ROI setter retains the SDK channel but may leave receive paused.
                require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
            }
            let state = self.motion_config_observation(deadline)?;
            if let Some(roi) = roi
                && Self::motion_roi_observation(&state)?
                    .is_none_or(|state| !state.matches(roi, roi_frame.unwrap()))
            {
                return Err(BackendError::Upstream(502));
            }
            if sensitivity.is_some() && Self::motion_sensitivity(&state)? != sensitivity {
                return Err(BackendError::Upstream(502));
            }
            if skip_frame_count.is_some()
                && Self::motion_skip_frame_count(&state)? != skip_frame_count
            {
                return Err(BackendError::Upstream(502));
            }
            if state.value.get_path("available").and_then(Value::as_bool) != Some(true)
                || state.value.get_path("active").and_then(Value::as_bool) != Some(enabled)
            {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            if self.motion_disk_enabled(deadline, false)? != enabled {
                return Err(BackendError::Upstream(502));
            }
            if let Some(value) = sensitivity
                && self.motion_disk_sensitivity(deadline, false)? != value
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(value) = skip_frame_count
                && self.motion_disk_skip_frame_count(deadline, false)? != value
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(values) = lifecycle
                && self.motion_disk_lifecycle(deadline, false)? != values
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(actions) = actions
                && self.motion_disk_actions(deadline, false)? != actions
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(actions) = actions
                && self.motion_action_config(deadline)? != actions
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(roi) = roi {
                if self.motion_disk_roi(deadline)? != roi {
                    return Err(BackendError::Upstream(502));
                }
                let last = self.motion_config_observation(deadline)?;
                if last.value.get_path("active").and_then(Value::as_bool) != Some(true)
                    || Self::motion_roi_observation(&last)?
                        .is_none_or(|state| !state.matches(roi, roi_frame.unwrap()))
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Motion may have changed, but apply, save or readback was incomplete. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

#[cfg(test)]
pub(super) mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::io::Write;
    use std::os::unix::net::UnixListener;
    use std::{fs, thread};

    fn observation(enabled: bool) -> Vec<u8> {
        format!(r#"{{"status":"ok","persistence":"checked-config","supported":true,"available":true,"active":{enabled},"receiving":{enabled},"worker":{enabled},"lifecycle":{{"debounce_time":0,"cooldown_time":5,"init_time":5,"min_time":1,"post_time":0}}}}"#).into_bytes()
    }

    pub(in crate::raptor_backend) fn sequence(
        root: &std::path::Path,
        pairs: Vec<(Vec<u8>, Vec<u8>)>,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for (request, response) in pairs {
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing motion request: {other:?}"),
                    }
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        })
    }

    fn rad_sequence(
        root: &std::path::Path,
        request: &'static [u8],
        response: &'static [u8],
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join("rad.sock")).unwrap();
        thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            assert_eq!(read_request(&mut socket), request);
            socket.write_all(&framed(response)).unwrap();
        })
    }

    fn runtime_backend(root: &std::path::Path) -> RaptorBackend {
        fs::write(root.join("uptime"), "123.0 0.0\n").unwrap();
        let mut backend = backend(root, "127.0.0.1:9".parse().unwrap());
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            uptime: root.join("uptime"),
            ..crate::camera::CameraPaths::default()
        });
        backend
    }

    #[test]
    fn motion_runtime_and_heartbeat_share_detection_availability() {
        for (supported, monitoring, receiving, detection) in [
            (true, true, true, true),
            (true, true, true, false),
            (true, false, false, true),
            (true, false, true, true),
            (true, true, false, true),
            (false, true, true, true),
        ] {
            let root = task_temp("motion-event");
            let state = format!(r#"{{"status":"ok","supported":{supported},"active":{monitoring},"receiving":{receiving},"motion":{detection}}}"#).into_bytes();
            let expected_available = supported && monitoring == receiving;
            let expected_active = expected_available && monitoring && detection;
            let backend = runtime_backend(&root);
            for heartbeat in [false, true] {
                let daemon = sequence(
                    &root,
                    vec![(br#"{"cmd":"ivs-status"}"#.to_vec(), state.clone())],
                );
                let response = if heartbeat {
                    backend.heartbeat(Instant::now() + Duration::from_secs(1))
                } else {
                    backend.motion_runtime(Instant::now() + Duration::from_secs(1))
                }
                .unwrap();
                let value = crate::json::parse(&response.body).unwrap();
                if heartbeat {
                    assert_eq!(
                        value.get_path("motion_active"),
                        Some(&if expected_available {
                            Value::Bool(expected_active)
                        } else {
                            Value::Null
                        })
                    );
                    assert_eq!(
                        value.get_path("motion_enabled"),
                        Some(&if expected_available {
                            Value::Bool(monitoring)
                        } else {
                            Value::Null
                        })
                    );
                } else {
                    assert_eq!(
                        value.get_path("available"),
                        Some(&Value::Bool(expected_available))
                    );
                    assert_eq!(
                        value.get_path("active"),
                        Some(&Value::Bool(expected_active))
                    );
                }
                daemon.join().unwrap();
                fs::remove_file(root.join("rvd.sock")).unwrap();
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn malformed_motion_reply_is_an_upstream_error_not_a_bad_client_request() {
        let root = task_temp("motion-protocol");
        let daemon = sequence(
            &root,
            vec![(br#"{"cmd":"ivs-status"}"#.to_vec(), b"{}".to_vec())],
        );
        assert!(matches!(
            runtime_backend(&root).motion_runtime(Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Upstream(502))
        ));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn motion_observation_is_not_cached_across_disconnect_or_malformed_restart() {
        let root = task_temp("motion-restart");
        let backend = runtime_backend(&root);
        for state in [
            Some(br#"{"status":"ok","supported":true,"active":true,"receiving":true,"motion":true}"#.as_slice()),
            None,
            Some(br#"{"status":"ok","supported":true,"active":true,"receiving":true,"motion":null}"#.as_slice()),
            Some(br#"{"status":"ok","supported":true,"active":true,"receiving":true,"motion":false}"#.as_slice()),
        ] {
            let daemon = state.map(|state| sequence(&root, vec![(br#"{"cmd":"ivs-status"}"#.to_vec(), state.to_vec())]));
            let reply = backend.heartbeat(Instant::now() + Duration::from_secs(1)).unwrap();
            let value = crate::json::parse(&reply.body).unwrap();
            let expected = match state {
                Some(bytes) if bytes.ends_with(b"true}") => Value::Bool(true),
                Some(bytes) if bytes.ends_with(b"false}") => Value::Bool(false),
                _ => Value::Null,
            };
            assert_eq!(value.get_path("motion_active"), Some(&expected));
            if let Some(daemon) = daemon {
                daemon.join().unwrap();
                fs::remove_file(root.join("rvd.sock")).unwrap();
            }
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn persistent_motion_checks_each_stage_including_false_disk_key() {
        for enabled in [true, false] {
            for fault in ["none", "setter", "live", "save", "disk", "missing"] {
                let root = task_temp("motion-persist");
                let mut pairs = vec![(
                    br#"{"cmd":"get-motion-config"}"#.to_vec(),
                    observation(enabled),
                )];
                pairs.push((
                    format!(r#"{{"cmd":"set-motion-config","enabled":{enabled}}}"#).into_bytes(),
                    if fault == "setter" {
                        br#"{"status":"error"}"#.to_vec()
                    } else {
                        br#"{"status":"ok"}"#.to_vec()
                    },
                ));
                if fault != "setter" {
                    pairs.push((
                        br#"{"cmd":"get-motion-config"}"#.to_vec(),
                        observation(if fault == "live" { !enabled } else { enabled }),
                    ));
                    if fault != "live" {
                        pairs.push((
                            br#"{"cmd":"config-save"}"#.to_vec(),
                            if fault == "save" {
                                br#"{"status":"error"}"#.to_vec()
                            } else {
                                br#"{"status":"ok"}"#.to_vec()
                            },
                        ));
                        if fault != "save" {
                            pairs.push((
                                br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                                format!(
                                    r#"{{"status":"ok","section":"motion","keys":{{{}}}}}"#,
                                    if fault == "missing" {
                                        String::new()
                                    } else {
                                        format!(
                                            r#""enabled":"{}""#,
                                            if fault == "disk" { !enabled } else { enabled }
                                        )
                                    }
                                )
                                .into_bytes(),
                            ));
                        }
                    }
                }
                let daemon = sequence(&root, pairs);
                let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(
                    format!(r#"{{"motion":{{"enabled":{enabled}}}}}"#).as_bytes(),
                    Instant::now() + Duration::from_secs(2),
                );
                if fault == "none" {
                    assert!(result.is_ok());
                } else {
                    assert!(
                        matches!(result, Err(BackendError::PartialApply(_))),
                        "{fault}"
                    );
                }
                daemon.join().unwrap();
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn motion_config_separates_saved_choice_from_runtime_state() {
        let root = task_temp("motion-observe");
        let daemon = sequence(
            &root,
            vec![
                (
                    br#"{"cmd":"get-motion-config"}"#.to_vec(),
                    observation(false),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                    br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                    br#"{"status":"ok","section":"motion","keys":{}}"#.to_vec(),
                ),
            ],
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap())
            .motion_config(Instant::now() + Duration::from_secs(2))
            .unwrap();
        let value = crate::json::parse(&result.body).unwrap();
        assert_eq!(value.get_path("enabled"), Some(&Value::Bool(false)));
        assert_eq!(value.get_path("saved_enabled"), Some(&Value::Bool(true)));
        assert_eq!(value.get_path("matches_saved"), Some(&Value::Bool(false)));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn motion_config_exposes_checked_skip_count_and_saved_comparison() {
        let root = task_temp("motion-skip-observe");
        let mut state = String::from_utf8(observation(true)).unwrap();
        state.pop();
        state.push_str(
            r#","skip_frames":{"supported":true,"available":true,"min":0,"max":65535,"value":12}}"#,
        );
        let daemon = sequence(
            &root,
            vec![
                (
                    br#"{"cmd":"get-motion-config"}"#.to_vec(),
                    state.into_bytes(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                    br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                    br#"{"status":"ok","section":"motion","keys":{"skip_frames":"12"}}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                    br#"{"status":"ok","section":"motion","keys":{}}"#.to_vec(),
                ),
            ],
        );
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .motion_config(Instant::now() + Duration::from_secs(2))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("skip_frame_count"),
            Some(&Value::Number("12".into()))
        );
        assert_eq!(
            value.get_path("skip_frame_count_available"),
            Some(&Value::Bool(true))
        );
        assert_eq!(
            value.get_path("saved_skip_frame_count"),
            Some(&Value::Number("12".into()))
        );
        assert_eq!(
            value.get_path("skip_frame_count_matches_saved"),
            Some(&Value::Bool(true))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn motion_event_actions_require_live_save_disk_and_final_live_readback() {
        let root = task_temp("motion-actions-persist");
        let action = br#"{"status":"ok","persistence":"checked-control-actions","video_length":15,"send2storage":true,"playonspeaker":true,"speaker_repeats":2}"#.to_vec();
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), observation(true)),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), action.clone()),
                (br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"set-motion-actions","video_length":15,"send2storage":true,"playonspeaker":true,"speaker_repeats":2}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), action.clone()),
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), observation(true)),
                (br#"{"cmd":"config-save"}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(), br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(), br#"{"status":"ok","section":"motion","keys":{"video_length":"15","send2storage":"true","playonspeaker":"true","speaker_repeats":"2"}}"#.to_vec()),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), action),
            ],
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(
            br#"{"motion":{"enabled":true,"video_length":15,"send2storage":true,"playonspeaker":true,"speaker_repeats":2}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn disabling_motion_speaker_cancels_the_native_clip_before_save() {
        let root = task_temp("motion-speaker-disable");
        let prior = br#"{"status":"ok","persistence":"checked-control-actions","video_length":15,"send2storage":false,"playonspeaker":true,"speaker_repeats":2}"#.to_vec();
        let disabled = br#"{"status":"ok","persistence":"checked-control-actions","video_length":15,"send2storage":false,"playonspeaker":false,"speaker_repeats":2}"#.to_vec();
        let video = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), observation(true)),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), prior),
                (br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"set-motion-actions","video_length":15,"send2storage":false,"playonspeaker":false,"speaker_repeats":2}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), disabled.clone()),
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), observation(true)),
                (br#"{"cmd":"config-save"}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(), br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(), br#"{"status":"ok","section":"motion","keys":{"video_length":"15","send2storage":"false","playonspeaker":"false","speaker_repeats":"2"}}"#.to_vec()),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), disabled),
            ],
        );
        let audio = rad_sequence(
            &root,
            br#"{"cmd":"ao-cancel-clip"}"#,
            br#"{"status":"ok","clip":"motion","playback":"cancelled","repeats":2,"sample_rate":16000,"max_duration_ms":10000,"talkback_active":false,"generation":7}"#,
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(
            br#"{"motion":{"enabled":true,"video_length":15,"send2storage":false,"playonspeaker":false,"speaker_repeats":2}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        video.join().unwrap();
        audio.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn invalid_motion_event_action_pair_is_rejected_before_ipc() {
        for body in [
            br#"{"motion":{"enabled":true,"video_length":0,"send2storage":true,"playonspeaker":true,"speaker_repeats":1}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"video_length":86401,"send2storage":true,"playonspeaker":true,"speaker_repeats":1}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"video_length":10,"send2storage":true,"playonspeaker":true,"speaker_repeats":0}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"video_length":10,"send2storage":true,"playonspeaker":true,"speaker_repeats":4}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"video_length":10,"send2storage":true,"playonspeaker":1,"speaker_repeats":1}}"#.as_slice(),
        ] {
            let root = task_temp("motion-actions-invalid");
            assert!(matches!(
                backend(&root, "127.0.0.1:9".parse().unwrap())
                    .update_motion_config(body, Instant::now() + Duration::from_millis(100)),
                Err(BackendError::Protocol)
            ));
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn sensitivity_requires_live_and_disk_readback() {
        fn state(value: u64) -> Vec<u8> {
            let mut text = String::from_utf8(observation(true)).unwrap();
            text.pop();
            format!(r#"{text},"sensitivity":{{"supported":true,"available":true,"min":0,"max":4,"value":{value}}}}}"#).into_bytes()
        }
        for fault in ["none", "setter", "readback", "save", "disk", "missing"] {
            let root = task_temp("motion-sensitivity");
            let mut pairs = vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), state(3)),
                (
                    br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"set-motion-sensitivity","sensitivity":4}"#.to_vec(),
                    if fault == "setter" {
                        br#"{"status":"error"}"#.to_vec()
                    } else {
                        br#"{"status":"ok"}"#.to_vec()
                    },
                ),
            ];
            if fault != "setter" {
                pairs.push((
                    br#"{"cmd":"get-motion-config"}"#.to_vec(),
                    state(if fault == "readback" { 3 } else { 4 }),
                ));
                if fault != "readback" {
                    pairs.push((
                        br#"{"cmd":"config-save"}"#.to_vec(),
                        if fault == "save" {
                            br#"{"status":"error"}"#.to_vec()
                        } else {
                            br#"{"status":"ok"}"#.to_vec()
                        },
                    ));
                    if fault != "save" {
                        pairs.push((
                            br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                            br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#
                                .to_vec(),
                        ));
                        pairs.push((
                            br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                            format!(
                                r#"{{"status":"ok","section":"motion","keys":{{{}}}}}"#,
                                if fault == "missing" {
                                    ""
                                } else if fault == "disk" {
                                    r#""sensitivity":"3""#
                                } else {
                                    r#""sensitivity":"4""#
                                }
                            )
                            .into_bytes(),
                        ));
                    }
                }
            }
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(
                br#"{"motion":{"enabled":true,"sensitivity":4}}"#,
                Instant::now() + Duration::from_secs(2),
            );
            if fault == "none" {
                assert!(result.is_ok(), "{result:?}");
            } else {
                assert!(
                    matches!(result, Err(BackendError::PartialApply(_))),
                    "{fault}: {result:?}"
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn skip_frame_count_requires_live_and_disk_readback() {
        fn state(value: u64) -> Vec<u8> {
            let mut text = String::from_utf8(observation(true)).unwrap();
            text.pop();
            format!(r#"{text},"skip_frames":{{"supported":true,"available":true,"min":0,"max":65535,"value":{value}}}}}"#).into_bytes()
        }
        for fault in ["none", "setter", "readback", "save", "disk", "missing"] {
            let root = task_temp("motion-skip-frames");
            let mut pairs = vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), state(5)),
                (
                    br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"set-motion-skip-frames","skip_frame_count":12}"#.to_vec(),
                    if fault == "setter" {
                        br#"{"status":"error"}"#.to_vec()
                    } else {
                        br#"{"status":"ok"}"#.to_vec()
                    },
                ),
            ];
            if fault != "setter" {
                pairs.push((
                    br#"{"cmd":"get-motion-config"}"#.to_vec(),
                    state(if fault == "readback" { 5 } else { 12 }),
                ));
                if fault != "readback" {
                    pairs.push((
                        br#"{"cmd":"config-save"}"#.to_vec(),
                        if fault == "save" {
                            br#"{"status":"error"}"#.to_vec()
                        } else {
                            br#"{"status":"ok"}"#.to_vec()
                        },
                    ));
                    if fault != "save" {
                        pairs.push((
                            br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                            br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#
                                .to_vec(),
                        ));
                        pairs.push((
                            br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(),
                            format!(
                                r#"{{"status":"ok","section":"motion","keys":{{{}}}}}"#,
                                if fault == "missing" {
                                    ""
                                } else if fault == "disk" {
                                    r#""skip_frames":"5""#
                                } else {
                                    r#""skip_frames":"12""#
                                }
                            )
                            .into_bytes(),
                        ));
                    }
                }
            }
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(
                br#"{"motion":{"enabled":true,"skip_frame_count":12}}"#,
                Instant::now() + Duration::from_secs(2),
            );
            if fault == "none" {
                assert!(result.is_ok(), "{result:?}");
            } else {
                assert!(
                    matches!(result, Err(BackendError::PartialApply(_))),
                    "{fault}: {result:?}"
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn invalid_skip_frame_count_is_rejected_before_ipc() {
        let root = task_temp("motion-skip-invalid");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"motion":{"enabled":true,"skip_frame_count":"12"}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"skip_frame_count":true}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"skip_frame_count":1.5}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"skip_frame_count":65536}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"skip_frame_count":-1}}"#.as_slice(),
        ] {
            assert!(matches!(
                backend.update_motion_config(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        assert!(!root.join("rvd.sock").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn lifecycle_settings_require_live_cache_and_disk_readback() {
        fn state(values: [u64; 5]) -> Vec<u8> {
            format!(r#"{{"status":"ok","persistence":"checked-config","supported":true,"available":true,"active":true,"receiving":true,"worker":true,"lifecycle":{{"debounce_time":{},"cooldown_time":{},"init_time":{},"min_time":{},"post_time":{}}}}}"#,
                values[0], values[1], values[2], values[3], values[4]).into_bytes()
        }
        let root = task_temp("motion-lifecycle-config");
        let requested = [2, 7, 3, 4, 6];
        let daemon = sequence(&root, vec![
            (br#"{"cmd":"get-motion-config"}"#.to_vec(), state([0, 5, 5, 1, 0])),
            (br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (br#"{"cmd":"set-motion-lifecycle","debounce_time":2,"cooldown_time":7,"init_time":3,"min_time":4,"post_time":6}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (br#"{"cmd":"get-motion-config"}"#.to_vec(), state(requested)),
            (br#"{"cmd":"get-motion-config"}"#.to_vec(), state(requested)),
            (br#"{"cmd":"config-save"}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(), br#"{"status":"ok","section":"motion","keys":{"enabled":"true"}}"#.to_vec()),
            (br#"{"cmd":"config-read-section","section":"motion"}"#.to_vec(), br#"{"status":"ok","section":"motion","keys":{"debounce_time":"2","cooldown_time":"7","init_time":"3","min_time":"4","post_time":"6"}}"#.to_vec()),
        ]);
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(
            br#"{"motion":{"enabled":true,"debounce_time":2,"cooldown_time":7,"init_time":3,"min_time":4,"post_time":6}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn incomplete_or_out_of_range_lifecycle_is_rejected_before_ipc() {
        let root = task_temp("motion-lifecycle-invalid");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"motion":{"enabled":true,"debounce_time":2}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"debounce_time":2,"cooldown_time":61,"init_time":3,"min_time":4,"post_time":6}}"#.as_slice(),
            br#"{"motion":{"enabled":true,"debounce_time":2.5,"cooldown_time":7,"init_time":3,"min_time":4,"post_time":6}}"#.as_slice(),
        ] {
            assert!(matches!(backend.update_motion_config(body, Instant::now() + Duration::from_secs(1)), Err(BackendError::Protocol)));
        }
        assert!(!root.join("rvd.sock").exists());
        fs::remove_dir_all(root).unwrap();
    }
}
