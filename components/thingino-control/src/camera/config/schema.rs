use std::collections::BTreeMap;

use crate::BackendError;
use crate::json::Value;

type Object = BTreeMap<String, Value>;

const MAX_DIMENSION: i64 = 16_384;
const MAX_BITRATE: i64 = 100_000_000;
const MAX_GOP: i64 = 65_535;

pub(crate) fn validate_prudynt_update(value: &Value) -> Result<(), BackendError> {
    let fields = object(value)?;
    if fields.is_empty() {
        return Err(BackendError::Protocol);
    }
    for (domain, value) in fields {
        validate_prudynt_domain(domain, value)?;
    }
    Ok(())
}

pub(crate) fn validate_prudynt_effective_streams(value: &Value) -> Result<(), BackendError> {
    let fields = object(value)?;
    for name in ["stream0", "stream1"] {
        let Some(stream) = fields.get(name).and_then(Value::as_object) else {
            continue;
        };
        if stream.get("enabled").and_then(Value::as_bool) != Some(true) {
            continue;
        }
        if !matches!(stream.get("fps").and_then(integer), Some(1..=30))
            || !matches!(stream.get("buffers").and_then(integer), Some(1..=65_535))
        {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

pub(crate) fn validate_prudynt_domain(domain: &str, value: &Value) -> Result<(), BackendError> {
    let fields = object(value)?;
    match domain {
        "audio" => validate_audio(fields),
        "image" => validate_image(fields),
        "stream0" | "stream1" => validate_stream(fields),
        "osd" => validate_osd(fields),
        "motion" => validate_motion(fields),
        "privacy" => validate_privacy(fields),
        "rtsp" => validate_rtsp(fields),
        _ => Err(BackendError::Protocol),
    }
}

fn object(value: &Value) -> Result<&Object, BackendError> {
    value.as_object().ok_or(BackendError::Protocol)
}

fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Number(raw) => raw.parse().ok(),
        _ => None,
    }
}

fn real(value: &Value) -> Option<f64> {
    match value {
        Value::Number(raw) => raw.parse().ok(),
        _ => None,
    }
}

fn require_bool(value: &Value) -> Result<(), BackendError> {
    if matches!(value, Value::Bool(_)) {
        Ok(())
    } else {
        Err(BackendError::Protocol)
    }
}

fn require_string(value: &Value) -> Result<(), BackendError> {
    if matches!(value, Value::String(_)) {
        Ok(())
    } else {
        Err(BackendError::Protocol)
    }
}

fn require_integer(value: &Value, min: i64, max: i64) -> Result<(), BackendError> {
    integer(value)
        .filter(|value| (min..=max).contains(value))
        .map(|_| ())
        .ok_or(BackendError::Protocol)
}

fn require_nonnegative_integer(value: &Value) -> Result<(), BackendError> {
    require_integer(value, 0, i64::MAX)
}

fn require_real(value: &Value, min: f64, max: f64) -> Result<(), BackendError> {
    real(value)
        .filter(|value| value.is_finite() && (min..=max).contains(value))
        .map(|_| ())
        .ok_or(BackendError::Protocol)
}

fn require_enum(value: &Value, allowed: &[&str]) -> Result<(), BackendError> {
    match value {
        Value::String(value) if allowed.iter().any(|item| *item == value) => Ok(()),
        _ => Err(BackendError::Protocol),
    }
}

fn require_hex_color(value: &Value) -> Result<(), BackendError> {
    let Value::String(value) = value else {
        return Err(BackendError::Protocol);
    };
    let bytes = value.as_bytes();
    if bytes.len() != 9
        || bytes[0] != b'#'
        || !bytes[1..].iter().all(|byte| {
            byte.is_ascii_digit() || (b'a'..=b'f').contains(byte) || (b'A'..=b'F').contains(byte)
        })
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn validate_audio(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "mic_format" => require_enum(value, &["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"])?,
            "mic_vol" | "spk_vol" => require_integer(value, -30, 120)?,
            "mic_gain" | "spk_gain" => require_integer(value, 0, 31)?,
            "mic_alc_gain" => require_integer(value, 0, 7)?,
            "mic_noise_suppression" => require_integer(value, 0, 3)?,
            "mic_agc_compression_gain_db" => require_integer(value, 0, 90)?,
            "mic_agc_target_level_dbfs" => require_integer(value, 0, 31)?,
            "buffer_warn_frames" | "buffer_cap_frames" => require_integer(value, 10, 1_000)?,
            "force_stereo"
            | "mic_enabled"
            | "mic_agc_enabled"
            | "mic_high_pass_filter"
            | "mic_is_digital"
            | "spk_enabled"
            | "tap_enabled" => require_bool(value)?,
            "tap_path" => require_string(value)?,
            _ => {}
        }
    }
    Ok(())
}

