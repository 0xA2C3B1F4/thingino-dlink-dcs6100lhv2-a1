use super::*;

fn upstream(error: BackendError) -> BackendError {
    match error {
        BackendError::Protocol => BackendError::Upstream(502),
        error => error,
    }
}

#[derive(Clone, Copy)]
struct GopObservation {
    supported: bool,
    value: Option<u64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct GopModeObservation {
    supported: bool,
    active_mode: Option<&'static str>,
}

#[derive(Clone)]
struct EncodingObservation {
    supported: bool,
    mode: Option<&'static str>,
    bitrate: Option<u64>,
    min: u64,
    max: u64,
    step: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct RcConfigObservation {
    supported: bool,
    active_mode: Option<&'static str>,
    active_qp: Option<u64>,
    min: u64,
    max: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct SavedRcConfig {
    mode: &'static str,
    qp: Option<u64>,
}

#[derive(Clone)]
struct CodecObservation {
    supported: bool,
    codec: Option<&'static str>,
    codecs: Vec<&'static str>,
    recovery_required: bool,
}

#[derive(Clone, Copy)]
struct ProfileObservation {
    supported: bool,
    profile: Option<u64>,
    recovery_required: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct GeometryObservation {
    width: u64,
    height: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct BufferObservation {
    supported: bool,
    active: Option<u64>,
    configured: Option<u64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct StreamAudioOwner {
    active: bool,
    startup_explicit: bool,
    source_available: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct StreamAudioObservation {
    configured: Option<bool>,
    rsd: StreamAudioOwner,
    rmr: Option<StreamAudioOwner>,
}

impl StreamAudioObservation {
    fn active_enabled(self) -> Option<bool> {
        if self.selection_required() || self.legacy_selection_pending() {
            return None;
        }
        match self.rmr {
            Some(rmr) if rmr.active != self.rsd.active => None,
            _ => Some(self.rsd.active),
        }
    }

    fn legacy_owners(self) -> bool {
        !self.rsd.startup_explicit
            && self.rsd.active
            && self
                .rmr
                .is_none_or(|rmr| !rmr.startup_explicit && !rmr.active)
    }

    fn selection_required(self) -> bool {
        self.configured.is_none() && self.legacy_owners()
    }

    fn legacy_selection_pending(self) -> bool {
        self.configured.is_some() && self.legacy_owners()
    }

    fn policy_signature(self) -> (bool, bool, Option<(bool, bool)>) {
        (
            self.rsd.active,
            self.rsd.startup_explicit,
            self.rmr.map(|rmr| (rmr.active, rmr.startup_explicit)),
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct EnableObservation {
    editable: bool,
    required: bool,
    pub(super) active: bool,
    configured: bool,
    motion_blocks_disable: bool,
    recorder_blocks_disable: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct MotionRoi {
    x0: u64,
    y0: u64,
    x1: u64,
    y1: u64,
}

fn enable_pending_restart(saved: Option<bool>, active: bool) -> Option<bool> {
    saved.map(|value| value != active)
}

fn encoding_mode(value: &str) -> Option<(&'static str, &'static str)> {
    match value {
        "CBR" | "cbr" => Some(("CBR", "cbr")),
        "VBR" | "vbr" => Some(("VBR", "vbr")),
        "CAPPED_VBR" | "capped_vbr" => Some(("CAPPED_VBR", "capped_vbr")),
        "CAPPED_QUALITY" | "capped_quality" => Some(("CAPPED_QUALITY", "capped_quality")),
        _ => None,
    }
}

fn rc_config_mode(value: &str) -> Option<(&'static str, &'static str)> {
    match value {
        "FIXQP" | "fixqp" => Some(("FIXQP", "fixqp")),
        _ => encoding_mode(value),
    }
}

fn gop_mode(value: &str) -> Option<(&'static str, &'static str)> {
    match value {
        "DEFAULT" | "default" => Some(("DEFAULT", "default")),
        "PYRAMIDAL" | "pyramidal" => Some(("PYRAMIDAL", "pyramidal")),
        "SMARTP" | "smartp" => Some(("SMARTP", "smartp")),
        _ => None,
    }
}

impl RaptorBackend {
    fn stream_audio_owner(
        &self,
        daemon: RaptorDaemon,
        owner: &str,
        id: u64,
        deadline: Instant,
    ) -> Result<StreamAudioOwner, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-audio-policy","stream_id":{id}}}"#);
        let reply = self.command(daemon, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply
            .require_only_fields(&[
                "status",
                "owner",
                "apply",
                "stream_id",
                "supported",
                "startup_explicit",
                "active_enabled",
                "source_available",
                "track_active",
            ])
            .or_else(|_| {
                reply.require_only_fields(&[
                    "status",
                    "owner",
                    "apply",
                    "stream_id",
                    "supported",
                    "startup_explicit",
                    "active_enabled",
                    "source_available",
                ])
            })?;
        if reply.value.get_path("owner").and_then(Value::as_str) != Some(owner)
            || reply.value.get_path("apply").and_then(Value::as_str) != Some("full-camera-restart")
            || reply
                .value
                .get_path("stream_id")
                .and_then(|v| value_u64(v).ok())
                != Some(id)
            || reply.value.get_path("supported").and_then(Value::as_bool) != Some(true)
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(StreamAudioOwner {
            active: reply
                .value
                .get_path("active_enabled")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?,
            startup_explicit: reply
                .value
                .get_path("startup_explicit")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?,
            source_available: reply
                .value
                .get_path("source_available")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?,
        })
    }

    fn stream_audio_configured(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<Option<bool>, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-audio-config","stream_id":{id}}}"#);
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&[
            "status",
            "persistence",
            "stream_id",
            "configured_explicit",
            "configured_enabled",
        ])?;
        if reply.value.get_path("persistence").and_then(Value::as_str)
            != Some("checked-next-full-stack-restart")
            || reply
                .value
                .get_path("stream_id")
                .and_then(|v| value_u64(v).ok())
                != Some(id)
        {
            return Err(BackendError::Upstream(502));
        }
        let explicit = reply
            .value
            .get_path("configured_explicit")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let value = reply.value.get_path("configured_enabled");
        match (
            explicit,
            value.and_then(Value::as_bool),
            value == Some(&Value::Null),
        ) {
            (true, Some(value), false) => Ok(Some(value)),
            (false, None, true) => Ok(None),
            _ => Err(BackendError::Upstream(502)),
        }
    }

    fn stream_audio_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<StreamAudioObservation, BackendError> {
        let configured = self.stream_audio_configured(id, deadline)?;
        let rsd = self.stream_audio_owner(RaptorDaemon::Rsd, "rsd", id, deadline)?;
        let stream = self.stream_enable_observation(id, deadline)?;
        let rmr = if id == 0 || stream.active {
            Some(self.stream_audio_owner(
                if id == 0 {
                    RaptorDaemon::Rmr0
                } else {
                    RaptorDaemon::Rmr1
                },
                if id == 0 { "rmr0" } else { "rmr1" },
                id,
                deadline,
            )?)
        } else {
            None
        };
        Ok(StreamAudioObservation {
            configured,
            rsd,
            rmr,
        })
    }

    fn update_audio_inclusion(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, value) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = value
                .as_object()
                .filter(|f| f.len() == 1)
                .ok_or(BackendError::Protocol)?;
            let enabled = fields
                .get("audio_enabled")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Protocol)?;
            updates.push((id, enabled));
        }
        let mut before = Vec::new();
        for (id, enabled) in &updates {
            let observed = self.stream_audio_observation(*id, deadline)?;
            if observed.active_enabled().is_none()
                && !observed.selection_required()
                && !observed.legacy_selection_pending()
            {
                return Err(BackendError::Unavailable);
            }
            if *enabled {
                let audio = self.audio_settings_observation(deadline)?;
                let codec = audio.get_path("mic_format").and_then(Value::as_str);
                if audio.get_path("mic_enabled").and_then(Value::as_bool) != Some(true)
                    || !matches!(codec, Some("PCM" | "G711U" | "G711A" | "AAC" | "OPUS"))
                {
                    return Err(BackendError::Unavailable);
                }
            }
            before.push((*id, observed));
        }
        let apply = || -> Result<bool, BackendError> {
            for (id, enabled) in &updates {
                let command = format!(
                    r#"{{"cmd":"set-stream-audio-config","stream_id":{id},"enabled":{enabled}}}"#
                );
                let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
                require_ok(&reply)?;
                reply.require_only_fields(&[
                    "status",
                    "persistence",
                    "stream_id",
                    "configured_explicit",
                    "configured_enabled",
                ])?;
                if reply.value.get_path("persistence").and_then(Value::as_str)
                    != Some("checked-next-full-stack-restart")
                    || reply
                        .value
                        .get_path("stream_id")
                        .and_then(|value| value_u64(value).ok())
                        != Some(*id)
                    || reply
                        .value
                        .get_path("configured_explicit")
                        .and_then(Value::as_bool)
                        != Some(true)
                    || reply
                        .value
                        .get_path("configured_enabled")
                        .and_then(Value::as_bool)
                        != Some(*enabled)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let mut pending = false;
            for (id, enabled) in &updates {
                if self.stream_disk_audio_enabled(*id, true, deadline)? != Some(*enabled) {
                    return Err(BackendError::Upstream(502));
                }
                let old = before.iter().find(|v| v.0 == *id).unwrap().1;
                let after = self.stream_audio_observation(*id, deadline)?;
                if after.configured != Some(*enabled)
                    || after.policy_signature() != old.policy_signature()
                    || (after.active_enabled().is_none() && !after.legacy_selection_pending())
                {
                    return Err(BackendError::Upstream(502));
                }
                pending |= after.active_enabled() != Some(*enabled);
            }
            Ok(pending)
        };
        let pending = apply().map_err(|_| BackendError::PartialApply(
            "Stream audio policy may have changed, but save or owner readback was incomplete. Reload and explicitly retry. A full camera stack restart applies the saved policy."))?;
        Ok(BackendResponse::json(
            format!(r#"{{"status":"accepted","persistent":true,"pending_restart":{pending}}}"#)
                .into_bytes(),
        ))
    }

    fn stream_disk_audio_enabled(
        &self,
        id: u64,
        require_explicit: bool,
        deadline: Instant,
    ) -> Result<Option<bool>, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        let expected = format!("stream{id}");
        if reply.value.get_path("section").and_then(Value::as_str) != Some(expected.as_str())
            || reply
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        match reply.value.get_path("keys.audio_enabled") {
            Some(Value::String(value)) if value == "true" => Ok(Some(true)),
            Some(Value::String(value)) if value == "false" => Ok(Some(false)),
            None if !require_explicit => Ok(None),
            _ => Err(BackendError::Upstream(502)),
        }
    }
    pub(super) fn stream_enable_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<EnableObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-enabled","stream_id":{id}}}"#);
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&[
            "status",
            "persistence",
            "stream_id",
            "supported",
            "editable",
            "required",
            "active_enabled",
            "configured_enabled",
            "pending_restart",
            "motion_blocks_disable",
            "recorder_blocks_disable",
        ])?;
        let boolean = |name| {
            reply
                .value
                .get_path(name)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))
        };
        if reply.value.get_path("persistence").and_then(Value::as_str)
            != Some("checked-next-full-stack-restart")
            || reply
                .value
                .get_path("stream_id")
                .and_then(|value| value_u64(value).ok())
                != Some(id)
            || !boolean("supported")?
        {
            return Err(BackendError::Unavailable);
        }
        let observation = EnableObservation {
            editable: boolean("editable")?,
            required: boolean("required")?,
            active: boolean("active_enabled")?,
            configured: boolean("configured_enabled")?,
            motion_blocks_disable: boolean("motion_blocks_disable")?,
            recorder_blocks_disable: boolean("recorder_blocks_disable")?,
        };
        if observation.editable != (id == 1)
            || observation.required != (id == 0)
            || boolean("pending_restart")? != (observation.active != observation.configured)
            || (id == 0
                && (observation.motion_blocks_disable || observation.recorder_blocks_disable))
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(observation)
    }

    fn stream_disk_enabled(&self, id: u64, deadline: Instant) -> Result<bool, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        match reply.value.get_path("keys.enabled").and_then(Value::as_str) {
            Some("true") => Ok(true),
            Some("false") => Ok(false),
            _ => Err(BackendError::Upstream(502)),
        }
    }

    pub(super) fn stream_configured_enabled(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<bool, BackendError> {
        let observed = self.stream_enable_observation(id, deadline)?;
        if observed.configured != self.stream_disk_enabled(id, deadline)? {
            return Err(BackendError::Unavailable);
        }
        Ok(observed.configured)
    }

    fn stream_buffer_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<BufferObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-buffer-status","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        let fields = [
            "status",
            "persistence",
            "stream_id",
            "supported",
            "available",
            "editable",
            "profile",
            "profile_required_buffers",
            "hardware_limit_known",
            "active_buffers",
            "configured_buffers",
            "matches_configured",
            "profile_admitted",
        ];
        reply.require_only_fields(&fields).map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        if field("persistence")?.as_str() != Some("read-only-config-and-sdk")
            || value_u64(field("stream_id")?).map_err(upstream)? != id
            || field("editable")?.as_bool() != Some(false)
            || field("profile")?.as_str() != Some("dcs6100lhv2-a1-42m-22m-v1")
            || value_u64(field("profile_required_buffers")?).map_err(upstream)? != 1
            || field("hardware_limit_known")?.as_bool() != Some(false)
        {
            return Err(BackendError::Unavailable);
        }
        let supported = field("supported")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let available = field("available")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let active = match field("active_buffers")? {
            Value::Null => None,
            value => Some(value_u64(value).map_err(upstream)?),
        };
        let configured = match field("configured_buffers")? {
            Value::Null => None,
            value => Some(value_u64(value).map_err(upstream)?),
        };
        if available != active.is_some()
            || available && !supported
            || active.is_some_and(|value| value == 0 || value > i32::MAX as u64)
            || configured.is_some_and(|value| value == 0 || value > i32::MAX as u64)
            || field("matches_configured")?.as_bool()
                != Some(active.is_some() && active == configured)
            || field("profile_admitted")?.as_bool()
                != Some(active == Some(1) && configured == Some(1))
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(BufferObservation {
            supported,
            active,
            configured,
        })
    }

    fn stream_disk_buffers(&self, id: u64, deadline: Instant) -> Result<u64, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let raw = reply
            .value
            .get_path("keys.nr_vbs")
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        if raw.is_empty() || raw.len() > 10 || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(BackendError::Upstream(502));
        }
        let value = raw
            .parse::<u64>()
            .map_err(|_| BackendError::Upstream(502))?;
        if value == 0 || value > i32::MAX as u64 {
            return Err(BackendError::Upstream(502));
        }
        Ok(value)
    }

