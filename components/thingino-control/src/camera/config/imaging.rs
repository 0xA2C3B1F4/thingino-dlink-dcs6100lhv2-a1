use super::super::*;

impl PrudyntBackend {
    pub(in crate::camera) fn imaging(&self) -> Result<BackendResponse, BackendError> {
        let state = json::parse(&read_bounded(&self.paths.imaging_state, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let fields = state
            .get_path("fields")
            .cloned()
            .ok_or(BackendError::Protocol)?;
        let body = object([
            ("code", number(200)),
            ("result", Value::String("success".to_owned())),
            ("message", object([("fields", fields)])),
        ]);
        json_response(body)
    }
    pub(in crate::camera) fn update_imaging(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        const FIELDS: [&str; 9] = [
            "brightness",
            "contrast",
            "saturation",
            "sharpness",
            "backlight",
            "wide_dynamic_range",
            "tone",
            "defog",
            "noise_reduction",
        ];
        let values = parse_imaging_values(body)?;
        let state = json::parse(&read_bounded(&self.paths.imaging_state, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let mut command = String::from("SET");
        let mut changed = false;
        for field in FIELDS {
            let Some(raw) = values.get(field) else {
                continue;
            };
            let value = raw.parse::<f64>().map_err(|_| BackendError::Protocol)?;
            let minimum = json_number(&state, &format!("fields.{field}.min"))?;
            let maximum = json_number(&state, &format!("fields.{field}.max"))?;
            if state
                .get_path(&format!("fields.{field}.supported"))
                .and_then(Value::as_bool)
                != Some(true)
                || maximum <= minimum
            {
                return Err(BackendError::Protocol);
            }
            let normalized =
                ((value.max(minimum).min(maximum) - minimum) / (maximum - minimum)).clamp(0.0, 1.0);
            command.push_str(&format!(" {field}={normalized:.4}"));
            changed = true;
        }
        if !changed {
            return Err(BackendError::Protocol);
        }
        command.push('\n');
        write_fifo(&self.paths.imaging_ctl, command.as_bytes())?;
        self.imaging()
    }
}

fn parse_imaging_values(body: &[u8]) -> Result<BTreeMap<String, String>, BackendError> {
    if body.first().copied() != Some(b'{') {
        return parse_form(body);
    }
    let value = json::parse(body).map_err(|_| BackendError::Protocol)?;
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    if fields.is_empty() {
        return Err(BackendError::Protocol);
    }
    let mut values = BTreeMap::new();
    for (name, value) in fields {
        let Value::Number(value) = value else {
            return Err(BackendError::Protocol);
        };
        values.insert(name.clone(), value.clone());
    }
    Ok(values)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = PathBuf::from(root).join(format!(
            "thingino-control-imaging-{}-{}-{name}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[cfg(unix)]
    fn make_fifo(path: &Path) {
        unsafe extern "C" {
            fn mkfifo(path: *const std::os::raw::c_char, mode: u32) -> std::os::raw::c_int;
        }
        let path = CString::new(path.as_os_str().as_encoded_bytes()).unwrap();
        assert_eq!(unsafe { mkfifo(path.as_ptr(), 0o600) }, 0);
    }

    #[test]
    fn canonical_imaging_json_is_numeric_and_partial() {
        let values = parse_imaging_values(br#"{"brightness":128,"defog":64.5}"#).unwrap();
        assert_eq!(values.get("brightness").map(String::as_str), Some("128"));
        assert_eq!(values.get("defog").map(String::as_str), Some("64.5"));
        for invalid in [
            br#"{}"#.as_slice(),
            br#"{"brightness":"128"}"#.as_slice(),
            br#"{"brightness":true}"#.as_slice(),
        ] {
            assert!(parse_imaging_values(invalid).is_err());
        }
    }

    #[test]
    fn imaging_get_returns_the_canonical_dynamic_range_envelope() {
        let root = task_temp("get");
        let state = root.join("imaging.json");
        fs::write(
            &state,
            br#"{"fields":{"brightness":{"supported":true,"min":0,"max":255,"value":128},"defog":{"supported":false,"min":0,"max":0,"value":null}}}"#,
        )
        .unwrap();
        let backend = PrudyntBackend::new(CameraPaths {
            imaging_state: state,
            ..CameraPaths::default()
        });

        let response = backend.imaging().unwrap();
        let value = json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("code"),
            Some(&Value::Number("200".to_owned()))
        );
        assert_eq!(
            value.get_path("result").and_then(Value::as_str),
            Some("success")
        );
        assert_eq!(
            value.get_path("message.fields.brightness.max"),
            Some(&Value::Number("255".to_owned()))
        );
        assert_eq!(
            value
                .get_path("message.fields.defog.supported")
                .and_then(Value::as_bool),
            Some(false)
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn imaging_post_writes_one_normalized_fifo_command_and_returns_the_envelope() {
        let root = task_temp("post");
        let state = root.join("imaging.json");
        let control = root.join("imagingctl");
        fs::write(
            &state,
            br#"{"fields":{"brightness":{"supported":true,"min":0,"max":255,"value":128}}}"#,
        )
        .unwrap();
        make_fifo(&control);
        // Keep a reader open before the production path attempts its
        // non-blocking FIFO write. A spawned blocking reader has a scheduling
        // race with that write and can fail spuriously with ENXIO on Linux.
        let mut reader = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&control)
            .unwrap();
        let backend = PrudyntBackend::new(CameraPaths {
            imaging_state: state,
            imaging_ctl: control,
            ..CameraPaths::default()
        });

        let response = backend.update_imaging(br#"{"brightness":255}"#).unwrap();
        let expected = b"SET brightness=1.0000\n";
        let mut command = vec![0; expected.len()];
        reader.read_exact(&mut command).unwrap();
        assert_eq!(&command, expected);
        let value = json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("code"),
            Some(&Value::Number("200".to_owned()))
        );
        assert!(matches!(
            backend.update_imaging(br#"{"unknown":1}"#),
            Err(BackendError::Protocol)
        ));
        fs::remove_dir_all(root).unwrap();
    }
}
