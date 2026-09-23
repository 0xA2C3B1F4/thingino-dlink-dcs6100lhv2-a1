use super::*;

pub(super) const ROI_KEYS: [&str; 4] = ["roi_0_x", "roi_0_y", "roi_1_x", "roi_1_y"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct SingleRoi(pub [u64; 4]);

impl SingleRoi {
    pub(super) fn from_value(value: &Value) -> Result<Self, BackendError> {
        let mut values = [0; 4];
        for (index, name) in ROI_KEYS.iter().enumerate() {
            values[index] = value
                .get_path(name)
                .ok_or(BackendError::Protocol)
                .and_then(value_u64)
                .map_err(|_| BackendError::Protocol)?;
        }
        let result = Self(values);
        result.validate(16384, 16384)?;
        Ok(result)
    }

    pub(super) fn validate(self, width: u64, height: u64) -> Result<(), BackendError> {
        let [x0, y0, x1, y1] = self.0;
        if x0 >= x1 || y0 >= y1 || x1 - x0 < 2 || y1 - y0 < 2 || x1 > width || y1 > height {
            return Err(BackendError::Protocol);
        }
        Ok(())
    }

    fn value(self) -> Value {
        Value::Object(
            ROI_KEYS
                .iter()
                .zip(self.0)
                .map(|(key, value)| (key.to_string(), Value::Number(value.to_string())))
                .collect(),
        )
    }
}

pub(super) struct MotionRoi {
    pub(super) supported: bool,
    pub(super) available: bool,
    pub(super) applied: bool,
    pub(super) frame: Option<(u64, u64)>,
    pub(super) count: Option<u64>,
    pub(super) single: Option<SingleRoi>,
}

impl MotionRoi {
    pub(super) fn matches(&self, expected: SingleRoi, frame: (u64, u64)) -> bool {
        self.supported
            && self.available
            && self.applied
            && self.frame == Some(frame)
            && self.count == Some(1)
            && self.single == Some(expected)
    }

    fn value(&self, saved: Option<SingleRoi>) -> Value {
        let optional = |value: Option<u64>| {
            value.map_or(Value::Null, |value| Value::Number(value.to_string()))
        };
        Value::Object(
            [
                ("supported".into(), Value::Bool(self.supported)),
                ("available".into(), Value::Bool(self.available)),
                ("applied".into(), Value::Bool(self.applied)),
                (
                    "frame_width".into(),
                    optional(self.frame.map(|value| value.0)),
                ),
                (
                    "frame_height".into(),
                    optional(self.frame.map(|value| value.1)),
                ),
                ("count".into(), optional(self.count)),
                (
                    "single".into(),
                    self.single.map_or(Value::Null, SingleRoi::value),
                ),
                (
                    "saved_single".into(),
                    saved.map_or(Value::Null, SingleRoi::value),
                ),
                (
                    "matches_saved".into(),
                    Value::Bool(
                        self.available
                            && self.applied
                            && self.single.is_some()
                            && self.single == saved,
                    ),
                ),
            ]
            .into(),
        )
    }
}

impl RaptorBackend {
    pub(super) fn motion_roi_observation(
        reply: &RaptorReply,
    ) -> Result<Option<MotionRoi>, BackendError> {
        let Some(roi) = reply.value.get_path("roi") else {
            return Ok(None);
        };
        if roi.get_path("contract").and_then(Value::as_str) != Some("single-region-v1") {
            return Err(BackendError::Unavailable);
        }
        let flag = |name| {
            roi.get_path(name)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))
        };
        let supported = flag("supported")?;
        let available = flag("available")?;
        let applied = flag("applied")?;
        if (available
            && (!supported
                || reply.value.get_path("available").and_then(Value::as_bool) != Some(true)))
            || (applied && !available)
        {
            return Err(BackendError::Upstream(502));
        }
        let (frame, count, single) = if available {
            let number = |name| {
                roi.get_path(name)
                    .ok_or(BackendError::Upstream(502))
                    .and_then(value_u64)
            };
            let (width, height, count) = (
                number("frame_width")?,
                number("frame_height")?,
                number("count")?,
            );
            if !(2..=16384).contains(&width)
                || !(2..=16384).contains(&height)
                || !(1..=51).contains(&count)
            {
                return Err(BackendError::Upstream(502));
            }
            let single = if count == 1 {
                let value = roi
                    .get_path("single")
                    .filter(|value| value.as_object().is_some_and(|fields| fields.len() == 4))
                    .ok_or(BackendError::Upstream(502))?;
                let single =
                    SingleRoi::from_value(value).map_err(|_| BackendError::Upstream(502))?;
                single
                    .validate(width, height)
                    .map_err(|_| BackendError::Upstream(502))?;
                Some(single)
            } else {
                if roi.get_path("single") != Some(&Value::Null) {
                    return Err(BackendError::Upstream(502));
                }
                None
            };
            (Some((width, height)), Some(count), single)
        } else {
            for name in ["frame_width", "frame_height", "count", "single"] {
                if roi.get_path(name) != Some(&Value::Null) {
                    return Err(BackendError::Upstream(502));
                }
            }
            (None, None, None)
        };
        Ok(Some(MotionRoi {
            supported,
            available,
            applied,
            frame,
            count,
            single,
        }))
    }

    pub(super) fn motion_disk_roi(&self, deadline: Instant) -> Result<SingleRoi, BackendError> {
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
        let tuple = reply
            .value
            .get_path("keys.roi0")
            .and_then(Value::as_str)
            .filter(|value| value.len() <= 23)
            .ok_or(BackendError::Upstream(502))?;
        let values: Vec<u64> = tuple
            .split(',')
            .map(|part| {
                if part.is_empty()
                    || !part.bytes().all(|byte| byte.is_ascii_digit())
                    || (part.len() > 1 && part.starts_with('0'))
                {
                    return Err(BackendError::Upstream(502));
                }
                part.parse::<u64>().map_err(|_| BackendError::Upstream(502))
            })
            .collect::<Result<_, _>>()?;
        let [x0, y0, x1, y1]: [u64; 4] =
            values.try_into().map_err(|_| BackendError::Upstream(502))?;
        let single = SingleRoi([
            x0,
            y0,
            x1.checked_add(1).ok_or(BackendError::Upstream(502))?,
            y1.checked_add(1).ok_or(BackendError::Upstream(502))?,
        ]);
        single
            .validate(16384, 16384)
            .map_err(|_| BackendError::Upstream(502))?;
        Ok(single)
    }

    pub(super) fn motion_roi_config_value(
        &self,
        state: &RaptorReply,
        deadline: Instant,
    ) -> Result<Value, BackendError> {
        let Some(roi) = Self::motion_roi_observation(state)? else {
            return Ok(Value::Null);
        };
        Ok(roi.value(self.motion_disk_roi(deadline).ok()))
    }

    pub(super) fn apply_motion_roi(
        &self,
        roi: SingleRoi,
        frame: (u64, u64),
        deadline: Instant,
    ) -> Result<(), BackendError> {
        let [x0, y0, x1, y1] = roi.0;
        let (width, height) = frame;
        let command = format!(
            r#"{{"cmd":"set-motion-roi","frame_width":{width},"frame_height":{height},"roi_0_x":{x0},"roi_0_y":{y0},"roi_1_x":{x1},"roi_1_y":{y1},"roi_count":1}}"#
        );
        require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)
    }
}