fn validate_image(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "ae_compensation" | "brightness" | "contrast" | "defog_strength" | "dpc_strength"
            | "drc_strength" | "highlight_depress" | "hue" | "saturation" | "sharpness" => {
                require_integer(value, 0, 255)?
            }
            "anti_flicker" => require_integer(value, 0, 2)?,
            "backlight_compensation" | "backlight" => require_integer(value, 0, 10)?,
            "core_wb_mode" => require_integer(value, 0, 9)?,
            "max_again" | "max_dgain" => require_integer(value, 0, 160)?,
            "running_mode" => require_integer(value, 0, 1)?,
            "sinter_strength" | "temper_strength" => require_integer(value, 0, 255)?,
            "wb_bgain" | "wb_rgain" => require_integer(value, 0, 1_024)?,
            "hflip" | "vflip" | "isp_bypass" => require_bool(value)?,
            "wide_dynamic_range" | "tone" | "defog" | "noise_reduction" => {
                require_integer(value, 0, 255)?
            }
            _ => {}
        }
    }
    Ok(())
}

fn validate_stream(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "format" => require_enum(value, &["H264", "H265"])?,
            "mode" => require_enum(
                value,
                &["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"],
            )?,
            "enabled" | "audio_enabled" | "allow_shared" => require_bool(value)?,
            "fps" => require_integer(value, 0, 30)?,
            "buffers" => {
                let valid = integer(value)
                    .is_some_and(|value| value == -1 || (1..=65_535).contains(&value));
                if !valid {
                    return Err(BackendError::Protocol);
                }
            }
            "width" | "height" => require_integer(value, 1, MAX_DIMENSION)?,
            "bitrate" | "bandwidth" => require_integer(value, 1, MAX_BITRATE)?,
            "max_bitrate" => {
                let valid = integer(value).is_some_and(|value| {
                    value == -1 || value == 0 || (1..=MAX_BITRATE).contains(&value)
                });
                if !valid {
                    return Err(BackendError::Protocol);
                }
            }
            "gop" | "max_gop" => require_integer(value, 1, MAX_GOP)?,
            "qp_init" | "qp_min" | "qp_max" => require_integer(value, -1, 51)?,
            "ip_delta" | "pb_delta" => {
                let valid =
                    integer(value).is_some_and(|value| value == -1 || (-20..=20).contains(&value));
                if !valid {
                    return Err(BackendError::Protocol);
                }
            }
            "profile" => require_integer(value, 0, 2)?,
            "rotation" => {
                let valid = integer(value).is_some_and(|value| matches!(value, 0 | 90 | 180 | 270));
                if !valid {
                    return Err(BackendError::Protocol);
                }
            }
            "rtsp_endpoint" | "rtsp_info" => require_string(value)?,
            "osd" => validate_osd(value.as_object().ok_or(BackendError::Protocol)?)?,
            _ => {}
        }
    }
    Ok(())
}

fn validate_osd(fields: &Object) -> Result<(), BackendError> {
    validate_osd_object(fields)
}