    fn stream_geometry_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<Option<GeometryObservation>, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-geometry","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "profile",
                "active_width",
                "active_height",
            ])
            .map_err(upstream)?;
        if reply.value.get_path("persistence").and_then(Value::as_str)
            != Some("checked-next-full-stack-restart")
            || reply
                .value
                .get_path("stream_id")
                .and_then(|value| value_u64(value).ok())
                != Some(id)
            || reply.value.get_path("supported").and_then(Value::as_bool) != Some(true)
            || reply.value.get_path("profile").and_then(Value::as_str)
                != Some("dcs6100lhv2-a1-42m-22m-v1")
        {
            return Err(BackendError::Unavailable);
        }
        let available = reply
            .value
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if !available {
            if !matches!(reply.value.get_path("active_width"), Some(Value::Null))
                || !matches!(reply.value.get_path("active_height"), Some(Value::Null))
            {
                return Err(BackendError::Upstream(502));
            }
            return Ok(None);
        }
        let width = value_u64(
            reply
                .value
                .get_path("active_width")
                .ok_or(BackendError::Upstream(502))?,
        )
        .map_err(upstream)?;
        let height = value_u64(
            reply
                .value
                .get_path("active_height")
                .ok_or(BackendError::Upstream(502))?,
        )
        .map_err(upstream)?;
        if !(32..=1920).contains(&width)
            || !(32..=1080).contains(&height)
            || width % 2 != 0
            || height % 2 != 0
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(Some(GeometryObservation { width, height }))
    }

    fn stream_disk_geometry(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<GeometryObservation, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let parse = |key: &str| -> Result<u64, BackendError> {
            let raw = reply
                .value
                .get_path(&format!("keys.{key}"))
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?;
            if raw.is_empty() || raw.len() > 4 || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err(BackendError::Upstream(502));
            }
            raw.parse::<u64>().map_err(|_| BackendError::Upstream(502))
        };
        Ok(GeometryObservation {
            width: parse("width")?,
            height: parse("height")?,
        })
    }

    fn stream_disk_motion_roi(&self, deadline: Instant) -> Result<MotionRoi, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"motion"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str) != Some("motion")
            || reply
                .value
                .get_path("keys.roi_count")
                .and_then(Value::as_str)
                != Some("1")
        {
            return Err(BackendError::Upstream(502));
        }
        let raw = reply
            .value
            .get_path("keys.roi0")
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        let values = raw
            .split(',')
            .map(|part| {
                if part.is_empty()
                    || part.len() > 4
                    || !part.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return Err(BackendError::Upstream(502));
                }
                part.parse::<u64>().map_err(|_| BackendError::Upstream(502))
            })
            .collect::<Result<Vec<_>, _>>()?;
        if values.len() != 4 || values[0] >= values[2] || values[1] >= values[3] {
            return Err(BackendError::Upstream(502));
        }
        Ok(MotionRoi {
            x0: values[0],
            y0: values[1],
            x1: values[2],
            y1: values[3],
        })
    }

    fn update_enabled(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.len() != 1 || !streams.contains_key("stream1") {
            return Err(BackendError::Protocol);
        }
        let fields = streams["stream1"]
            .as_object()
            .ok_or(BackendError::Protocol)?;
        if fields.len() != 1 {
            return Err(BackendError::Protocol);
        }
        let requested = fields
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
        let before = self.stream_enable_observation(1, deadline)?;
        let _saved = self.stream_disk_enabled(1, deadline)?;
        let recorder_idle = if requested {
            true
        } else {
            let recorder = self.recording_observation(1, deadline)?;
            recorder
                .value
                .get_path("recording")
                .and_then(Value::as_bool)
                == Some(false)
                && recorder
                    .value
                    .get_path("file_closed")
                    .and_then(Value::as_bool)
                    == Some(true)
        };
        if !before.editable
            || (!requested && (before.motion_blocks_disable || before.recorder_blocks_disable))
            || (!requested && !recorder_idle)
        {
            return Err(BackendError::Unavailable);
        }
        let command = format!(
            r#"{{"cmd":"set-stream-enabled-config","stream_id":1,"enabled":{requested}}}"#,
        );
        let apply = || -> Result<(), BackendError> {
            let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
            require_ok(&reply)?;
            let configured = reply
                .value
                .get_path("configured_enabled")
                .and_then(Value::as_bool);
            let active = reply
                .value
                .get_path("active_enabled")
                .and_then(Value::as_bool);
            if configured != Some(requested) || active != Some(before.active) {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let after = self.stream_enable_observation(1, deadline)?;
            if self.stream_disk_enabled(1, deadline)? != requested
                || after.configured != requested
                || after.active != before.active
                || (after.active != after.configured) != (before.active != requested)
            {
                return Err(BackendError::Upstream(502));
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "Substream configuration may have changed, but save or readback was incomplete. Reload and explicitly retry. A full camera stack restart is required before the saved state becomes active.",
        ))?;
        Ok(BackendResponse::json(
            format!(
                r#"{{"status":"accepted","persistent":true,"pending_restart":{}}}"#,
                before.active != requested,
            )
            .into_bytes(),
        ))
    }

    fn update_geometry(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut requested = [None, None];
        for (name, value) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = value.as_object().ok_or(BackendError::Protocol)?;
            if fields.len() != 2 {
                return Err(BackendError::Protocol);
            }
            let width = value_u64(fields.get("width").ok_or(BackendError::Protocol)?)
                .map_err(|_| BackendError::Protocol)?;
            let height = value_u64(fields.get("height").ok_or(BackendError::Protocol)?)
                .map_err(|_| BackendError::Protocol)?;
            requested[id] = Some(GeometryObservation { width, height });
        }
        let admitted = |id: usize, value: GeometryObservation| match id {
            0 => matches!((value.width, value.height), (1920, 1080) | (1280, 720)),
            1 => matches!(
                (value.width, value.height),
                (640, 360) | (480, 270) | (320, 180)
            ),
            _ => false,
        };
        if requested
            .iter()
            .enumerate()
            .any(|(id, value)| value.is_some_and(|value| !admitted(id, value)))
        {
            return Err(BackendError::Protocol);
        }
        let active = [
            self.stream_geometry_observation(0, deadline)?
                .ok_or(BackendError::Unavailable)?,
            self.stream_geometry_observation(1, deadline)?
                .ok_or(BackendError::Unavailable)?,
        ];
        let saved = [
            self.stream_disk_geometry(0, deadline)?,
            self.stream_disk_geometry(1, deadline)?,
        ];
        let saved_roi = self.stream_disk_motion_roi(deadline)?;
        let desired = [
            requested[0].unwrap_or(saved[0]),
            requested[1].unwrap_or(saved[1]),
        ];
        if !admitted(0, desired[0])
            || !admitted(1, desired[1])
            || desired[1].width * 3 > desired[0].width
            || desired[1].height * 3 > desired[0].height
        {
            return Err(BackendError::Protocol);
        }
        let saved_full_frame_roi = MotionRoi {
            x0: 0,
            y0: 0,
            x1: saved[1]
                .width
                .checked_sub(1)
                .ok_or(BackendError::Protocol)?,
            y1: saved[1]
                .height
                .checked_sub(1)
                .ok_or(BackendError::Protocol)?,
        };
        let expected_roi = if desired[1] != saved[1] && saved_roi == saved_full_frame_roi {
            MotionRoi {
                x0: 0,
                y0: 0,
                x1: desired[1].width - 1,
                y1: desired[1].height - 1,
            }
        } else {
            if saved_roi.x1 >= desired[1].width || saved_roi.y1 >= desired[1].height {
                return Err(BackendError::Protocol);
            }
            saved_roi
        };
        let expected_roi_adjustment = expected_roi != saved_roi;
        let command = format!(
            r#"{{"cmd":"set-stream-geometry-config","stream0_width":{},"stream0_height":{},"stream1_width":{},"stream1_height":{}}}"#,
            desired[0].width, desired[0].height, desired[1].width, desired[1].height,
        );
        let apply = || -> Result<(), BackendError> {
            let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
            require_ok(&reply)?;
            reply.require_only_fields(&[
                "status",
                "persistence",
                "motion_roi_full_frame_adjusted",
                "pending_restart",
            ])?;
            if reply.value.get_path("persistence").and_then(Value::as_str)
                != Some("checked-next-full-stack-restart")
                || reply
                    .value
                    .get_path("motion_roi_full_frame_adjusted")
                    .and_then(Value::as_bool)
                    != Some(expected_roi_adjustment)
                || reply
                    .value
                    .get_path("pending_restart")
                    .and_then(Value::as_bool)
                    != Some(active != desired)
            {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            if self.stream_disk_motion_roi(deadline)? != expected_roi {
                return Err(BackendError::Upstream(502));
            }
            for id in 0..=1 {
                if self.stream_disk_geometry(id as u64, deadline)? != desired[id]
                    || self.stream_geometry_observation(id as u64, deadline)? != Some(active[id])
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "Stream geometry configuration may have changed, but save or readback was incomplete. Reload and explicitly retry. A full camera stack restart is still required before the saved geometry becomes active.",
        ))?;
        Ok(BackendResponse::json(
            format!(
                r#"{{"status":"accepted","persistent":true,"pending_restart":{}}}"#,
                active != desired,
            )
            .into_bytes(),
        ))
    }

    fn stream_profile_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<ProfileObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-profile","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "recovery_required",
                "profile",
            ])
            .map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        if field("persistence")?.as_str() != Some("checked-restart-profile")
            || value_u64(field("stream_id")?).map_err(upstream)? != id
        {
            return Err(BackendError::Upstream(502));
        }
        let supported = field("supported")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let available = field("available")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let recovery_required = field("recovery_required")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let profile = if available {
            let profile = value_u64(field("profile")?).map_err(upstream)?;
            if !supported || recovery_required || profile > 2 {
                return Err(BackendError::Upstream(502));
            }
            Some(profile)
        } else {
            if !matches!(field("profile")?, Value::Null) {
                return Err(BackendError::Upstream(502));
            }
            None
        };
        Ok(ProfileObservation {
            supported,
            profile,
            recovery_required,
        })
    }

    fn stream_disk_profile(&self, id: u64, deadline: Instant) -> Result<Option<u64>, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let Some(raw) = reply.value.get_path("keys.profile") else {
            return Ok(None);
        };
        let raw = raw.as_str().ok_or(BackendError::Upstream(502))?;
        if raw.len() != 1 || !matches!(raw, "0" | "1" | "2") {
            return Err(BackendError::Upstream(502));
        }
        Ok(Some((raw.as_bytes()[0] - b'0') as u64))
    }

    fn update_profile(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, values) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = values.as_object().ok_or(BackendError::Protocol)?;
            if fields.len() != 1 {
                return Err(BackendError::Protocol);
            }
            let profile = value_u64(fields.get("profile").ok_or(BackendError::Protocol)?)
                .map_err(|_| BackendError::Protocol)?;
            if profile > 2 {
                return Err(BackendError::Protocol);
            }
            updates.push((id, profile));
        }
        for (id, _) in &updates {
            let observed = self.stream_profile_observation(*id, deadline)?;
            if !observed.supported || observed.recovery_required || observed.profile.is_none() {
                return Err(BackendError::Unavailable);
            }
        }
        let apply = || -> Result<(), BackendError> {
            for (id, profile) in &updates {
                let command = format!(
                    r#"{{"cmd":"set-stream-profile","stream_id":{id},"profile":{profile}}}"#
                );
                require_ok(
                    &self
                        .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
                        .map_err(upstream)?,
                )?;
                if self.stream_profile_observation(*id, deadline)?.profile != Some(*profile) {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            for (id, profile) in &updates {
                if self.stream_disk_profile(*id, deadline)? != Some(*profile)
                    || self.stream_profile_observation(*id, deadline)?.profile != Some(*profile)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "Stream profile may have changed, but restart, save or readback was incomplete. Reload before an explicit retry.",
        ))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }

    fn stream_codec_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<CodecObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-codec","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "recovery_required",
                "codecs",
                "codec",
                "width",
                "height",
            ])
            .map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        if field("persistence")?.as_str() != Some("checked-restart-codec")
            || value_u64(field("stream_id")?).map_err(upstream)? != id
        {
            return Err(BackendError::Upstream(502));
        }
        let supported = field("supported")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let available = field("available")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let recovery_required = field("recovery_required")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let raw_codecs = field("codecs")?
            .as_array()
            .ok_or(BackendError::Upstream(502))?;
        let mut codecs = Vec::new();
        for value in raw_codecs {
            let codec = match value.as_str() {
                Some("h264") => "H264",
                Some("h265") => "H265",
                _ => return Err(BackendError::Upstream(502)),
            };
            if codecs.contains(&codec) {
                return Err(BackendError::Upstream(502));
            }
            codecs.push(codec);
        }
        if (!supported && !codecs.is_empty())
            || (supported && codecs.first() != Some(&"H264"))
            || codecs.len() > 2
            || (codecs.len() == 2 && codecs[1] != "H265")
            || (available && (!supported || recovery_required))
        {
            return Err(BackendError::Upstream(502));
        }
        let codec = if available {
            let codec = match field("codec")?.as_str() {
                Some("h264") => "H264",
                Some("h265") => "H265",
                _ => return Err(BackendError::Upstream(502)),
            };
            let width = value_u64(field("width")?).map_err(upstream)?;
            let height = value_u64(field("height")?).map_err(upstream)?;
            if !codecs.contains(&codec)
                || width == 0
                || height == 0
                || width > 4096
                || height > 4096
            {
                return Err(BackendError::Upstream(502));
            }
            Some(codec)
        } else {
            if !matches!(field("codec")?, Value::Null)
                || !matches!(field("width")?, Value::Null)
                || !matches!(field("height")?, Value::Null)
            {
                return Err(BackendError::Upstream(502));
            }
            None
        };
        Ok(CodecObservation {
            supported,
            codec,
            codecs,
            recovery_required,
        })
    }

    fn stream_disk_codec(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<Option<&'static str>, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        match reply.value.get_path("keys.codec").and_then(Value::as_str) {
            Some("h264") => Ok(Some("H264")),
            Some("h265") => Ok(Some("H265")),
            None => Ok(None),
            _ => Err(BackendError::Upstream(502)),
        }
    }

    fn update_codec(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, values) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = values.as_object().ok_or(BackendError::Protocol)?;
            if fields.len() != 1 {
                return Err(BackendError::Protocol);
            }
            let codec = match fields.get("format").and_then(Value::as_str) {
                Some("H264") => ("H264", "h264"),
                Some("H265") => ("H265", "h265"),
                _ => return Err(BackendError::Protocol),
            };
            updates.push((id, codec.0, codec.1));
        }
        for (id, codec, _) in &updates {
            let observed = self.stream_codec_observation(*id, deadline)?;
            if !observed.supported
                || observed.recovery_required
                || !observed.codecs.contains(codec)
                || observed.codec.is_none()
            {
                return Err(BackendError::Unavailable);
            }
        }
        let apply = || -> Result<(), BackendError> {
            for (id, codec, native) in &updates {
                let command =
                    format!(r#"{{"cmd":"set-stream-codec","stream_id":{id},"codec":"{native}"}}"#);
                require_ok(
                    &self
                        .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
                        .map_err(upstream)?,
                )?;
                if self.stream_codec_observation(*id, deadline)?.codec != Some(*codec) {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            for (id, codec, _) in &updates {
                if self.stream_disk_codec(*id, deadline)? != Some(*codec)
                    || self.stream_codec_observation(*id, deadline)?.codec != Some(*codec)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "Stream codec may have changed, but restart, save or readback was incomplete. Reload before an explicit retry.",
        ))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }

    fn fps_reply(
        &self,
        id: u64,
        requested: Option<u64>,
        deadline: Instant,
    ) -> Result<String, BackendError> {
        let command = requested.map_or_else(
            || format!(r#"{{"cmd":"get-stream-fps","stream_id":{id}}}"#),
            |fps| format!(r#"{{"cmd":"set-stream-fps","stream_id":{id},"fps":{fps}}}"#),
        );
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "error",
                "stream_id",
                "recovery_required",
                "persistence_pending",
                "live_applied",
                "persisted",
                "fps",
                "evidence",
                "monitoring",
            ])
            .map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        let status = field("status")?
            .as_str()
            .ok_or(BackendError::Upstream(502))?;
        if !["ok", "error"].contains(&status)
            || value_u64(field("stream_id")?).map_err(upstream)? != id
            || field("evidence")?.as_str() != Some("SDK configuration readback")
        {
            return Err(BackendError::Upstream(502));
        }
        let recovery = field("recovery_required")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let pending = field("persistence_pending")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        for name in ["live_applied", "persisted"] {
            if !matches!(field(name)?, Value::Null) && field(name)?.as_bool().is_none() {
                return Err(BackendError::Upstream(502));
            }
        }
        let fps = if matches!(field("fps")?, Value::Null) {
            None
        } else {
            let fps = value_u64(field("fps")?).map_err(upstream)?;
            if !(1..=30).contains(&fps) {
                return Err(BackendError::Upstream(502));
            }
            Some(fps)
        };
        let monitoring = field("monitoring")?
            .as_object()
            .ok_or(BackendError::Upstream(502))?;
        let names = ["related", "active", "paused", "receiving", "thread_owned"];
        if monitoring.len() != names.len()
            || names
                .iter()
                .any(|name| monitoring.get(*name).and_then(Value::as_bool).is_none())
            || (recovery && fps.is_some())
            || (status == "ok"
                && (recovery || fps.is_none() || field("live_applied")?.as_bool() != Some(true)))
            || (requested.is_some()
                && status == "ok"
                && (fps != requested || pending || field("persisted")?.as_bool() != Some(true)))
        {
            return Err(BackendError::Upstream(502));
        }
        // Never forward arbitrary daemon error text into the public response.
        let available = !recovery
            && fps.is_some()
            && field("live_applied")?.as_bool() == Some(true)
            && (status == "ok" || pending);
        Ok(format!(
            r#"{{"supported":true,"available":{available},"status":"{status}","stream_id":{id},"recovery_required":{recovery},"persistence_pending":{pending},"live_applied":{},"persisted":{},"fps":{},"monitoring":{}}}"#,
            field("live_applied")?.to_json(),
            field("persisted")?.to_json(),
            field("fps")?.to_json(),
            field("monitoring")?.to_json()
        ))
    }

    fn update_fps(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.len() != 1 {
            return Err(BackendError::Protocol);
        }
        let (name, values) = streams.iter().next().ok_or(BackendError::Protocol)?;
        let id = match name.as_str() {
            "stream0" => 0,
            "stream1" => 1,
            _ => return Err(BackendError::Protocol),
        };
        let values = values.as_object().ok_or(BackendError::Protocol)?;
        if values.len() != 1 {
            return Err(BackendError::Protocol);
        }
        let fps = value_u64(values.get("fps").ok_or(BackendError::Protocol)?)
            .map_err(|_| BackendError::Protocol)?;
        if !(1..=30).contains(&fps) {
            return Err(BackendError::Protocol);
        }
        // A single checked backend operation owns preflight, mutation and save.
        // No config-save, legacy setter, readback retry or restart is sent here.
        let receipt = self.fps_reply(id, Some(fps), deadline).map_err(|_| BackendError::PartialApply("FPS result is unknown. Keep your draft and reload the observed state before an explicit retry."))?;
        let value =
            crate::json::parse(receipt.as_bytes()).map_err(|_| BackendError::Upstream(502))?;
        let accepted = value.get_path("status").and_then(Value::as_str) == Some("ok");
        Ok(BackendResponse::json(
            format!(
                r#"{{"status":"{}","persistent":{accepted},"fps_result":{receipt}}}"#,
                if accepted { "accepted" } else { "error" }
            )
            .into_bytes(),
        ))
    }
    fn stream_gop_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<GopObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-gop","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "gop",
            ])
            .map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        if field("persistence")?.as_str() != Some("checked-startup-gop") {
            return Err(BackendError::Unavailable);
        }
        if value_u64(field("stream_id")?).map_err(upstream)? != id {
            return Err(BackendError::Upstream(502));
        }
        let supported = field("supported")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let available = field("available")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let value = if available {
            let value = value_u64(field("gop")?).map_err(upstream)?;
            if !supported || !(1..=65535).contains(&value) {
                return Err(BackendError::Upstream(502));
            }
            Some(value)
        } else {
            if !matches!(field("gop")?, Value::Null) {
                return Err(BackendError::Upstream(502));
            }
            None
        };
        Ok(GopObservation { supported, value })
    }

    fn stream_disk_gop(&self, id: u64, deadline: Instant) -> Result<Option<u64>, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let keys = reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        let Some(raw) = keys.get("gop") else {
            return Ok(None);
        };
        let raw = raw.as_str().ok_or(BackendError::Upstream(502))?;
        if raw.is_empty() || raw.len() > 5 || !raw.bytes().all(|c| c.is_ascii_digit()) {
            return Err(BackendError::Upstream(502));
        }
        let value = raw
            .parse::<u64>()
            .map_err(|_| BackendError::Upstream(502))?;
        if !(1..=65535).contains(&value) {
            return Err(BackendError::Upstream(502));
        }
        Ok(Some(value))
    }

    fn stream_gop_mode_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<GopModeObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-gop-mode","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "active_mode",
            ])
            .map_err(upstream)?;
        if reply.value.get_path("persistence").and_then(Value::as_str)
            != Some("checked-next-full-stack-restart")
            || reply
                .value
                .get_path("stream_id")
                .and_then(|value| value_u64(value).ok())
                != Some(id)
        {
            return Err(BackendError::Upstream(502));
        }
        let supported = reply
            .value
            .get_path("supported")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let available = reply
            .value
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let active_mode = if available {
            let raw = reply
                .value
                .get_path("active_mode")
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?;
            let mode = gop_mode(raw)
                .filter(|(_, native)| *native == raw)
                .map(|(public, _)| public)
                .ok_or(BackendError::Upstream(502))?;
            if !supported {
                return Err(BackendError::Upstream(502));
            }
            Some(mode)
        } else {
            if !matches!(reply.value.get_path("active_mode"), Some(Value::Null)) {
                return Err(BackendError::Upstream(502));
            }
            None
        };
        Ok(GopModeObservation {
            supported,
            active_mode,
        })
    }

    fn stream_disk_gop_mode(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<&'static str, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let keys = reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        let raw = match keys.get("gop_mode") {
            None => "default",
            Some(value) => value.as_str().ok_or(BackendError::Upstream(502))?,
        };
        gop_mode(raw)
            .filter(|(_, native)| *native == raw)
            .map(|(public, _)| public)
            .ok_or(BackendError::Upstream(502))
    }

    fn stream_encoding_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<EncodingObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-encoding","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "rc_mode",
                "bitrate",
                "bitrate_min",
                "bitrate_max",
                "bitrate_step",
            ])
            .map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        if field("persistence")?.as_str() != Some("checked-live-encoding")
            || value_u64(field("stream_id")?).map_err(upstream)? != id
        {
            return Err(BackendError::Upstream(502));
        }
        let supported = field("supported")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let available = field("available")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let min = value_u64(field("bitrate_min")?).map_err(upstream)?;
        let max = value_u64(field("bitrate_max")?).map_err(upstream)?;
        let step = value_u64(field("bitrate_step")?).map_err(upstream)?;
        if min != 1_000 || max != 100_000_000 || step != 1_000 {
            return Err(BackendError::Upstream(502));
        }
        let (mode, bitrate) = if available {
            let raw_mode = field("rc_mode")?
                .as_str()
                .ok_or(BackendError::Upstream(502))?;
            let mode = encoding_mode(raw_mode)
                .filter(|(_, native)| *native == raw_mode)
                .map(|(public, _)| public)
                .ok_or(BackendError::Upstream(502))?;
            let bitrate = value_u64(field("bitrate")?).map_err(upstream)?;
            if !supported || !(min..=max).contains(&bitrate) || bitrate % step != 0 {
                return Err(BackendError::Upstream(502));
            }
            (Some(mode), Some(bitrate))
        } else {
            if !matches!(field("rc_mode")?, Value::Null)
                || !matches!(field("bitrate")?, Value::Null)
            {
                return Err(BackendError::Upstream(502));
            }
            (None, None)
        };
        Ok(EncodingObservation {
            supported,
            mode,
            bitrate,
            min,
            max,
            step,
        })
    }

    fn stream_disk_encoding(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<Option<(&'static str, u64)>, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let keys = reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        let mode = keys.get("rc_mode");
        let bitrate = keys.get("bitrate");
        if mode.is_none() && bitrate.is_none() {
            return Ok(None);
        }
        let mode = mode
            .and_then(Value::as_str)
            .and_then(encoding_mode)
            .filter(|(_, native)| Some(*native) == mode.and_then(Value::as_str))
            .map(|(public, _)| public)
            .ok_or(BackendError::Upstream(502))?;
        let raw = bitrate
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        if raw.is_empty() || raw.len() > 9 || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(BackendError::Upstream(502));
        }
        let bitrate = raw
            .parse::<u64>()
            .map_err(|_| BackendError::Upstream(502))?;
        if !(1_000..=100_000_000).contains(&bitrate) || bitrate % 1_000 != 0 {
            return Err(BackendError::Upstream(502));
        }
        Ok(Some((mode, bitrate)))
    }

    fn update_encoding(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, values) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let values = values.as_object().ok_or(BackendError::Protocol)?;
            if values.len() != 2 {
                return Err(BackendError::Protocol);
            }
            let public_mode = values
                .get("mode")
                .and_then(Value::as_str)
                .ok_or(BackendError::Protocol)?;
            let (mode, native_mode) = encoding_mode(public_mode).ok_or(BackendError::Protocol)?;
            if mode != public_mode {
                return Err(BackendError::Protocol);
            }
            let bitrate = value_u64(values.get("bitrate").ok_or(BackendError::Protocol)?)
                .map_err(|_| BackendError::Protocol)?;
            if !(1_000..=100_000_000).contains(&bitrate) || bitrate % 1_000 != 0 {
                return Err(BackendError::Protocol);
            }
            updates.push((id, mode, native_mode, bitrate));
        }
        for (id, _, _, _) in &updates {
            let observed = self.stream_encoding_observation(*id, deadline)?;
            if !observed.supported || observed.mode.is_none() {
                return Err(BackendError::Unavailable);
            }
        }
        let apply = || -> Result<(), BackendError> {
            for (id, mode, native_mode, bitrate) in &updates {
                let command = format!(
                    r#"{{"cmd":"set-stream-encoding","stream_id":{id},"rc_mode":"{native_mode}","bitrate":{bitrate}}}"#
                );
                require_ok(
                    &self
                        .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
                        .map_err(upstream)?,
                )?;
                let observed = self.stream_encoding_observation(*id, deadline)?;
                if observed.mode != Some(*mode) || observed.bitrate != Some(*bitrate) {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            for (id, mode, _, bitrate) in &updates {
                if self.stream_disk_encoding(*id, deadline)? != Some((*mode, *bitrate)) {
                    return Err(BackendError::Upstream(502));
                }
                let observed = self.stream_encoding_observation(*id, deadline)?;
                if observed.mode != Some(*mode) || observed.bitrate != Some(*bitrate) {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "Stream encoding may have changed, but apply, save or readback was incomplete. Reload and explicitly retry saving.",
        ))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }

    fn stream_rc_config_observation(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<RcConfigObservation, BackendError> {
        let command = format!(r#"{{"cmd":"get-stream-rc-config","stream_id":{id}}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&[
                "status",
                "persistence",
                "stream_id",
                "supported",
                "available",
                "active_mode",
                "active_qp",
                "qp_min",
                "qp_max",
            ])
            .map_err(upstream)?;
        let field = |name| {
            reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))
        };
        if field("persistence")?.as_str() != Some("checked-next-full-stack-restart")
            || value_u64(field("stream_id")?).map_err(upstream)? != id
        {
            return Err(BackendError::Upstream(502));
        }
        let supported = field("supported")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let available = field("available")?
            .as_bool()
            .ok_or(BackendError::Upstream(502))?;
        let min = value_u64(field("qp_min")?).map_err(upstream)?;
        let max = value_u64(field("qp_max")?).map_err(upstream)?;
        if min != 0 || max != 51 {
            return Err(BackendError::Upstream(502));
        }
        let (active_mode, active_qp) = if available {
            let raw_mode = field("active_mode")?
                .as_str()
                .ok_or(BackendError::Upstream(502))?;
            let mode = rc_config_mode(raw_mode)
                .filter(|(_, native)| *native == raw_mode)
                .map(|(public, _)| public)
                .ok_or(BackendError::Upstream(502))?;
            let qp = if mode == "FIXQP" {
                let value = value_u64(field("active_qp")?).map_err(upstream)?;
                if !(min..=max).contains(&value) {
                    return Err(BackendError::Upstream(502));
                }
                Some(value)
            } else {
                if !matches!(field("active_qp")?, Value::Null) {
                    return Err(BackendError::Upstream(502));
                }
                None
            };
            if !supported {
                return Err(BackendError::Upstream(502));
            }
            (Some(mode), qp)
        } else {
            if !matches!(field("active_mode")?, Value::Null)
                || !matches!(field("active_qp")?, Value::Null)
            {
                return Err(BackendError::Upstream(502));
            }
            (None, None)
        };
        Ok(RcConfigObservation {
            supported,
            active_mode,
            active_qp,
            min,
            max,
        })
    }

    fn stream_disk_rc_config(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<Option<SavedRcConfig>, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let keys = reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        let Some(raw_mode) = keys.get("rc_mode").and_then(Value::as_str) else {
            return Ok(None);
        };
        let mode = rc_config_mode(raw_mode)
            .filter(|(_, native)| *native == raw_mode)
            .map(|(public, _)| public)
            .ok_or(BackendError::Upstream(502))?;
        let qp = match keys.get("init_qp") {
            Some(value) => {
                let raw = value.as_str().ok_or(BackendError::Upstream(502))?;
                if raw.is_empty() || raw.len() > 2 || !raw.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return Err(BackendError::Upstream(502));
                }
                let qp = raw
                    .parse::<u64>()
                    .map_err(|_| BackendError::Upstream(502))?;
                if qp > 51 {
                    return Err(BackendError::Upstream(502));
                }
                Some(qp)
            }
            None if mode == "FIXQP" => return Err(BackendError::Upstream(502)),
            None => None,
        };
        Ok(Some(SavedRcConfig { mode, qp }))
    }

    fn stream_disk_bitrate(&self, id: u64, deadline: Instant) -> Result<u64, BackendError> {
        let command = format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#);
        let reply = self
            .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
            .map_err(upstream)?;
        require_ok(&reply).map_err(upstream)?;
        reply
            .require_only_fields(&["status", "section", "keys"])
            .map_err(upstream)?;
        if reply.value.get_path("section").and_then(Value::as_str)
            != Some(format!("stream{id}").as_str())
        {
            return Err(BackendError::Upstream(502));
        }
        let raw = reply
            .value
            .get_path("keys.bitrate")
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        if raw.is_empty() || raw.len() > 9 || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(BackendError::Upstream(502));
        }
        let bitrate = raw
            .parse::<u64>()
            .map_err(|_| BackendError::Upstream(502))?;
        if !(1_000..=100_000_000).contains(&bitrate) || bitrate % 1_000 != 0 {
            return Err(BackendError::Upstream(502));
        }
        Ok(bitrate)
    }

    fn update_rc_config(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, values) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = values.as_object().ok_or(BackendError::Protocol)?;
            if fields.len() != 2 {
                return Err(BackendError::Protocol);
            }
            let public_mode = fields
                .get("mode")
                .and_then(Value::as_str)
                .ok_or(BackendError::Protocol)?;
            let (mode, native_mode) = rc_config_mode(public_mode).ok_or(BackendError::Protocol)?;
            if mode != public_mode {
                return Err(BackendError::Protocol);
            }
            let qp = value_u64(fields.get("qp_init").ok_or(BackendError::Protocol)?)
                .map_err(|_| BackendError::Protocol)?;
            if qp > 51 {
                return Err(BackendError::Protocol);
            }
            updates.push((id, mode, native_mode, qp));
        }

        let mut before = Vec::new();
        for (id, mode, _, _) in &updates {
            let observed = self.stream_rc_config_observation(*id, deadline)?;
            if !observed.supported
                || observed.active_mode.is_none()
                || (observed.active_mode != Some("FIXQP") && *mode != "FIXQP")
            {
                return Err(BackendError::Unavailable);
            }
            let bitrate = self.stream_disk_bitrate(*id, deadline)?;
            before.push((*id, observed, bitrate));
        }

        let apply = || -> Result<bool, BackendError> {
            let mut any_pending = false;
            for (id, mode, native_mode, qp) in &updates {
                let observed = before
                    .iter()
                    .find(|value| value.0 == *id)
                    .map(|value| value.1)
                    .ok_or(BackendError::Upstream(502))?;
                let expected_pending = observed.active_mode != Some(*mode)
                    || (*mode == "FIXQP" && observed.active_qp != Some(*qp));
                let command = format!(
                    r#"{{"cmd":"set-stream-rc-config","stream_id":{id},"rc_mode":"{native_mode}","initial_qp":{qp}}}"#
                );
                let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
                require_ok(&reply)?;
                reply.require_only_fields(&["status", "persistence", "pending_restart"])?;
                if reply.value.get_path("persistence").and_then(Value::as_str)
                    != Some("checked-next-full-stack-restart")
                    || reply
                        .value
                        .get_path("pending_restart")
                        .and_then(Value::as_bool)
                        != Some(expected_pending)
                {
                    return Err(BackendError::Upstream(502));
                }
                any_pending |= expected_pending;
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            for (id, mode, _, qp) in &updates {
                let prior = before
                    .iter()
                    .find(|value| value.0 == *id)
                    .ok_or(BackendError::Upstream(502))?;
                let expected = SavedRcConfig {
                    mode,
                    qp: Some(*qp),
                };
                if self.stream_disk_rc_config(*id, deadline)? != Some(expected)
                    || self.stream_disk_bitrate(*id, deadline)? != prior.2
                    || self.stream_rc_config_observation(*id, deadline)? != prior.1
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(any_pending)
        };
        let pending = apply().map_err(|_| BackendError::PartialApply(
            "FIXQP configuration may have changed, but save or readback was incomplete. Reload and explicitly retry. A full camera restart is required before the saved rate-control mode becomes active.",
        ))?;
        Ok(BackendResponse::json(
            format!(r#"{{"status":"accepted","persistent":true,"pending_restart":{pending}}}"#)
                .into_bytes(),
        ))
    }

    fn update_gop_mode(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let streams = request.as_object().ok_or(BackendError::Protocol)?;
        if streams.is_empty() || streams.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, values) in streams {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = values.as_object().ok_or(BackendError::Protocol)?;
            if fields.len() != 1 {
                return Err(BackendError::Protocol);
            }
            let public = fields
                .get("gop_mode")
                .and_then(Value::as_str)
                .ok_or(BackendError::Protocol)?;
            let (mode, native) = gop_mode(public)
                .filter(|(mode, _)| *mode == public)
                .ok_or(BackendError::Protocol)?;
            updates.push((id, mode, native));
        }
        let mut before = Vec::new();
        for (id, _, _) in &updates {
            let observed = self.stream_gop_mode_observation(*id, deadline)?;
            if !observed.supported || observed.active_mode.is_none() {
                return Err(BackendError::Unavailable);
            }
            before.push((*id, observed));
        }
        let apply = || -> Result<bool, BackendError> {
            let mut pending = false;
            for (id, mode, native) in &updates {
                let observed = before
                    .iter()
                    .find(|value| value.0 == *id)
                    .map(|value| value.1)
                    .ok_or(BackendError::Upstream(502))?;
                let expected_pending = observed.active_mode != Some(*mode);
                let command = format!(
                    r#"{{"cmd":"set-stream-gop-mode","stream_id":{id},"gop_mode":"{native}"}}"#
                );
                let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
                require_ok(&reply)?;
                reply.require_only_fields(&["status", "persistence", "pending_restart"])?;
                if reply.value.get_path("persistence").and_then(Value::as_str)
                    != Some("checked-next-full-stack-restart")
                    || reply
                        .value
                        .get_path("pending_restart")
                        .and_then(Value::as_bool)
                        != Some(expected_pending)
                {
                    return Err(BackendError::Upstream(502));
                }
                pending |= expected_pending;
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            for (id, mode, _) in &updates {
                let prior = before
                    .iter()
                    .find(|value| value.0 == *id)
                    .map(|value| value.1)
                    .ok_or(BackendError::Upstream(502))?;
                if self.stream_disk_gop_mode(*id, deadline)? != *mode
                    || self.stream_gop_mode_observation(*id, deadline)? != prior
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(pending)
        };
        let pending = apply().map_err(|_| BackendError::PartialApply(
            "GOP mode configuration may have changed, but save or readback was incomplete. Reload and explicitly retry. A full camera restart is required before the saved GOP mode becomes active.",
        ))?;
        Ok(BackendResponse::json(
            format!(r#"{{"status":"accepted","persistent":true,"pending_restart":{pending}}}"#)
                .into_bytes(),
        ))
    }

    pub(super) fn stream_config(
        &self,
        id: u64,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let observed = self.stream_gop_observation(id, deadline)?;
        let saved = self.stream_disk_gop(id, deadline).ok().flatten();
        let fps = self
            .fps_reply(id, None, deadline)
            .unwrap_or_else(|_| r#"{"supported":false,"available":false}"#.into());
        let encoding = self.stream_encoding_observation(id, deadline).ok();
        let encoding_json = if let Some(observed) = encoding {
            let saved = if observed.supported {
                self.stream_disk_encoding(id, deadline).ok().flatten()
            } else {
                None
            };
            let mode = observed
                .mode
                .map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let bitrate = observed
                .bitrate
                .map_or_else(|| "null".to_owned(), |value| value.to_string());
            let saved_mode = saved
                .map(|value| value.0)
                .map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let saved_bitrate =
                saved.map_or_else(|| "null".to_owned(), |value| value.1.to_string());
            let live = observed.mode.zip(observed.bitrate);
            let matches_saved = live.is_some() && live == saved;
            format!(
                r#"{{"supported":{},"available":{},"rc_mode":{mode},"bitrate":{bitrate},"saved_rc_mode":{saved_mode},"saved_bitrate":{saved_bitrate},"matches_saved":{matches_saved},"bitrate_min":{},"bitrate_max":{},"bitrate_step":{},"modes":["CBR","VBR","CAPPED_VBR","CAPPED_QUALITY"]}}"#,
                observed.supported,
                observed.mode.is_some(),
                observed.min,
                observed.max,
                observed.step
            )
        } else {
            r#"{"supported":false,"available":false}"#.to_owned()
        };
        let codec = self.stream_codec_observation(id, deadline).ok();
        let codec_json = if let Some(observed) = codec.filter(|value| value.supported) {
            let saved = self.stream_disk_codec(id, deadline).ok().flatten();
            let live = observed
                .codec
                .map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let saved_value =
                saved.map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let codecs = observed
                .codecs
                .iter()
                .map(|value| format!(r#""{value}""#))
                .collect::<Vec<_>>()
                .join(",");
            format!(
                r#"{{"supported":{},"available":{},"recovery_required":{},"codec":{live},"saved_codec":{saved_value},"matches_saved":{},"codecs":[{codecs}]}}"#,
                observed.supported,
                observed.codec.is_some(),
                observed.recovery_required,
                observed.codec.is_some() && observed.codec == saved
            )
        } else {
            r#"{"supported":false,"available":false}"#.to_owned()
        };
        let profile = self.stream_profile_observation(id, deadline).ok();
        let profile_json = if let Some(observed) = profile.filter(|value| value.supported) {
            let saved = self.stream_disk_profile(id, deadline).ok().flatten();
            let live = observed
                .profile
                .map_or_else(|| "null".to_owned(), |value| value.to_string());
            let saved_value = saved.map_or_else(|| "null".to_owned(), |value| value.to_string());
            format!(
                r#"{{"supported":true,"available":{},"recovery_required":{},"profile":{live},"saved_profile":{saved_value},"matches_saved":{},"profiles":[0,1,2]}}"#,
                observed.profile.is_some(),
                observed.recovery_required,
                observed.profile.is_some() && observed.profile == saved
            )
        } else {
            r#"{"supported":false,"available":false}"#.to_owned()
        };
        let geometry = self.stream_geometry_observation(id, deadline).ok();
        let saved_geometry = geometry.and_then(|_| self.stream_disk_geometry(id, deadline).ok());
        let geometry_json = if let Some(active) = geometry {
            let active_width =
                active.map_or_else(|| "null".to_owned(), |value| value.width.to_string());
            let active_height =
                active.map_or_else(|| "null".to_owned(), |value| value.height.to_string());
            let saved_width =
                saved_geometry.map_or_else(|| "null".to_owned(), |value| value.width.to_string());
            let saved_height =
                saved_geometry.map_or_else(|| "null".to_owned(), |value| value.height.to_string());
            let matches = active.is_some() && active == saved_geometry;
            format!(
                r#"{{"supported":true,"available":{},"saved_available":{},"profile":"dcs6100lhv2-a1-42m-22m-v1","active_width":{active_width},"active_height":{active_height},"saved_width":{saved_width},"saved_height":{saved_height},"matches_saved":{matches},"pending_restart":{}}}"#,
                active.is_some(),
                saved_geometry.is_some(),
                active.is_some() && saved_geometry.is_some() && !matches
            )
        } else {
            r#"{"supported":false,"available":false}"#.to_owned()
        };
        let rc_config = self.stream_rc_config_observation(id, deadline).ok();
        let rc_config_json = if let Some(observed) = rc_config {
            let saved = if observed.supported {
                self.stream_disk_rc_config(id, deadline).ok().flatten()
            } else {
                None
            };
            let active_mode = observed
                .active_mode
                .map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let active_qp = observed
                .active_qp
                .map_or_else(|| "null".to_owned(), |value| value.to_string());
            let saved_mode = saved
                .map(|value| value.mode)
                .map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let saved_qp = saved
                .and_then(|value| value.qp)
                .map_or_else(|| "null".to_owned(), |value| value.to_string());
            let matches_saved = match (observed.active_mode, saved) {
                (Some("FIXQP"), Some(saved)) => {
                    saved.mode == "FIXQP" && observed.active_qp == saved.qp
                }
                (Some(active), Some(saved)) => active == saved.mode,
                _ => false,
            };
            format!(
                r#"{{"supported":{},"available":{},"active_mode":{active_mode},"active_qp":{active_qp},"saved_available":{},"saved_mode":{saved_mode},"saved_qp":{saved_qp},"matches_saved":{matches_saved},"pending_restart":{},"qp_min":{},"qp_max":{},"qp_default":35,"modes":["CBR","VBR","CAPPED_VBR","CAPPED_QUALITY","FIXQP"]}}"#,
                observed.supported,
                observed.active_mode.is_some(),
                saved.is_some(),
                observed.active_mode.is_some() && saved.is_some() && !matches_saved,
                observed.min,
                observed.max,
            )
        } else {
            r#"{"supported":false,"available":false}"#.to_owned()
        };
        /* Keep new optional capability probes last so older RVD builds and
         * bounded scripted consumers retain the established stream reads. */
        let gop_mode_observed = self.stream_gop_mode_observation(id, deadline).ok();
        let gop_mode_json = if let Some(observed) = gop_mode_observed {
            let saved_mode = if observed.supported {
                self.stream_disk_gop_mode(id, deadline).ok()
            } else {
                None
            };
            let active_mode = observed
                .active_mode
                .map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let saved_value =
                saved_mode.map_or_else(|| "null".to_owned(), |value| format!(r#""{value}""#));
            let matches_saved =
                observed.active_mode.is_some() && observed.active_mode == saved_mode;
            format!(
                r#"{{"supported":{},"available":{},"active_mode":{active_mode},"saved_available":{},"saved_mode":{saved_value},"matches_saved":{matches_saved},"pending_restart":{},"modes":["DEFAULT","PYRAMIDAL","SMARTP"]}}"#,
                observed.supported,
                observed.active_mode.is_some(),
                saved_mode.is_some(),
                observed.active_mode.is_some() && saved_mode.is_some() && !matches_saved,
            )
        } else {
            r#"{"supported":false,"available":false}"#.to_owned()
        };
        let buffer_json = match self.stream_buffer_observation(id, deadline) {
            Ok(observed) if observed.supported => {
                let saved = self.stream_disk_buffers(id, deadline).ok();
                let active = observed
                    .active
                    .map_or_else(|| "null".to_owned(), |value| value.to_string());
                let configured = observed
                    .configured
                    .map_or_else(|| "null".to_owned(), |value| value.to_string());
                let saved_value =
                    saved.map_or_else(|| "null".to_owned(), |value| value.to_string());
                let active_matches_configured =
                    observed.active.is_some() && observed.active == observed.configured;
                let configured_matches_saved =
                    observed.configured.is_some() && observed.configured == saved;
                let profile_admitted = observed.active == Some(1)
                    && observed.configured == Some(1)
                    && saved == Some(1);
                format!(
                    r#"{{"supported":true,"available":{},"editable":false,"active_buffers":{active},"configured_buffers":{configured},"saved_available":{},"saved_buffers":{saved_value},"active_matches_configured":{active_matches_configured},"configured_matches_saved":{configured_matches_saved},"profile":"dcs6100lhv2-a1-42m-22m-v1","profile_required_buffers":1,"profile_admitted":{profile_admitted},"hardware_limit_known":false}}"#,
                    observed.active.is_some(),
                    saved.is_some(),
                )
            }
            Ok(_) => r#"{"supported":false,"available":false,"editable":false}"#.to_owned(),
            Err(_) => r#"{"supported":null,"available":false,"editable":false}"#.to_owned(),
        };
        let enable_json = match self.stream_enable_observation(id, deadline) {
            Ok(enable) => {
                let saved = self.stream_disk_enabled(id, deadline).ok();
                let recorder = if id == 1 {
                    self.recording_observation(1, deadline).ok()
                } else {
                    None
                };
                let recorder_state_known = id == 0 || recorder.is_some();
                let recorder_active_blocks_disable = recorder.as_ref().is_some_and(|reply| {
                    reply.value.get_path("recording").and_then(Value::as_bool) != Some(false)
                        || reply.value.get_path("file_closed").and_then(Value::as_bool)
                            != Some(true)
                });
                let saved_value = saved.map_or("null".to_owned(), |value| value.to_string());
                let pending_restart = enable_pending_restart(saved, enable.active)
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "null".to_owned());
                format!(
                    r#"{{"supported":true,"available":true,"editable":{},"required":{},"active_enabled":{},"configured_enabled":{},"saved_available":{},"saved_enabled":{saved_value},"configured_matches_saved":{},"pending_restart":{pending_restart},"motion_blocks_disable":{},"recorder_blocks_disable":{},"recorder_state_known":{recorder_state_known},"recorder_active_blocks_disable":{recorder_active_blocks_disable},"apply":"full-camera-restart"}}"#,
                    enable.editable,
                    enable.required,
                    enable.active,
                    enable.configured,
                    saved.is_some(),
                    saved == Some(enable.configured),
                    enable.motion_blocks_disable,
                    enable.recorder_blocks_disable,
                )
            }
            Err(_) => r#"{"supported":null,"available":false,"editable":false}"#.to_owned(),
        };
        let audio_json = match self.stream_audio_observation(id, deadline) {
            Ok(audio) => {
                let disk = self.stream_disk_audio_enabled(id, false, deadline);
                let disk_available = disk.is_ok();
                let saved = disk.ok().flatten();
                let saved_value = saved.map_or("null".to_owned(), |value| value.to_string());
                let configured_value = audio
                    .configured
                    .map_or("null".to_owned(), |value| value.to_string());
                let active = audio.active_enabled();
                let active_value = active.map_or("null".to_owned(), |value| value.to_string());
                let configured_matches_saved = saved
                    .zip(audio.configured)
                    .map(|(saved, configured)| saved == configured)
                    .map_or("null".to_owned(), |value| value.to_string());
                let selection_required = audio.selection_required();
                let legacy_selection_pending = audio.legacy_selection_pending();
                let pending_restart = if legacy_selection_pending {
                    "true".to_owned()
                } else {
                    saved
                        .zip(active)
                        .map(|(saved, active)| saved != active)
                        .map_or("null".to_owned(), |value| value.to_string())
                };
                let rmr_required = audio.rmr.is_some();
                let rmr_active = audio
                    .rmr
                    .map(|rmr| rmr.active.to_string())
                    .unwrap_or_else(|| "null".to_owned());
                let rmr_source = audio
                    .rmr
                    .map(|rmr| rmr.source_available.to_string())
                    .unwrap_or_else(|| "null".to_owned());
                format!(
                    r#"{{"supported":true,"available":true,"editable":{},"apply":"full-camera-restart","selection_required":{selection_required},"legacy_selection_pending":{legacy_selection_pending},"configured_enabled":{configured_value},"saved_readback_available":{disk_available},"saved_available":{},"saved_enabled":{saved_value},"configured_matches_saved":{configured_matches_saved},"active_available":{},"active_enabled":{active_value},"pending_restart":{pending_restart},"rsd_active_enabled":{},"rmr_required":{rmr_required},"rmr_active_enabled":{rmr_active},"rsd_source_available":{},"rmr_source_available":{rmr_source}}}"#,
                    disk_available
                        && (selection_required
                            || legacy_selection_pending
                            || (saved.is_some() && active.is_some())),
                    saved.is_some(),
                    active.is_some(),
                    audio.rsd.active,
                    audio.rsd.source_available,
                )
            }
            Err(_) => r#"{"supported":null,"available":false,"editable":false}"#.to_owned(),
        };
        let number =
            |value: Option<u64>| value.map_or_else(|| "null".to_owned(), |v| v.to_string());
        Ok(BackendResponse::json(format!(r#"{{"source":"raptor","persistent":true,"stream_id":{id},"supported":{},"available":{},"gop":{},"saved_gop":{},"matches_saved":{},"gop_mode_control":{gop_mode_json},"fps_control":{fps},"encoding_control":{encoding_json},"rc_config_control":{rc_config_json},"codec_control":{codec_json},"profile_control":{profile_json},"geometry_control":{geometry_json},"buffer_control":{buffer_json},"enable_control":{enable_json},"audio_control":{audio_json}}}"#,
            observed.supported, observed.value.is_some(), number(observed.value), number(saved),
            observed.value.is_some() && observed.value == saved).into_bytes()))
    }

    pub(super) fn update_streams(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let _mutation = self.lock_mutation()?;
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        if ["stream0.audio_enabled", "stream1.audio_enabled"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_audio_inclusion(&request, deadline);
        }
        if ["stream0.enabled", "stream1.enabled"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_enabled(&request, deadline);
        }
        if ["stream0.fps", "stream1.fps"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_fps(&request, deadline);
        }
        if [
            "stream0.width",
            "stream0.height",
            "stream1.width",
            "stream1.height",
        ]
        .iter()
        .any(|path| request.get_path(path).is_some())
        {
            return self.update_geometry(&request, deadline);
        }
        if ["stream0.format", "stream1.format"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_codec(&request, deadline);
        }
        if ["stream0.profile", "stream1.profile"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_profile(&request, deadline);
        }
        if ["stream0.qp_init", "stream1.qp_init"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_rc_config(&request, deadline);
        }
        if ["stream0.gop_mode", "stream1.gop_mode"]
            .iter()
            .any(|path| request.get_path(path).is_some())
        {
            return self.update_gop_mode(&request, deadline);
        }
        if [
            "stream0.mode",
            "stream0.bitrate",
            "stream1.mode",
            "stream1.bitrate",
        ]
        .iter()
        .any(|path| request.get_path(path).is_some())
        {
            return self.update_encoding(&request, deadline);
        }
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.is_empty() || fields.len() > 2 {
            return Err(BackendError::Protocol);
        }
        let mut updates = Vec::new();
        for (name, fields) in fields {
            let id = match name.as_str() {
                "stream0" => 0,
                "stream1" => 1,
                _ => return Err(BackendError::Protocol),
            };
            let fields = fields.as_object().ok_or(BackendError::Protocol)?;
            if fields.len() != 1 {
                return Err(BackendError::Protocol);
            }
            let value = value_u64(fields.get("gop").ok_or(BackendError::Protocol)?)
                .map_err(|_| BackendError::Protocol)?;
            if !(1..=65535).contains(&value) {
                return Err(BackendError::Protocol);
            }
            updates.push((id, value));
        }
        for (id, _) in &updates {
            if self.stream_gop_observation(*id, deadline)?.value.is_none() {
                return Err(BackendError::Unavailable);
            }
        }
        let apply = || -> Result<(), BackendError> {
            for (id, value) in &updates {
                let command =
                    format!(r#"{{"cmd":"set-stream-gop","stream_id":{id},"gop":{value}}}"#);
                require_ok(
                    &self
                        .command(RaptorDaemon::Rvd, command.as_bytes(), deadline)
                        .map_err(upstream)?,
                )?;
                if self.stream_gop_observation(*id, deadline)?.value != Some(*value) {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            for (id, value) in &updates {
                if self.stream_disk_gop(*id, deadline)? != Some(*value)
                    || self.stream_gop_observation(*id, deadline)?.value != Some(*value)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Stream GOP may have changed, but apply, save or readback was incomplete. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::thread;

    fn observation(id: u64, value: &str) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-startup-gop","stream_id":{id},"supported":true,"available":{},"gop":{value}}}"#,
            value != "null"
        )
    }
    fn enable_observation(active: bool, configured: bool) -> String {
        stream_enable_reply(1, active, configured)
    }
    fn stream_enable_reply(id: u64, active: bool, configured: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":{id},"supported":true,"editable":{},"required":{},"active_enabled":{active},"configured_enabled":{configured},"pending_restart":{},"motion_blocks_disable":false,"recorder_blocks_disable":false}}"#,
            id == 1,
            id == 0,
            active != configured,
        )
    }
    fn run_script(
        script: Vec<(String, String)>,
        write: bool,
    ) -> Result<BackendResponse, BackendError> {
        run_request(script, write.then_some(br#"{"stream0":{"gop":60}}"#))
    }
    fn run_request(
        script: Vec<(String, String)>,
        body: Option<&[u8]>,
    ) -> Result<BackendResponse, BackendError> {
        let root = task_temp("stream-gop");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        let worker = thread::spawn(move || {
            for (request, reply) in script {
                let end = Instant::now() + Duration::from_secs(3);
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == io::ErrorKind::WouldBlock
                                && Instant::now() < end =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing GOP request: {other:?}"),
                    }
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                assert_eq!(read_request(&mut socket), request.as_bytes());
                socket.write_all(&framed(reply.as_bytes())).unwrap();
            }
        });
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        let deadline = Instant::now() + Duration::from_secs(2);
        let result = if let Some(body) = body {
            b.update_streams(body, deadline)
        } else {
            b.stream_config(0, deadline)
        };
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
        result
    }
    fn read() -> String {
        r#"{"cmd":"get-stream-gop","stream_id":0}"#.to_owned()
    }

    fn audio_owner(
        owner: &str,
        id: u64,
        enabled: bool,
        source_available: bool,
        track_active: Option<bool>,
    ) -> String {
        let track = track_active
            .map(|active| format!(r#", "track_active":{active}"#))
            .unwrap_or_default();
        format!(
            r#"{{"status":"ok","owner":"{owner}","apply":"full-camera-restart","stream_id":{id},"supported":true,"startup_explicit":true,"active_enabled":{enabled},"source_available":{source_available}{track}}}"#
        )
    }

    fn legacy_audio_owner(
        owner: &str,
        id: u64,
        enabled: bool,
        source_available: bool,
        track_active: Option<bool>,
    ) -> String {
        audio_owner(owner, id, enabled, source_available, track_active)
            .replace(r#""startup_explicit":true"#, r#""startup_explicit":false"#)
    }

    fn daemon_sequence(
        root: &std::path::Path,
        socket: &str,
        script: Vec<(String, String)>,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join(socket)).unwrap();
        thread::spawn(move || {
            for (request, response) in script {
                let (mut stream, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut stream), request.as_bytes());
                stream.write_all(&framed(response.as_bytes())).unwrap();
            }
        })
    }

    fn audio_config_reply(id: u64, enabled: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":{id},"configured_explicit":true,"configured_enabled":{enabled}}}"#
        )
    }

    fn legacy_audio_config_reply(id: u64) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":{id},"configured_explicit":false,"configured_enabled":null}}"#
        )
    }

    fn audio_disk_reply(id: u64, enabled: bool) -> String {
        format!(
            r#"{{"status":"ok","section":"stream{id}","keys":{{"audio_enabled":"{enabled}"}}}}"#
        )
    }

    fn rad_audio_sequence(root: &std::path::Path) -> thread::JoinHandle<()> {
        use super::super::audio::tests::{alc, input_state, levels};
        daemon_sequence(
            root,
            "rad.sock",
            vec![
                (
                    r#"{"cmd":"get-audio-observation-levels"}"#.into(),
                    String::from_utf8(levels(20)).unwrap(),
                ),
                (
                    r#"{"cmd":"get-input-state"}"#.into(),
                    String::from_utf8(input_state(true, "l16", true)).unwrap(),
                ),
                (
                    r#"{"cmd":"get-alc-gain"}"#.into(),
                    String::from_utf8(alc(2)).unwrap(),
                ),
            ],
        )
    }

    #[test]
    fn stream_audio_enable_checks_persistence_and_ignores_transient_source_state() {
        let root = task_temp("stream-audio-save");
        let get = |id| format!(r#"{{"cmd":"get-stream-audio-config","stream_id":{id}}}"#);
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (get(0), legacy_audio_config_reply(0)),
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":0}"#.into(),
                    stream_enable_reply(0, true, true),
                ),
                (
                    r#"{"cmd":"set-stream-audio-config","stream_id":0,"enabled":true}"#.into(),
                    audio_config_reply(0, true),
                ),
                (
                    r#"{"cmd":"config-save"}"#.into(),
                    r#"{"status":"ok"}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.into(),
                    audio_disk_reply(0, true),
                ),
                (get(0), audio_config_reply(0, true)),
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":0}"#.into(),
                    stream_enable_reply(0, true, true),
                ),
            ],
        );
        let rsd = daemon_sequence(
            &root,
            "rsd.sock",
            vec![
                (
                    r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                    legacy_audio_owner("rsd", 0, true, false, None),
                ),
                (
                    r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                    legacy_audio_owner("rsd", 0, true, true, None),
                ),
            ],
        );
        let rmr0 = daemon_sequence(
            &root,
            "rmr0.sock",
            vec![
                (
                    r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                    legacy_audio_owner("rmr0", 0, false, false, Some(false)),
                ),
                (
                    r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                    legacy_audio_owner("rmr0", 0, false, true, Some(false)),
                ),
            ],
        );
        let rad = rad_audio_sequence(&root);
        let result = backend(&root, "127.0.0.1:9".parse().unwrap())
            .update_streams(
                br#"{"stream0":{"audio_enabled":true}}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        assert_eq!(
            crate::json::parse(&result.body)
                .unwrap()
                .get_path("pending_restart"),
            Some(&Value::Bool(true))
        );
        for worker in [rvd, rsd, rmr0, rad] {
            worker.join().unwrap();
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cold_disabled_substream_audio_policy_does_not_require_rmr1() {
        let root = task_temp("stream-audio-cold-sub");
        let get = r#"{"cmd":"get-stream-audio-config","stream_id":1}"#.to_owned();
        let enabled = r#"{"cmd":"get-stream-enabled","stream_id":1}"#.to_owned();
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (get.clone(), audio_config_reply(1, false)),
                (enabled.clone(), stream_enable_reply(1, false, false)),
                (
                    r#"{"cmd":"set-stream-audio-config","stream_id":1,"enabled":false}"#.into(),
                    audio_config_reply(1, false),
                ),
                (
                    r#"{"cmd":"config-save"}"#.into(),
                    r#"{"status":"ok"}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream1"}"#.into(),
                    audio_disk_reply(1, false),
                ),
                (get, audio_config_reply(1, false)),
                (enabled, stream_enable_reply(1, false, false)),
            ],
        );
        let rsd = daemon_sequence(
            &root,
            "rsd.sock",
            vec![
                (
                    r#"{"cmd":"get-stream-audio-policy","stream_id":1}"#.into(),
                    audio_owner("rsd", 1, false, false, None),
                ),
                (
                    r#"{"cmd":"get-stream-audio-policy","stream_id":1}"#.into(),
                    audio_owner("rsd", 1, false, true, None),
                ),
            ],
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_streams(
            br#"{"stream1":{"audio_enabled":false}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        rvd.join().unwrap();
        rsd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn post_restart_matching_audio_owners_report_no_pending_restart() {
        let root = task_temp("stream-audio-post-restart");
        let get = r#"{"cmd":"get-stream-audio-config","stream_id":0}"#.to_owned();
        let enabled = r#"{"cmd":"get-stream-enabled","stream_id":0}"#.to_owned();
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (get.clone(), audio_config_reply(0, false)),
                (enabled.clone(), stream_enable_reply(0, true, true)),
                (
                    r#"{"cmd":"set-stream-audio-config","stream_id":0,"enabled":false}"#.into(),
                    audio_config_reply(0, false),
                ),
                (
                    r#"{"cmd":"config-save"}"#.into(),
                    r#"{"status":"ok"}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.into(),
                    audio_disk_reply(0, false),
                ),
                (get, audio_config_reply(0, false)),
                (enabled, stream_enable_reply(0, true, true)),
            ],
        );
        let owner_request = r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.to_owned();
        let rsd = daemon_sequence(
            &root,
            "rsd.sock",
            vec![
                (
                    owner_request.clone(),
                    audio_owner("rsd", 0, false, true, None),
                );
                2
            ],
        );
        let rmr = daemon_sequence(
            &root,
            "rmr0.sock",
            vec![
                (
                    owner_request,
                    audio_owner("rmr0", 0, false, true, Some(false)),
                );
                2
            ],
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap())
            .update_streams(
                br#"{"stream0":{"audio_enabled":false}}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        assert_eq!(
            crate::json::parse(&result.body)
                .unwrap()
                .get_path("pending_restart"),
            Some(&Value::Bool(false))
        );
        for worker in [rvd, rsd, rmr] {
            worker.join().unwrap();
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn disagreeing_required_audio_owners_reject_save() {
        let root = task_temp("stream-audio-disagree");
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (
                    r#"{"cmd":"get-stream-audio-config","stream_id":0}"#.into(),
                    audio_config_reply(0, false),
                ),
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":0}"#.into(),
                    stream_enable_reply(0, true, true),
                ),
            ],
        );
        let rsd = daemon_sequence(
            &root,
            "rsd.sock",
            vec![(
                r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                audio_owner("rsd", 0, false, true, None),
            )],
        );
        let rmr = daemon_sequence(
            &root,
            "rmr0.sock",
            vec![(
                r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                audio_owner("rmr0", 0, true, true, Some(true)),
            )],
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_streams(
            br#"{"stream0":{"audio_enabled":false}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(matches!(result, Err(BackendError::Unavailable)));
        for worker in [rvd, rsd, rmr] {
            worker.join().unwrap();
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn required_audio_owner_missing_rejects_save() {
        let root = task_temp("stream-audio-missing-owner");
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (
                    r#"{"cmd":"get-stream-audio-config","stream_id":0}"#.into(),
                    audio_config_reply(0, false),
                ),
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":0}"#.into(),
                    stream_enable_reply(0, true, true),
                ),
            ],
        );
        let rsd = daemon_sequence(
            &root,
            "rsd.sock",
            vec![(
                r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                audio_owner("rsd", 0, false, true, None),
            )],
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_streams(
            br#"{"stream0":{"audio_enabled":false}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_err());
        rvd.join().unwrap();
        rsd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn audio_disk_readback_failure_requires_explicit_retry() {
        let root = task_temp("stream-audio-retry");
        let get = r#"{"cmd":"get-stream-audio-config","stream_id":0}"#.to_owned();
        let enabled = r#"{"cmd":"get-stream-enabled","stream_id":0}"#.to_owned();
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (get.clone(), legacy_audio_config_reply(0)),
                (enabled.clone(), stream_enable_reply(0, true, true)),
                (
                    r#"{"cmd":"set-stream-audio-config","stream_id":0,"enabled":false}"#.into(),
                    audio_config_reply(0, false),
                ),
                (
                    r#"{"cmd":"config-save"}"#.into(),
                    r#"{"status":"ok"}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.into(),
                    r#"{"status":"ok","section":"stream1","keys":{"audio_enabled":"false"}}"#
                        .into(),
                ),
                (get.clone(), audio_config_reply(0, false)),
                (enabled.clone(), stream_enable_reply(0, true, true)),
                (
                    r#"{"cmd":"set-stream-audio-config","stream_id":0,"enabled":false}"#.into(),
                    audio_config_reply(0, false),
                ),
                (
                    r#"{"cmd":"config-save"}"#.into(),
                    r#"{"status":"ok"}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.into(),
                    audio_disk_reply(0, false),
                ),
                (get, audio_config_reply(0, false)),
                (enabled, stream_enable_reply(0, true, true)),
            ],
        );
        let owners = vec![
            (
                r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                legacy_audio_owner("rsd", 0, true, true, None),
            );
            3
        ];
        let recorders = vec![
            (
                r#"{"cmd":"get-stream-audio-policy","stream_id":0}"#.into(),
                legacy_audio_owner("rmr0", 0, false, true, Some(false)),
            );
            3
        ];
        let rsd = daemon_sequence(&root, "rsd.sock", owners);
        let rmr = daemon_sequence(&root, "rmr0.sock", recorders);
        let control = backend(&root, "127.0.0.1:9".parse().unwrap());
        let body = br#"{"stream0":{"audio_enabled":false}}"#;
        assert!(matches!(
            control.update_streams(body, Instant::now() + Duration::from_secs(2)),
            Err(BackendError::PartialApply(_))
        ));
        assert!(
            control
                .update_streams(body, Instant::now() + Duration::from_secs(2))
                .is_ok()
        );
        for worker in [rvd, rsd, rmr] {
            worker.join().unwrap();
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn audio_post_save_readback_requires_matching_section_and_explicit_key() {
        let root = task_temp("stream-audio-disk-identity");
        let request = r#"{"cmd":"config-read-section","section":"stream0"}"#.to_owned();
        let rvd = daemon_sequence(
            &root,
            "rvd.sock",
            vec![
                (
                    request.clone(),
                    r#"{"status":"ok","section":"stream1","keys":{"audio_enabled":"false"}}"#
                        .into(),
                ),
                (
                    request,
                    r#"{"status":"ok","section":"stream0","keys":{}}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.into(),
                    r#"{"status":"ok","section":"stream0","keys":{}}"#.into(),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.into(),
                    r#"{"status":"ok","section":"stream0","keys":{"audio_enabled":17}}"#.into(),
                ),
            ],
        );
        let control = backend(&root, "127.0.0.1:9".parse().unwrap());
        for _ in 0..2 {
            assert!(matches!(
                control.stream_disk_audio_enabled(0, true, Instant::now() + Duration::from_secs(2)),
                Err(BackendError::Upstream(502))
            ));
        }
        assert_eq!(
            control
                .stream_disk_audio_enabled(0, false, Instant::now() + Duration::from_secs(2))
                .unwrap(),
            None
        );
        assert!(matches!(
            control.stream_disk_audio_enabled(0, false, Instant::now() + Duration::from_secs(2)),
            Err(BackendError::Upstream(502))
        ));
        rvd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn substream_disable_requires_idle_recorder_and_checked_disk_readback() {
        let root = task_temp("stream-enable-save");
        let recorder = super::super::recorder::tests::sequence(
            &root,
            1,
            vec![(
                super::super::recorder::tests::get(),
                super::super::recorder::tests::state(1, Some(false), true, true, None),
            )],
        );
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let worker = thread::spawn(move || {
            let script = [
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":1}"#,
                    enable_observation(true, true),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream1"}"#,
                    r#"{"status":"ok","section":"stream1","keys":{"enabled":"true"}}"#.to_owned(),
                ),
                (
                    r#"{"cmd":"set-stream-enabled-config","stream_id":1,"enabled":false}"#,
                    enable_observation(true, false),
                ),
                (r#"{"cmd":"config-save"}"#, r#"{"status":"ok"}"#.to_owned()),
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":1}"#,
                    enable_observation(true, false),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream1"}"#,
                    r#"{"status":"ok","section":"stream1","keys":{"enabled":"false"}}"#.to_owned(),
                ),
            ];
            for (request, response) in script {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request.as_bytes());
                socket.write_all(&framed(response.as_bytes())).unwrap();
            }
        });
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        let result = backend
            .update_streams(
                br#"{"stream1":{"enabled":false}}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        let reply = crate::json::parse(&result.body).unwrap();
        assert_eq!(reply.get_path("pending_restart"), Some(&Value::Bool(true)));
        worker.join().unwrap();
        recorder.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    fn run_enable_save(
        active: bool,
        configured: bool,
        saved: bool,
        requested: bool,
    ) -> BackendResponse {
        let root = task_temp("stream-enable-retry");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let worker = thread::spawn(move || {
            let script = [
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":1}"#.to_owned(),
                    enable_observation(active, configured),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream1"}"#.to_owned(),
                    format!(
                        r#"{{"status":"ok","section":"stream1","keys":{{"enabled":"{saved}"}}}}"#
                    ),
                ),
                (
                    format!(
                        r#"{{"cmd":"set-stream-enabled-config","stream_id":1,"enabled":{requested}}}"#
                    ),
                    enable_observation(active, requested),
                ),
                (
                    r#"{"cmd":"config-save"}"#.to_owned(),
                    r#"{"status":"ok"}"#.to_owned(),
                ),
                (
                    r#"{"cmd":"get-stream-enabled","stream_id":1}"#.to_owned(),
                    enable_observation(active, requested),
                ),
                (
                    r#"{"cmd":"config-read-section","section":"stream1"}"#.to_owned(),
                    format!(
                        r#"{{"status":"ok","section":"stream1","keys":{{"enabled":"{requested}"}}}}"#
                    ),
                ),
            ];
            for (request, response) in script {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request.as_bytes());
                socket.write_all(&framed(response.as_bytes())).unwrap();
            }
        });
        let recorder = (!requested).then(|| {
            super::super::recorder::tests::sequence(
                &root,
                1,
                vec![(
                    super::super::recorder::tests::get(),
                    super::super::recorder::tests::state(1, Some(false), true, true, None),
                )],
            )
        });
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        let body = format!(r#"{{"stream1":{{"enabled":{requested}}}}}"#);
        let result = backend
            .update_streams(body.as_bytes(), Instant::now() + Duration::from_secs(2))
            .unwrap();
        worker.join().unwrap();
        if let Some(recorder) = recorder {
            recorder.join().unwrap();
        }
        fs::remove_dir_all(root).unwrap();
        result
    }

    #[test]
    fn substream_enable_retry_and_missing_recorder_owner_are_supported() {
        let retry = run_enable_save(true, false, true, false);
        assert!(
            retry
                .body
                .windows(22)
                .any(|part| part == b"\"pending_restart\":true")
        );

        let rollback = run_enable_save(true, false, true, true);
        assert!(
            rollback
                .body
                .windows(23)
                .any(|part| part == b"\"pending_restart\":false")
        );

        let enable = run_enable_save(false, false, false, true);
        assert!(
            enable
                .body
                .windows(22)
                .any(|part| part == b"\"pending_restart\":true")
        );
    }

    #[test]
    fn missing_saved_enable_state_has_unknown_restart_status() {
        assert_eq!(enable_pending_restart(None, true), None);
        assert_eq!(enable_pending_restart(Some(true), true), Some(false));
        assert_eq!(enable_pending_restart(Some(false), true), Some(true));
    }

    fn buffer_reply(active: &str, configured: &str, matches: bool, admitted: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"read-only-config-and-sdk","stream_id":0,"supported":true,"available":{},"editable":false,"profile":"dcs6100lhv2-a1-42m-22m-v1","profile_required_buffers":1,"hardware_limit_known":false,"active_buffers":{active},"configured_buffers":{configured},"matches_configured":{matches},"profile_admitted":{admitted}}}"#,
            active != "null"
        )
    }

    fn observe_buffers(
        reply: String,
        expect_disk: bool,
    ) -> Result<(BufferObservation, u64), BackendError> {
        let root = task_temp("stream-buffers");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let worker = thread::spawn(move || {
            let mut script = vec![(
                r#"{"cmd":"get-stream-buffer-status","stream_id":0}"#.to_owned(),
                reply,
            )];
            if expect_disk {
                script.push((
                    r#"{"cmd":"config-read-section","section":"stream0"}"#.to_owned(),
                    r#"{"status":"ok","section":"stream0","keys":{"nr_vbs":"1"}}"#.to_owned(),
                ));
            }
            for (request, response) in script {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request.as_bytes());
                socket.write_all(&framed(response.as_bytes())).unwrap();
            }
        });
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        let deadline = Instant::now() + Duration::from_secs(2);
        let result = b
            .stream_buffer_observation(0, deadline)
            .and_then(|observed| {
                b.stream_disk_buffers(0, deadline)
                    .map(|saved| (observed, saved))
            });
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
        result
    }

    #[test]
    fn buffer_status_keeps_sdk_config_and_disk_values_distinct() {
        let (observed, saved) = observe_buffers(buffer_reply("1", "1", true, true), true).unwrap();
        assert_eq!(
            observed,
            BufferObservation {
                supported: true,
                active: Some(1),
                configured: Some(1),
            }
        );
        assert_eq!(saved, 1);
        assert!(observe_buffers(buffer_reply("3", "1", true, false), false).is_err());
        assert!(observe_buffers(buffer_reply("3", "1", false, false), true).is_ok());
    }

    #[test]
    fn buffer_status_keeps_malformed_and_transport_failure_unknown() {
        let root = task_temp("stream-buffer-invalid");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(
                read_request(&mut socket),
                br#"{"cmd":"get-stream-buffer-status","stream_id":0}"#
            );
            socket
                .write_all(&framed(br#"{"status":"ok","supported":true}"#))
                .unwrap();
        });
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        let deadline = Instant::now() + Duration::from_secs(2);
        assert!(b.stream_buffer_observation(0, deadline).is_err());
        worker.join().unwrap();
        fs::remove_dir_all(&root).unwrap();

        let missing = task_temp("stream-buffer-missing");
        let b = backend(&missing, "127.0.0.1:9".parse().unwrap());
        assert!(
            b.stream_buffer_observation(0, Instant::now() + Duration::from_secs(2))
                .is_err()
        );
        fs::remove_dir_all(missing).unwrap();
    }

    fn disk() -> (String, String) {
        (
            r#"{"cmd":"config-read-section","section":"stream0"}"#.to_owned(),
            r#"{"status":"ok","section":"stream0","keys":{"gop":"60"}}"#.to_owned(),
        )
    }
    fn save_script() -> Vec<(String, String)> {
        vec![
            (read(), observation(0, "60")),
            (
                r#"{"cmd":"set-stream-gop","stream_id":0,"gop":60}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (read(), observation(0, "60")),
            (
                r#"{"cmd":"config-save"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            disk(),
            (read(), observation(0, "60")),
        ]
    }
    #[test]
    fn gop_save_and_unchanged_retry_require_live_disk_and_final_reads() {
        assert!(run_script(save_script(), true).is_ok());
        assert!(run_script(vec![(read(), observation(0, "60")), disk()], false).is_ok());
    }
    #[test]
    fn gop_every_post_mutation_failure_is_partial_apply() {
        for failure in 1..6 {
            let mut script = save_script();
            script[failure].1 = r#"{"status":"error"}"#.to_owned();
            script.truncate(failure + 1);
            assert!(matches!(
                run_script(script, true),
                Err(BackendError::PartialApply(_))
            ));
        }
        for (index, response) in [
            (2, observation(0, "59")),
            (2, observation(1, "60")),
            (2, observation(0, "null")),
            (
                4,
                r#"{"status":"ok","section":"stream0","keys":{"gop":"59"}}"#.to_owned(),
            ),
            (5, observation(0, "59")),
        ] {
            let mut script = save_script();
            script[index].1 = response;
            script.truncate(index + 1);
            assert!(matches!(
                run_script(script, true),
                Err(BackendError::PartialApply(_))
            ));
        }
    }
    #[test]
    fn gop_unavailable_preflight_never_mutates() {
        assert!(matches!(
            run_script(vec![(read(), observation(0, "null"))], true),
            Err(BackendError::Unavailable)
        ));
        assert!(run_script(vec![(read(), observation(1, "30"))], true).is_err());
        assert!(run_script(vec![(read(), observation(0, "65536"))], true).is_err());
    }
    #[test]
    fn gop_rejects_other_fields_before_ipc() {
        let root = task_temp("stream-gop-invalid");
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            r#"{}"#,
            r#"{"stream0":{"gop":0}}"#,
            r#"{"stream0":{"gop":65536}}"#,
            r#"{"stream0":{"gop":1.5}}"#,
            r#"{"stream0":{"gop":true}}"#,
            r#"{"stream0":{"max_gop":30}}"#,
            r#"{"stream0":{"gop":30,"bitrate":1000}}"#,
            r#"{"stream0":{"gop":30},"motion":{"enabled":true}}"#,
        ] {
            assert!(matches!(
                b.update_streams(body.as_bytes(), Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }

    fn encoding_observation(id: u64, mode: &str, bitrate: u64) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-live-encoding","stream_id":{id},"supported":true,"available":true,"rc_mode":"{mode}","bitrate":{bitrate},"bitrate_min":1000,"bitrate_max":100000000,"bitrate_step":1000}}"#
        )
    }

    fn encoding_read(id: u64) -> String {
        format!(r#"{{"cmd":"get-stream-encoding","stream_id":{id}}}"#)
    }

    fn encoding_disk(id: u64, mode: &str, bitrate: u64) -> (String, String) {
        (
            format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#),
            format!(
                r#"{{"status":"ok","section":"stream{id}","keys":{{"rc_mode":"{mode}","bitrate":"{bitrate}"}}}}"#
            ),
        )
    }

    fn rc_config_read(id: u64) -> String {
        format!(r#"{{"cmd":"get-stream-rc-config","stream_id":{id}}}"#)
    }

    fn rc_config_observation(id: u64, mode: &str, qp: Option<u64>) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":{id},"supported":true,"available":true,"active_mode":"{mode}","active_qp":{},"qp_min":0,"qp_max":51}}"#,
            qp.map_or_else(|| "null".to_owned(), |value| value.to_string())
        )
    }

    fn rc_config_disk(id: u64, mode: &str, qp: u64, bitrate: u64) -> (String, String) {
        (
            format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#),
            format!(
                r#"{{"status":"ok","section":"stream{id}","keys":{{"rc_mode":"{mode}","init_qp":"{qp}","bitrate":"{bitrate}"}}}}"#
            ),
        )
    }

    fn bitrate_disk(id: u64, bitrate: u64) -> (String, String) {
        (
            format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#),
            format!(r#"{{"status":"ok","section":"stream{id}","keys":{{"bitrate":"{bitrate}"}}}}"#),
        )
    }

    fn fixqp_save_script() -> Vec<(String, String)> {
        vec![
            (rc_config_read(0), rc_config_observation(0, "cbr", None)),
            bitrate_disk(0, 2_000_000),
            (
                r#"{"cmd":"set-stream-rc-config","stream_id":0,"rc_mode":"fixqp","initial_qp":37}"#.to_owned(),
                r#"{"status":"ok","persistence":"checked-next-full-stack-restart","pending_restart":true}"#.to_owned(),
            ),
            (
                r#"{"cmd":"config-save"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            rc_config_disk(0, "fixqp", 37, 2_000_000),
            bitrate_disk(0, 2_000_000),
            (rc_config_read(0), rc_config_observation(0, "cbr", None)),
        ]
    }

    fn encoding_save_script() -> Vec<(String, String)> {
        vec![
            (encoding_read(0), encoding_observation(0, "cbr", 2_000_000)),
            (
                r#"{"cmd":"set-stream-encoding","stream_id":0,"rc_mode":"vbr","bitrate":3000000}"#
                    .to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (encoding_read(0), encoding_observation(0, "vbr", 3_000_000)),
            (
                r#"{"cmd":"config-save"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            encoding_disk(0, "vbr", 3_000_000),
            (encoding_read(0), encoding_observation(0, "vbr", 3_000_000)),
        ]
    }

    #[test]
    fn encoding_save_requires_live_apply_disk_and_final_readback() {
        let result = run_request(
            encoding_save_script(),
            Some(br#"{"stream0":{"mode":"VBR","bitrate":3000000}}"#),
        )
        .unwrap();
        let response = crate::json::parse(&result.body).unwrap();
        assert_eq!(
            response.get_path("status").and_then(Value::as_str),
            Some("accepted")
        );
        assert_eq!(
            response.get_path("persistent").and_then(Value::as_bool),
            Some(true)
        );
    }

    #[test]
    fn encoding_two_stream_save_preflights_both_before_mutation() {
        let script = vec![
            (encoding_read(0), encoding_observation(0, "cbr", 2_000_000)),
            (encoding_read(1), encoding_observation(1, "vbr", 1_000_000)),
            (
                r#"{"cmd":"set-stream-encoding","stream_id":0,"rc_mode":"vbr","bitrate":3000000}"#
                    .to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (encoding_read(0), encoding_observation(0, "vbr", 3_000_000)),
            (
                r#"{"cmd":"set-stream-encoding","stream_id":1,"rc_mode":"cbr","bitrate":1500000}"#
                    .to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (encoding_read(1), encoding_observation(1, "cbr", 1_500_000)),
            (
                r#"{"cmd":"config-save"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            encoding_disk(0, "vbr", 3_000_000),
            (encoding_read(0), encoding_observation(0, "vbr", 3_000_000)),
            encoding_disk(1, "cbr", 1_500_000),
            (encoding_read(1), encoding_observation(1, "cbr", 1_500_000)),
        ];
        assert!(run_request(
            script,
            Some(
                br#"{"stream0":{"mode":"VBR","bitrate":3000000},"stream1":{"mode":"CBR","bitrate":1500000}}"#
            ),
        )
        .is_ok());
    }

    #[test]
    fn encoding_rejects_malformed_native_observations_before_mutation() {
        for reply in [
            encoding_observation(1, "cbr", 2_000_000),
            encoding_observation(0, "CBR", 2_000_000),
            encoding_observation(0, "cbr", 2_000_001),
            encoding_observation(0, "fixqp", 2_000_000),
            encoding_observation(0, "cbr", 100_001_000),
            encoding_observation(0, "cbr", 999),
            encoding_observation(0, "cbr", 2_000_000)
                .replace(r#""bitrate_step":1000"#, r#""bitrate_step":1"#),
        ] {
            assert!(matches!(
                run_request(
                    vec![(encoding_read(0), reply)],
                    Some(br#"{"stream0":{"mode":"VBR","bitrate":3000000}}"#),
                ),
                Err(BackendError::Upstream(502))
            ));
        }
    }

    #[test]
    fn encoding_every_post_mutation_failure_is_partial_apply() {
        for failure in 1..6 {
            let mut script = encoding_save_script();
            script[failure].1 = r#"{"status":"error"}"#.to_owned();
            script.truncate(failure + 1);
            assert!(matches!(
                run_request(
                    script,
                    Some(br#"{"stream0":{"mode":"VBR","bitrate":3000000}}"#)
                ),
                Err(BackendError::PartialApply(_))
            ));
        }
        for (index, response) in [
            (2, encoding_observation(0, "vbr", 2_999_000)),
            (2, encoding_observation(0, "cbr", 3_000_000)),
            (4, encoding_disk(0, "vbr", 2_999_000).1),
            (5, encoding_observation(0, "cbr", 3_000_000)),
        ] {
            let mut script = encoding_save_script();
            script[index].1 = response;
            script.truncate(index + 1);
            assert!(matches!(
                run_request(
                    script,
                    Some(br#"{"stream0":{"mode":"VBR","bitrate":3000000}}"#)
                ),
                Err(BackendError::PartialApply(_))
            ));
        }
    }

    #[test]
    fn encoding_unavailable_preflight_and_invalid_input_never_mutate() {
        let unavailable = r#"{"status":"ok","persistence":"checked-live-encoding","stream_id":0,"supported":true,"available":false,"rc_mode":null,"bitrate":null,"bitrate_min":1000,"bitrate_max":100000000,"bitrate_step":1000}"#;
        assert!(matches!(
            run_request(
                vec![(encoding_read(0), unavailable.to_owned())],
                Some(br#"{"stream0":{"mode":"VBR","bitrate":3000000}}"#)
            ),
            Err(BackendError::Unavailable)
        ));
        let root = task_temp("stream-encoding-invalid");
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"stream0":{"mode":"FIXQP","bitrate":2000000}}"#.as_slice(),
            br#"{"stream0":{"mode":"vbr","bitrate":2000000}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR"}}"#.as_slice(),
            br#"{"stream0":{"bitrate":2000000}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR","bitrate":999}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR","bitrate":1001}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR","bitrate":100001000}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR","bitrate":2000.5}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR","bitrate":2000000,"gop":30}}"#.as_slice(),
            br#"{"stream0":{"mode":"VBR","bitrate":2000000},"stream1":{"gop":30}}"#.as_slice(),
        ] {
            assert!(matches!(
                b.update_streams(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fixqp_save_keeps_active_state_and_preserves_bitrate() {
        let response = run_request(
            fixqp_save_script(),
            Some(br#"{"stream0":{"mode":"FIXQP","qp_init":37}}"#),
        )
        .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("status").and_then(Value::as_str),
            Some("accepted")
        );
        assert_eq!(
            value.get_path("pending_restart").and_then(Value::as_bool),
            Some(true)
        );
    }

    #[test]
    fn fixqp_every_post_mutation_failure_is_partial_apply() {
        for failure in 2..7 {
            let mut script = fixqp_save_script();
            script[failure].1 = r#"{"status":"error"}"#.to_owned();
            script.truncate(failure + 1);
            assert!(matches!(
                run_request(
                    script,
                    Some(br#"{"stream0":{"mode":"FIXQP","qp_init":37}}"#)
                ),
                Err(BackendError::PartialApply(_))
            ));
        }
        for (index, response) in [
            (2, r#"{"status":"ok","persistence":"checked-next-full-stack-restart","pending_restart":false}"#.to_owned()),
            (4, rc_config_disk(0, "fixqp", 38, 2_000_000).1),
            (5, bitrate_disk(0, 3_000_000).1),
            (6, rc_config_observation(0, "fixqp", Some(37))),
        ] {
            let mut script = fixqp_save_script();
            script[index].1 = response;
            script.truncate(index + 1);
            assert!(matches!(
                run_request(
                    script,
                    Some(br#"{"stream0":{"mode":"FIXQP","qp_init":37}}"#)
                ),
                Err(BackendError::PartialApply(_))
            ));
        }
    }

    #[test]
    fn fixqp_invalid_or_rate_only_request_never_mutates() {
        let root = task_temp("stream-fixqp-invalid");
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"stream0":{"mode":"FIXQP","qp_init":52}}"#.as_slice(),
            br#"{"stream0":{"mode":"FIXQP","qp_init":-1}}"#.as_slice(),
            br#"{"stream0":{"mode":"FIXQP"}}"#.as_slice(),
            br#"{"stream0":{"qp_init":37}}"#.as_slice(),
            br#"{"stream0":{"mode":"SMART","qp_init":37}}"#.as_slice(),
            br#"{"stream0":{"mode":"FIXQP","qp_init":37,"bitrate":2000000}}"#.as_slice(),
        ] {
            assert!(matches!(
                b.update_streams(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();

        assert!(matches!(
            run_request(
                vec![(rc_config_read(0), rc_config_observation(0, "cbr", None))],
                Some(br#"{"stream0":{"mode":"VBR","qp_init":37}}"#),
            ),
            Err(BackendError::Unavailable)
        ));
    }

    #[test]
    fn encoding_get_exposes_live_saved_capability_without_enabling_other_fields() {
        let response = run_request(
            vec![
                (read(), observation(0, "60")),
                disk(),
                (
                    r#"{"cmd":"get-stream-fps","stream_id":0}"#.to_owned(),
                    fps_observation(0, "ok", "15", "true", "true", false, false),
                ),
                (encoding_read(0), encoding_observation(0, "vbr", 3_000_000)),
                encoding_disk(0, "vbr", 3_000_000),
            ],
            None,
        )
        .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value
                .get_path("encoding_control.rc_mode")
                .and_then(Value::as_str),
            Some("VBR")
        );
        assert_eq!(
            value
                .get_path("encoding_control.bitrate")
                .and_then(|value| value_u64(value).ok()),
            Some(3_000_000)
        );
        assert_eq!(
            value
                .get_path("encoding_control.matches_saved")
                .and_then(Value::as_bool),
            Some(true)
        );
    }

    fn codec_read(id: u64) -> String {
        format!(r#"{{"cmd":"get-stream-codec","stream_id":{id}}}"#)
    }

    fn codec_observation(id: u64, codec: &str, h265: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-restart-codec","stream_id":{id},"supported":true,"available":true,"recovery_required":false,"codecs":["h264"{}],"codec":"{codec}","width":1920,"height":1080}}"#,
            if h265 { r#", "h265""# } else { "" }
        )
    }

    fn codec_disk(id: u64, codec: &str) -> (String, String) {
        (
            format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#),
            format!(r#"{{"status":"ok","section":"stream{id}","keys":{{"codec":"{codec}"}}}}"#),
        )
    }

    fn codec_save_script() -> Vec<(String, String)> {
        vec![
            (codec_read(0), codec_observation(0, "h264", true)),
            (
                r#"{"cmd":"set-stream-codec","stream_id":0,"codec":"h265"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (codec_read(0), codec_observation(0, "h265", true)),
            (
                r#"{"cmd":"config-save"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            codec_disk(0, "h265"),
            (codec_read(0), codec_observation(0, "h265", true)),
        ]
    }

    fn profile_read(id: u64) -> String {
        format!(r#"{{"cmd":"get-stream-profile","stream_id":{id}}}"#)
    }

    fn profile_observation(id: u64, profile: Option<u64>, recovery: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-restart-profile","stream_id":{id},"supported":true,"available":{},"recovery_required":{recovery},"profile":{}}}"#,
            profile.is_some(),
            profile.map_or_else(|| "null".to_owned(), |value| value.to_string())
        )
    }

    fn profile_disk(id: u64, profile: u64) -> (String, String) {
        (
            format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#),
            format!(r#"{{"status":"ok","section":"stream{id}","keys":{{"profile":"{profile}"}}}}"#),
        )
    }

    fn profile_save_script() -> Vec<(String, String)> {
        vec![
            (profile_read(0), profile_observation(0, Some(2), false)),
            (
                r#"{"cmd":"set-stream-profile","stream_id":0,"profile":1}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (profile_read(0), profile_observation(0, Some(1), false)),
            (
                r#"{"cmd":"config-save"}"#.to_owned(),
                r#"{"status":"ok"}"#.to_owned(),
            ),
            profile_disk(0, 1),
            (profile_read(0), profile_observation(0, Some(1), false)),
        ]
    }

    #[test]
    fn profile_save_requires_restart_live_disk_and_final_readback() {
        assert!(run_request(profile_save_script(), Some(br#"{"stream0":{"profile":1}}"#)).is_ok());
    }

    #[test]
    fn profile_get_exposes_exact_live_saved_capability() {
        let response = run_request(
            vec![
                (read(), observation(0, "60")),
                disk(),
                (
                    r#"{"cmd":"get-stream-fps","stream_id":0}"#.to_owned(),
                    fps_observation(0, "ok", "15", "true", "true", false, false),
                ),
                (encoding_read(0), encoding_observation(0, "cbr", 2_000_000)),
                encoding_disk(0, "cbr", 2_000_000),
                (codec_read(0), codec_observation(0, "h264", true)),
                codec_disk(0, "h264"),
                (profile_read(0), profile_observation(0, Some(2), false)),
                profile_disk(0, 2),
            ],
            None,
        )
        .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value
                .get_path("profile_control.profile")
                .and_then(|value| value_u64(value).ok()),
            Some(2)
        );
        assert_eq!(
            value
                .get_path("profile_control.saved_profile")
                .and_then(|value| value_u64(value).ok()),
            Some(2)
        );
        assert_eq!(
            value
                .get_path("profile_control.matches_saved")
                .and_then(Value::as_bool),
            Some(true)
        );
    }

    #[test]
    fn profile_two_stream_save_preflights_before_first_restart() {
        let unsupported = r#"{"status":"ok","persistence":"checked-restart-profile","stream_id":1,"supported":false,"available":false,"recovery_required":false,"profile":null}"#;
        assert!(matches!(
            run_request(
                vec![
                    (profile_read(0), profile_observation(0, Some(2), false)),
                    (profile_read(1), unsupported.to_owned()),
                ],
                Some(br#"{"stream0":{"profile":1},"stream1":{"profile":0}}"#),
            ),
            Err(BackendError::Unavailable)
        ));
    }

    #[test]
    fn profile_post_restart_failures_are_partial_apply() {
        for failure in 1..6 {
            let mut script = profile_save_script();
            script[failure].1 = r#"{"status":"error"}"#.to_owned();
            script.truncate(failure + 1);
            assert!(matches!(
                run_request(script, Some(br#"{"stream0":{"profile":1}}"#)),
                Err(BackendError::PartialApply(_))
            ));
        }
        for (index, reply) in [
            (2, profile_observation(0, Some(2), false)),
            (4, profile_disk(0, 2).1),
            (5, profile_observation(0, Some(2), false)),
        ] {
            let mut script = profile_save_script();
            script[index].1 = reply;
            script.truncate(index + 1);
            assert!(matches!(
                run_request(script, Some(br#"{"stream0":{"profile":1}}"#)),
                Err(BackendError::PartialApply(_))
            ));
        }
    }

    #[test]
    fn profile_rejects_invalid_input_and_incoherent_observation_before_mutation() {
        let root = task_temp("stream-profile-invalid");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"stream0":{"profile":-1}}"#.as_slice(),
            br#"{"stream0":{"profile":3}}"#,
            br#"{"stream0":{"profile":1.5}}"#,
            br#"{"stream0":{"profile":"1"}}"#,
            br#"{"stream0":{"profile":1,"gop":30}}"#,
        ] {
            assert!(matches!(
                backend.update_streams(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
        for reply in [
            profile_observation(0, None, false),
            profile_observation(0, Some(3), false),
            profile_observation(0, Some(1), true),
            profile_observation(1, Some(1), false),
        ] {
            assert!(
                run_request(
                    vec![(profile_read(0), reply)],
                    Some(br#"{"stream0":{"profile":1}}"#)
                )
                .is_err()
            );
        }
    }

    #[test]
    fn codec_save_requires_restart_live_disk_and_final_readback() {
        assert!(
            run_request(
                codec_save_script(),
                Some(br#"{"stream0":{"format":"H265"}}"#)
            )
            .is_ok()
        );
    }

    #[test]
    fn codec_two_stream_save_preflights_before_first_restart() {
        let script = vec![
            (codec_read(0), codec_observation(0, "h264", true)),
            (codec_read(1), codec_observation(1, "h264", false)),
        ];
        assert!(matches!(
            run_request(
                script,
                Some(br#"{"stream0":{"format":"H265"},"stream1":{"format":"H265"}}"#)
            ),
            Err(BackendError::Unavailable)
        ));
    }

    #[test]
    fn codec_failures_after_restart_attempt_are_partial_apply() {
        for failure in 1..6 {
            let mut script = codec_save_script();
            script[failure].1 = r#"{"status":"error"}"#.to_owned();
            script.truncate(failure + 1);
            assert!(matches!(
                run_request(script, Some(br#"{"stream0":{"format":"H265"}}"#)),
                Err(BackendError::PartialApply(_))
            ));
        }
        let malformed = codec_observation(0, "h264", true)
            .replace(r#""codecs":["h264", "h265"]"#, r#""codecs":["h265"]"#);
        assert!(matches!(
            run_request(
                vec![(codec_read(0), malformed)],
                Some(br#"{"stream0":{"format":"H265"}}"#)
            ),
            Err(BackendError::Upstream(502))
        ));
    }

    #[test]
    fn codec_unsupported_native_reply_becomes_minimal_disabled_capability() {
        let unsupported = r#"{"status":"ok","persistence":"checked-restart-codec","stream_id":0,"supported":false,"available":false,"recovery_required":false,"codecs":[],"codec":null,"width":null,"height":null}"#;
        let response = run_request(
            vec![
                (read(), observation(0, "60")),
                disk(),
                (
                    r#"{"cmd":"get-stream-fps","stream_id":0}"#.to_owned(),
                    fps_observation(0, "ok", "15", "true", "true", false, false),
                ),
                (
                    encoding_read(0),
                    r#"{"status":"error","error":"unknown command"}"#.to_owned(),
                ),
                (codec_read(0), unsupported.to_owned()),
            ],
            None,
        )
        .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value
                .get_path("codec_control.supported")
                .and_then(Value::as_bool),
            Some(false)
        );
        assert_eq!(
            value
                .get_path("codec_control.available")
                .and_then(Value::as_bool),
            Some(false)
        );
        assert_eq!(
            value
                .get_path("codec_control")
                .unwrap()
                .as_object()
                .unwrap()
                .len(),
            2
        );
    }

    fn fps_observation(
        id: u64,
        status: &str,
        fps: &str,
        applied: &str,
        persisted: &str,
        pending: bool,
        recovery: bool,
    ) -> String {
        format!(
            r#"{{"status":"{status}","stream_id":{id},"recovery_required":{recovery},"persistence_pending":{pending},"live_applied":{applied},"persisted":{persisted},"fps":{fps},"evidence":"SDK configuration readback","monitoring":{{"related":true,"active":false,"paused":true,"receiving":false,"thread_owned":false}}}}"#
        )
    }

    #[test]
    fn fps_semantic_main_sub_single_command_and_exact_receipts() {
        for id in 0..=1 {
            for (status, fps, applied, persisted, pending, recovery) in [
                ("ok", "15", "true", "true", false, false),
                ("error", "15", "true", "null", true, false),
                ("error", "null", "null", "null", false, true),
                ("error", "15", "true", "true", false, false),
                ("error", "20", "false", "null", false, false),
            ] {
                let command = format!(r#"{{"cmd":"set-stream-fps","stream_id":{id},"fps":15}}"#);
                let body = format!(r#"{{"stream{id}":{{"fps":15}}}}"#);
                let reply = fps_observation(id, status, fps, applied, persisted, pending, recovery);
                let response = run_request(vec![(command, reply)], Some(body.as_bytes())).unwrap();
                let result = crate::json::parse(&response.body).unwrap();
                assert_eq!(
                    result.get_path("status").and_then(Value::as_str),
                    Some(if status == "ok" { "accepted" } else { "error" })
                );
                assert_eq!(
                    result.get_path("fps_result.persisted").unwrap().to_json(),
                    persisted
                );
                assert_eq!(
                    result
                        .get_path("fps_result.live_applied")
                        .unwrap()
                        .to_json(),
                    applied
                );
                assert_eq!(
                    result
                        .get_path("fps_result.persistence_pending")
                        .and_then(Value::as_bool),
                    Some(pending)
                );
                assert_eq!(
                    result
                        .get_path("fps_result.recovery_required")
                        .and_then(Value::as_bool),
                    Some(recovery)
                );
            }
        }
    }

    #[test]
    fn fps_missing_malformed_and_transport_results_never_claim_success() {
        for reply in [
            r#"{"status":"error","error":"unknown command"}"#.into(),
            "{}".into(),
            fps_observation(1, "ok", "15", "true", "true", false, false),
            fps_observation(0, "ok", "14", "true", "true", false, false),
            fps_observation(0, "ok", "15", "true", "null", false, false),
            fps_observation(0, "error", "15", "null", "null", false, true),
        ] {
            assert!(matches!(
                run_request(
                    vec![(
                        r#"{"cmd":"set-stream-fps","stream_id":0,"fps":15}"#.into(),
                        reply
                    )],
                    Some(br#"{"stream0":{"fps":15}}"#)
                ),
                Err(BackendError::PartialApply(_))
            ));
        }
        let root = task_temp("fps-missing");
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            b.update_streams(
                br#"{"stream0":{"fps":15}}"#,
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::PartialApply(_))
        ));
        for body in [
            r#"{"stream0":{"fps":0}}"#,
            r#"{"stream0":{"fps":31}}"#,
            r#"{"stream0":{"fps":1.5}}"#,
            r#"{"stream0":{"fps":true}}"#,
            r#"{"stream0":{"fps":15,"gop":30}}"#,
            r#"{"stream0":{"fps":15},"stream1":{"fps":15}}"#,
        ] {
            assert!(matches!(
                b.update_streams(body.as_bytes(), Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fps_get_capability_comes_from_current_backend_reply() {
        for reply in [
            fps_observation(0, "ok", "20", "true", "true", false, false),
            r#"{"status":"error","error":"unknown command"}"#.into(),
        ] {
            let supported = reply.contains("stream_id");
            let result = run_request(
                vec![
                    (read(), observation(0, "60")),
                    disk(),
                    (r#"{"cmd":"get-stream-fps","stream_id":0}"#.into(), reply),
                ],
                None,
            )
            .unwrap();
            let result = crate::json::parse(&result.body).unwrap();
            assert_eq!(
                result
                    .get_path("fps_control.available")
                    .and_then(Value::as_bool),
                Some(supported)
            );
        }
    }

    fn geometry_read(id: u64) -> String {
        format!(r#"{{"cmd":"get-stream-geometry","stream_id":{id}}}"#)
    }

    fn geometry_observation(id: u64, width: u64, height: u64) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":{id},"supported":true,"available":true,"profile":"dcs6100lhv2-a1-42m-22m-v1","active_width":{width},"active_height":{height}}}"#
        )
    }

    fn geometry_disk(id: u64, width: u64, height: u64) -> (String, String) {
        (
            format!(r#"{{"cmd":"config-read-section","section":"stream{id}"}}"#),
            format!(
                r#"{{"status":"ok","section":"stream{id}","keys":{{"width":"{width}","height":"{height}"}}}}"#
            ),
        )
    }

    fn geometry_motion_disk(width: u64, height: u64) -> (String, String) {
        (
            r#"{"cmd":"config-read-section","section":"motion"}"#.to_owned(),
            format!(
                r#"{{"status":"ok","section":"motion","keys":{{"roi_count":"1","roi0":"0,0,{},{}"}}}}"#,
                width - 1,
                height - 1
            ),
        )
    }

    #[test]
    fn geometry_save_keeps_active_state_and_requires_disk_readback() {
        let command = r#"{"cmd":"set-stream-geometry-config","stream0_width":1920,"stream0_height":1080,"stream1_width":320,"stream1_height":180}"#;
        let mut script = vec![
            (geometry_read(0), geometry_observation(0, 1920, 1080)),
            (geometry_read(1), geometry_observation(1, 640, 360)),
            geometry_disk(0, 1920, 1080),
            geometry_disk(1, 640, 360),
            geometry_motion_disk(640, 360),
            (command.to_owned(), r#"{"status":"ok","persistence":"checked-next-full-stack-restart","motion_roi_full_frame_adjusted":true,"pending_restart":true}"#.to_owned()),
            (r#"{"cmd":"config-save"}"#.to_owned(), r#"{"status":"ok"}"#.to_owned()),
            geometry_motion_disk(320, 180),
            geometry_disk(0, 1920, 1080),
            (geometry_read(0), geometry_observation(0, 1920, 1080)),
            geometry_disk(1, 320, 180),
            (geometry_read(1), geometry_observation(1, 640, 360)),
        ];
        let response = run_request(
            script.clone(),
            Some(br#"{"stream1":{"width":320,"height":180}}"#),
        )
        .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("pending_restart").and_then(Value::as_bool),
            Some(true)
        );
        script[10].1 = geometry_disk(1, 480, 270).1;
        script.truncate(11);
        assert!(matches!(
            run_request(script, Some(br#"{"stream1":{"width":320,"height":180}}"#)),
            Err(BackendError::PartialApply(_))
        ));
    }

    #[test]
    fn geometry_validates_complete_admitted_pair_before_ipc() {
        let root = task_temp("stream-geometry-invalid");
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"stream0":{"width":1280}}"#.as_slice(),
            br#"{"stream0":{"width":1280,"height":1080}}"#,
            br#"{"stream1":{"width":321,"height":180}}"#,
            br#"{"stream0":{"width":1280,"height":720,"gop":30}}"#,
        ] {
            assert!(matches!(
                b.update_streams(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn geometry_rejects_unadmitted_combination_before_mutation() {
        let script = vec![
            (geometry_read(0), geometry_observation(0, 1920, 1080)),
            (geometry_read(1), geometry_observation(1, 640, 360)),
            geometry_disk(0, 1920, 1080),
            geometry_disk(1, 640, 360),
            geometry_motion_disk(640, 360),
        ];
        assert!(matches!(
            run_request(
                script,
                Some(
                    br#"{"stream0":{"width":1280,"height":720},"stream1":{"width":640,"height":360}}"#,
                ),
            ),
            Err(BackendError::Protocol)
        ));
    }

    fn gop_mode_read(id: u64) -> String {
        format!(r#"{{"cmd":"get-stream-gop-mode","stream_id":{id}}}"#)
    }

    fn gop_mode_observation(id: u64, mode: &str) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":{id},"supported":true,"available":true,"active_mode":"{mode}"}}"#
        )
    }

    #[test]
    fn gop_mode_save_preserves_active_mode_until_full_restart() {
        let script = vec![
            (gop_mode_read(0), gop_mode_observation(0, "default")),
            (r#"{"cmd":"set-stream-gop-mode","stream_id":0,"gop_mode":"smartp"}"#.to_owned(),
             r#"{"status":"ok","persistence":"checked-next-full-stack-restart","pending_restart":true}"#.to_owned()),
            (r#"{"cmd":"config-save"}"#.to_owned(), r#"{"status":"ok"}"#.to_owned()),
            (r#"{"cmd":"config-read-section","section":"stream0"}"#.to_owned(),
             r#"{"status":"ok","section":"stream0","keys":{"gop_mode":"smartp"}}"#.to_owned()),
            (gop_mode_read(0), gop_mode_observation(0, "default")),
        ];
        let response = run_request(script, Some(br#"{"stream0":{"gop_mode":"SMARTP"}}"#)).unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("pending_restart").and_then(Value::as_bool),
            Some(true)
        );
    }

    #[test]
    fn gop_mode_save_rejects_malformed_disk_values_instead_of_defaulting() {
        for invalid in ["null", "true", "0", "[]", "{}"] {
            let script = vec![
                (gop_mode_read(0), gop_mode_observation(0, "default")),
                (r#"{"cmd":"set-stream-gop-mode","stream_id":0,"gop_mode":"default"}"#.to_owned(),
                 r#"{"status":"ok","persistence":"checked-next-full-stack-restart","pending_restart":false}"#.to_owned()),
                (r#"{"cmd":"config-save"}"#.to_owned(), r#"{"status":"ok"}"#.to_owned()),
                (r#"{"cmd":"config-read-section","section":"stream0"}"#.to_owned(),
                 format!(r#"{{"status":"ok","section":"stream0","keys":{{"gop_mode":{invalid}}}}}"#)),
            ];
            assert!(
                matches!(
                    run_request(script, Some(br#"{"stream0":{"gop_mode":"DEFAULT"}}"#)),
                    Err(BackendError::PartialApply(_))
                ),
                "malformed stored GOP mode: {invalid}"
            );
        }
    }

    #[test]
    fn gop_mode_rejects_aliases_before_ipc() {
        let root = task_temp("stream-gop-mode-invalid");
        let b = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"stream0":{"gop_mode":"SMART"}}"#.as_slice(),
            br#"{"stream0":{"gop_mode":"smartp"}}"#,
            br#"{"stream0":{"gop_mode":"SMARTP","gop":30}}"#,
        ] {
            assert!(matches!(
                b.update_streams(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }
}