#[cfg(test)]
mod tests {
    use super::super::motion::tests::sequence;
    use super::super::tests::{backend, task_temp};
    use super::*;
    use std::fs;

    const GET: &[u8] = br#"{"cmd":"get-motion-config"}"#;
    const DISK: &[u8] = br#"{"cmd":"config-read-section","section":"motion"}"#;
    const SET: &[u8] = br#"{"cmd":"set-motion-roi","frame_width":640,"frame_height":360,"roi_0_x":64,"roi_0_y":32,"roi_1_x":320,"roi_1_y":240,"roi_count":1}"#;
    const BODY: &[u8] = br#"{"motion":{"enabled":true,"roi_0_x":64,"roi_0_y":32,"roi_1_x":320,"roi_1_y":240,"roi_count":1}}"#;
    const OK: &[u8] = br#"{"status":"ok"}"#;
    const SAVED: &[u8] = br#"{"status":"ok","section":"motion","keys":{"enabled":"true","sensitivity":"4","roi_count":"1","roi0":"64,32,319,239"}}"#;

    fn observation(active: bool, applied: bool, count: u64) -> Vec<u8> {
        let single = if count == 1 {
            r#"{"roi_0_x":64,"roi_0_y":32,"roi_1_x":320,"roi_1_y":240}"#
        } else {
            "null"
        };
        format!(r#"{{"status":"ok","persistence":"checked-config","supported":true,"available":true,"active":{active},"receiving":{active},"worker":{active},"sensitivity":{{"supported":true,"available":{active},"min":0,"max":4,"value":{}}},"roi":{{"contract":"single-region-v1","supported":true,"available":true,"applied":{applied},"frame_width":640,"frame_height":360,"count":{count},"single":{single}}}}}"#,
            if active { "4" } else { "null" }).into_bytes()
    }