fn validate_osd_object(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "enabled" => require_bool(value)?,
            "format" | "position" | "font_path" => require_string(value)?,
            "scale" => require_integer(value, 0, 10)?,
            "fill_color" | "stroke_color" | "outline_color" | "background_color" => {
                require_hex_color(value)?
            }
            "type" => require_enum(
                value,
                &[
                    "text",
                    "gain",
                    "hostname",
                    "ipaddress",
                    "timestamp",
                    "uptime",
                ],
            )?,
            "opacity" | "alpha" => require_integer(value, 0, 255)?,
            "rotation" | "font_size" | "start_delay" | "stroke_size" => {
                require_integer(value, i64::MIN, i64::MAX)?
            }
            "burnin" | "privacy" | "sei" | "brightness" | "time" | "uptime" | "usertext"
            | "logo" => validate_osd_object(object(value)?)?,
            "entries" => {
                let entries = object(value)?;
                for entry in entries.values() {
                    validate_osd_entry(entry)?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

fn validate_osd_entry(value: &Value) -> Result<(), BackendError> {
    let fields = object(value)?;
    for required in ["type", "format", "position"] {
        if !fields.contains_key(required) {
            return Err(BackendError::Protocol);
        }
    }
    validate_osd_object(fields)
}

fn validate_motion(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "enabled" | "playonspeaker" | "send2email" | "send2ftp" | "send2gotify"
            | "send2mqtt" | "send2ntfy" | "send2storage" | "send2telegram" | "send2webhook" => {
                require_bool(value)?
            }
            "sensitivity" => require_integer(value, 1, 8)?,
            "cooldown_time" => require_integer(value, 1, 60)?,
            "debounce_time" | "post_time" | "init_time" | "min_time" | "skip_frame_count"
            | "video_length" => require_nonnegative_integer(value)?,
            "ivs_polling_timeout" => require_integer(value, 100, 10_000)?,
            "motor_settle_ms" => require_integer(value, 0, 10_000)?,
            "frame_width" | "frame_height" => require_integer(value, 0, MAX_DIMENSION)?,
            "monitor_stream" => require_integer(value, 0, 1)?,
            "roi_0_x" | "roi_0_y" | "roi_1_x" | "roi_1_y" => {
                require_integer(value, 0, MAX_DIMENSION)?
            }
            "roi_count" => require_integer(value, 1, 52)?,
            "script_path" => return Err(BackendError::Protocol),
            _ => {}
        }
    }
    Ok(())
}

fn validate_privacy(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "enabled" | "stream0_enabled" | "stream1_enabled" => require_bool(value)?,
            _ => return Err(BackendError::Protocol),
        }
    }
    Ok(())
}