    #[test]
    fn roi_save_checks_pause_resume_live_disk_and_final_observation() {
        for start in ["multi", "unchanged", "paused-unconfirmed"] {
            for fault in ["none", "setter", "resume", "live", "save", "disk", "final"] {
                let root = task_temp("roi-save");
                let mut steps = vec![
                    (
                        GET.to_vec(),
                        observation(
                            start != "paused-unconfirmed",
                            start != "paused-unconfirmed",
                            if start == "multi" { 2 } else { 1 },
                        ),
                    ),
                    (SET.to_vec(), OK.to_vec()),
                    (
                        br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(),
                        OK.to_vec(),
                    ),
                    (GET.to_vec(), observation(true, true, 1)),
                    (br#"{"cmd":"config-save"}"#.to_vec(), OK.to_vec()),
                    (DISK.to_vec(), SAVED.to_vec()),
                    (DISK.to_vec(), SAVED.to_vec()),
                    (GET.to_vec(), observation(true, true, 1)),
                ];
                let index = match fault {
                    "setter" => Some(1),
                    "resume" => Some(2),
                    "live" => Some(3),
                    "save" => Some(4),
                    "disk" => Some(6),
                    "final" => Some(7),
                    _ => None,
                };
                if let Some(index) = index {
                    steps[index].1 = match fault {
                        "live" | "final" => observation(true, false, 1),
                        "disk" => String::from_utf8(SAVED.to_vec())
                            .unwrap()
                            .replace("319,239", "320,239")
                            .into_bytes(),
                        _ => br#"{"status":"error"}"#.to_vec(),
                    };
                    steps.truncate(index + 1);
                }
                let daemon = sequence(&root, steps);
                let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                    .update_motion_config(BODY, Instant::now() + Duration::from_secs(2));
                if fault == "none" {
                    assert!(result.is_ok(), "{start}: {result:?}");
                } else {
                    assert!(
                        matches!(result, Err(BackendError::PartialApply(_))),
                        "{start}/{fault}: {result:?}"
                    );
                }
                daemon.join().unwrap();
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn sensitivity_is_set_before_roi_pause_and_independently_persisted() {
        let root = task_temp("roi-sensitivity");
        let daemon = sequence(
            &root,
            vec![
                (GET.to_vec(), observation(true, true, 2)),
                (
                    br#"{"cmd":"set-motion-sensitivity","sensitivity":4}"#.to_vec(),
                    OK.to_vec(),
                ),
                (SET.to_vec(), OK.to_vec()),
                (
                    br#"{"cmd":"set-motion-config","enabled":true}"#.to_vec(),
                    OK.to_vec(),
                ),
                (GET.to_vec(), observation(true, true, 1)),
                (br#"{"cmd":"config-save"}"#.to_vec(), OK.to_vec()),
                (DISK.to_vec(), SAVED.to_vec()),
                (DISK.to_vec(), SAVED.to_vec()),
                (DISK.to_vec(), SAVED.to_vec()),
                (GET.to_vec(), observation(true, true, 1)),
            ],
        );
        let body = String::from_utf8(BODY.to_vec())
            .unwrap()
            .replace("\"enabled\":true", "\"enabled\":true,\"sensitivity\":4");
        let result = backend(&root, "127.0.0.1:9".parse().unwrap())
            .update_motion_config(body.as_bytes(), Instant::now() + Duration::from_secs(2));
        assert!(result.is_ok(), "{result:?}");
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_roi_capability_and_stale_frame_reject_before_writes() {
        let source = String::from_utf8(observation(true, true, 1)).unwrap();
        for reply in [
            source.replace("single-region-v1", "unknown"),
            source.replace("\"frame_width\":640", "\"frame_width\":200"),
            source.replace("\"count\":1", "\"count\":52"),
            source.replace("\"count\":1", "\"count\":2"),
            source.replace("\"roi_1_x\":320", "\"roi_1_x\":65"),
            source.replace("\"frame_height\":360", "\"frame_height\":null"),
            source.replace("\"available\":true", "\"available\":false"),
            r#"{"status":"ok","persistence":"checked-config","supported":true,"available":true,"active":true,"receiving":true,"worker":true}"#.into(),
        ] {
            let root = task_temp("roi-capability");
            let daemon = sequence(&root, vec![(GET.to_vec(), reply.into_bytes())]);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_motion_config(BODY, Instant::now() + Duration::from_secs(2));
            assert!(result.is_err() && !matches!(result, Err(BackendError::PartialApply(_))), "{result:?}");
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn incomplete_or_invalid_roi_payload_never_contacts_daemon() {
        let root = task_temp("roi-payload");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        let source = String::from_utf8(BODY.to_vec()).unwrap();
        for body in [
            source.replace("true", "false"),
            source.replace("\"roi_count\":1", "\"roi_count\":2"),
            source.replace("\"roi_0_y\":32,", ""),
            source.replace("\"roi_0_x\":64", "\"roi_0_x\":-1"),
            source.replace("\"roi_0_x\":64", "\"roi_0_x\":1.5"),
            source.replace("\"roi_1_x\":320", "\"roi_1_x\":65"),
            source.replace("\"roi_count\":1", "\"roi_count\":1,\"send2mqtt\":true"),
        ] {
            assert!(matches!(
                backend
                    .update_motion_config(body.as_bytes(), Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn saved_roi_requires_canonical_inclusive_tuple_and_single_count() {
        for tuple in [
            "64,32,319,239",
            "064,32,319,239",
            "64,32,319",
            "64,32,319,239 trailing",
            "64,32,64,239",
            "64,32,16384,239",
        ] {
            let root = task_temp("roi-disk");
            let response = String::from_utf8(SAVED.to_vec())
                .unwrap()
                .replace("64,32,319,239", tuple);
            let daemon = sequence(&root, vec![(DISK.to_vec(), response.into_bytes())]);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .motion_disk_roi(Instant::now() + Duration::from_secs(2));
            assert_eq!(result.is_ok(), tuple == "64,32,319,239");
            if let Ok(roi) = result {
                assert_eq!(roi, SingleRoi([64, 32, 320, 240]));
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }
}