fn validate_rtsp(fields: &Object) -> Result<(), BackendError> {
    for (name, value) in fields {
        match name.as_str() {
            "audio_only_enabled" | "auth_required" => require_bool(value)?,
            "bandwidth_margin" => require_real(value, 0.0, 100.0)?,
            "packet_loss_threshold" => require_real(value, 0.0, 1.0)?,
            "est_bitrate" | "out_buffer_size" | "send_buffer_size" | "session_reclaim" => {
                require_nonnegative_integer(value)?
            }
            "port" => require_integer(value, 1, 65_535)?,
            "name" | "audio_only_endpoint" | "audio_only_info" | "username" => {
                require_string(value)?
            }
            "password" if matches!(value, Value::Null | Value::String(_)) => {}
            "password" => return Err(BackendError::Protocol),
            _ => {}
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json;

    fn check(raw: &[u8]) -> Result<(), BackendError> {
        let value = json::parse(raw).expect("test JSON");
        validate_prudynt_update(&value)
    }

    #[test]
    fn accepts_disabled_stream_sentinels_and_unknown_pinned_fields() {
        assert!(check(
            br#"{"stream1":{"enabled":false,"fps":0,"buffers":-1,"future_encoder_field":"kept"}}"#
        )
        .is_ok());
        assert!(check(br#"{"stream0":{"buffers":65535,"rotation":180}}"#).is_ok());
        assert!(check(br#"{"rtsp":{"password":null}}"#).is_ok());
        // access.rs treats an empty password as "preserve the current secret".
        assert!(check(br#"{"rtsp":{"password":""}}"#).is_ok());
    }

    #[test]
    fn effective_enabled_stream_requires_frames_and_buffers() {
        for raw in [
            br#"{"stream1":{"enabled":true,"fps":0,"buffers":1}}"#.as_slice(),
            br#"{"stream1":{"enabled":true,"fps":15,"buffers":-1}}"#.as_slice(),
            br#"{"stream1":{"enabled":true,"fps":15}}"#.as_slice(),
        ] {
            let value = json::parse(raw).unwrap();
            assert_eq!(
                validate_prudynt_effective_streams(&value),
                Err(BackendError::Protocol)
            );
        }
        let active = json::parse(br#"{"stream1":{"enabled":true,"fps":15,"buffers":1}}"#).unwrap();
        assert!(validate_prudynt_effective_streams(&active).is_ok());
        let disabled =
            json::parse(br#"{"stream1":{"enabled":false,"fps":0,"buffers":-1}}"#).unwrap();
        assert!(validate_prudynt_effective_streams(&disabled).is_ok());
    }

    #[test]
    fn accepts_current_dlink_audio_image_and_stream_values() {
        assert!(check(
            br#"{"audio":{"mic_format":"AAC","mic_vol":80,"mic_gain":25,"mic_alc_gain":0,"mic_noise_suppression":0,"mic_agc_compression_gain_db":0,"mic_agc_target_level_dbfs":10,"mic_agc_enabled":false,"mic_high_pass_filter":false,"force_stereo":false},"image":{"brightness":128,"contrast":128,"anti_flicker":2,"core_wb_mode":0,"wb_rgain":0,"wb_bgain":0,"hflip":false,"vflip":false},"stream0":{"enabled":true,"audio_enabled":false,"width":1920,"height":1080,"fps":15,"bitrate":3000,"gop":30,"max_gop":60,"buffers":1,"format":"H264","mode":"CBR"}}"#
        )
        .is_ok());
    }

    #[test]
    fn accepts_current_parity_fixture_representations() {
        assert!(check(
            br#"{"audio":{"buffer_cap_frames":100,"buffer_warn_frames":80,"force_stereo":false,"mic_agc_compression_gain_db":9,"mic_agc_enabled":true,"mic_agc_target_level_dbfs":10,"mic_alc_gain":4,"mic_enabled":true,"mic_format":"AAC","mic_gain":18,"mic_high_pass_filter":false,"mic_is_digital":false,"mic_noise_suppression":1,"mic_vol":70,"spk_enabled":false,"spk_gain":12,"spk_vol":55,"tap_enabled":false,"tap_path":"/run/prudynt/audio_mic.pcm"},"image":{"brightness":128,"contrast":128,"sharpness":128,"saturation":128,"hue":128,"backlight_compensation":0,"drc_strength":0,"defog_strength":0,"dpc_strength":64,"core_wb_mode":0,"wb_bgain":128,"wb_rgain":128,"ae_compensation":128,"hflip":false,"vflip":false},"stream0":{"allow_shared":true,"audio_enabled":false,"enabled":true,"width":1920,"height":1080,"format":"H264","fps":20,"gop":40,"max_gop":80,"mode":"CBR","bitrate":2400,"profile":2,"buffers":4,"rtsp_endpoint":"ch0","rotation":0,"qp_init":-1,"qp_max":-1,"qp_min":-1},"stream1":{"allow_shared":true,"audio_enabled":false,"enabled":false,"width":640,"height":360,"format":"H264","fps":0,"gop":30,"max_gop":60,"mode":"CBR","bitrate":640,"profile":1,"buffers":-1,"rtsp_endpoint":"ch1","rotation":0,"qp_init":-1,"qp_max":-1,"qp_min":-1}}"#
        )
        .is_ok());
        assert!(check(
            br##"{"osd":{"burnin":{"enabled":true,"format":"%F %T","scale":1,"fill_color":"#ffffffff","outline_color":"#000000ff","background_color":"#00000080"},"privacy":{"enabled":false},"sei":{"enabled":true,"entries":{"clock":{"type":"timestamp","format":"%F %T","position":"-10,-10"},"gain":{"type":"gain","format":"%s","position":"10,-10"}}}},"motion":{"enabled":true,"sensitivity":5,"cooldown_time":5,"debounce_time":0,"init_time":5,"min_time":1,"post_time":0,"monitor_stream":1,"skip_frame_count":5,"video_length":10,"send2email":false,"send2ftp":false,"send2gotify":false,"send2mqtt":false,"send2ntfy":false,"send2storage":false,"send2telegram":false,"send2webhook":false},"privacy":{"enabled":false,"stream0_enabled":false,"stream1_enabled":false}}"##
        )
        .is_ok());
    }

    #[test]
    fn rejects_wrong_audio_enum_and_range() {
        assert!(check(br#"{"audio":{"mic_format":"FLAC"}}"#).is_err());
        assert!(check(br#"{"audio":{"mic_vol":121}}"#).is_err());
        assert!(check(br#"{"audio":{"mic_alc_gain":32}}"#).is_err());
        assert!(check(br#"{"audio":{"mic_agc_target_level_dbfs":-1}}"#).is_err());
        assert!(check(br#"{"audio":{"mic_agc_target_level_dbfs":32}}"#).is_err());
        assert!(check(br#"{"audio":{"mic_agc_enabled":1}}"#).is_err());
    }

    #[test]
    fn rejects_wrong_image_and_stream_values() {
        assert!(check(br#"{"image":{"brightness":256}}"#).is_err());
        assert!(check(br#"{"image":{"core_wb_mode":10}}"#).is_err());
        assert!(check(br#"{"image":{"running_mode":2}}"#).is_err());
        assert!(check(
            br#"{"image":{"wb_rgain":1024,"wb_bgain":0,"backlight":10,"wide_dynamic_range":255,"tone":0,"defog":128,"noise_reduction":255}}"#
        )
        .is_ok());
        assert!(check(br#"{"image":{"wb_rgain":1025}}"#).is_err());
        assert!(check(br#"{"image":{"backlight":11}}"#).is_err());
        assert!(check(br#"{"image":{"backlight_compensation":11}}"#).is_err());
        assert!(check(br#"{"image":{"max_again":161}}"#).is_err());
        assert!(check(br#"{"image":{"sinter_strength":256}}"#).is_err());
        assert!(check(br#"{"image":{"core_wb_mode":"auto"}}"#).is_err());
        assert!(check(br#"{"stream0":{"format":"VP9"}}"#).is_err());
        assert!(check(br#"{"stream0":{"mode":"SMART"}}"#).is_err());
        assert!(check(br#"{"stream0":{"fps":31}}"#).is_err());
        assert!(check(br#"{"stream0":{"buffers":0}}"#).is_err());
        assert!(check(br#"{"stream0":{"buffers":65536}}"#).is_err());
        assert!(check(br#"{"motion":{"monitor_stream":2}}"#).is_err());
        assert!(check(br#"{"stream0":{"width":0,"height":1080}}"#).is_err());
        assert!(check(br#"{"stream0":{"bitrate":0,"gop":0}}"#).is_err());
    }

    #[test]
    fn validates_osd_motion_and_privacy_known_fields_without_closing_schema() {
        assert!(check(
            br##"{"osd":{"burnin":{"enabled":true,"scale":10,"fill_color":"#ffffffff"},"sei":{"enabled":true,"entries":{"clock":{"type":"timestamp","format":"%F %T","position":"-10,-10"}},"future":true},"future_osd":{"new_field":[1,2,3]}},"motion":{"enabled":false,"sensitivity":8,"cooldown_time":60,"future_motion":{"mode":"vendor"}},"privacy":{"enabled":true,"stream0_enabled":false,"stream1_enabled":true}}"##
        )
        .is_ok());
        assert!(check(br#"{"osd":{"burnin":{"scale":11}}}"#).is_err());
        assert!(check(br##"{"osd":{"burnin":{"fill_color":"#fff"}}}"##).is_err());
        assert!(check(br#"{"osd":{"burnin":{"opacity":255,"alpha":0}}}"#).is_ok());
        assert!(check(br#"{"osd":{"burnin":{"opacity":256}}}"#).is_err());
        assert!(check(br#"{"osd":{"sei":{"entries":{"clock":{"type":"clock"}}}}}"#).is_err());
        assert!(check(br#"{"osd":{"sei":{"entries":{"clock":{"type":"timestamp"}}}}}"#).is_err());
        assert!(check(br#"{"motion":{"sensitivity":0}}"#).is_err());
        assert!(check(br#"{"motion":{"sensitivity":9}}"#).is_err());
        assert!(check(br#"{"motion":{"cooldown_time":61}}"#).is_err());
        assert!(check(br#"{"motion":{"script_path":"/tmp/x"}}"#).is_err());
        assert!(check(br#"{"privacy":{"stream0_enabled":"false"}}"#).is_err());
    }
}
