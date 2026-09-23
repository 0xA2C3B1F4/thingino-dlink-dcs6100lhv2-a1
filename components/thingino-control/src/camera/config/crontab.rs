use super::super::*;

const MAX_CRONTAB_BYTES: usize = 16 * 1024;
const MAX_CRONTAB_LINE_BYTES: usize = 1_024;

impl HostBackend {
    /// Return the root user's crontab as a bounded, editable text document.
    ///
    /// The file is read directly.  In particular, this route does not invoke
    /// BusyBox `crontab` or any other helper, so request handling cannot create
    /// a child process or a temporary file.
    pub(in crate::camera) fn crontab(&self) -> Result<BackendResponse, BackendError> {
        let content = read_bounded(&self.paths.crontab, MAX_CRONTAB_BYTES as u64)?;
        validate_crontab(&content)?;
        let content = String::from_utf8(content).map_err(|_| BackendError::Protocol)?;
        json_response(object([
            ("content", Value::String(content)),
            ("max_bytes", number(MAX_CRONTAB_BYTES as u64)),
        ]))
    }

    /// Validate and persist the root user's crontab from canonical JSON.
    ///
    /// The accepted request is exactly `{ "content": "..." }`.  Persistence
    /// uses the existing bounded in-place writer; no shell, child process, or
    /// request-time temporary file is involved.
    pub(in crate::camera) fn update_crontab(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.len() != 1 || !fields.contains_key("content") {
            return Err(BackendError::Protocol);
        }
        let content = fields
            .get("content")
            .and_then(Value::as_str)
            .ok_or(BackendError::Protocol)?;
        validate_crontab(content.as_bytes())?;
        write_in_place(&self.paths.crontab, content.as_bytes())?;
        json_response(object([("status", Value::String("ok".to_owned()))]))
    }
}

fn validate_crontab(content: &[u8]) -> Result<(), BackendError> {
    if content.len() > MAX_CRONTAB_BYTES || content.contains(&0) || content.contains(&b'\r') {
        return Err(BackendError::Protocol);
    }
    let content = std::str::from_utf8(content).map_err(|_| BackendError::Protocol)?;
    for line in content.split('\n') {
        if line.len() > MAX_CRONTAB_LINE_BYTES {
            return Err(BackendError::Protocol);
        }
        validate_crontab_line(line)?;
    }
    Ok(())
}

fn validate_crontab_line(line: &str) -> Result<(), BackendError> {
    let line = line.trim_matches(|character: char| character == ' ' || character == '\t');
    if line.is_empty() || line.starts_with('#') {
        return Ok(());
    }
    if is_environment_assignment(line) {
        return Ok(());
    }
    if let Some(rest) = line.strip_prefix('@') {
        let Some((schedule, command)) = rest.split_once(char::is_whitespace) else {
            return Err(BackendError::Protocol);
        };
        if !matches!(
            schedule,
            "reboot"
                | "yearly"
                | "annually"
                | "monthly"
                | "weekly"
                | "daily"
                | "midnight"
                | "hourly"
        ) || command.trim().is_empty()
        {
            return Err(BackendError::Protocol);
        }
        return Ok(());
    }

    // The D-Link profile uses the traditional five-field crontab syntax.
    // `split_whitespace` gives us the five schedule fields and at least one
    // command token; the command itself may contain arbitrary further text.
    let mut fields = line.split_whitespace();
    for (index, limits) in [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
        .into_iter()
        .enumerate()
    {
        let field = fields.next().ok_or(BackendError::Protocol)?;
        validate_schedule_field(field, index, limits.0, limits.1)?;
    }
    if fields.next().is_none() {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn validate_schedule_field(
    field: &str,
    index: usize,
    minimum: u8,
    maximum: u8,
) -> Result<(), BackendError> {
    if field.is_empty() {
        return Err(BackendError::Protocol);
    }
    for item in field.split(',') {
        if item.is_empty() {
            return Err(BackendError::Protocol);
        }
        let (range, step) = item
            .split_once('/')
            .map_or((item, None), |(range, step)| (range, Some(step)));
        if range.contains('/')
            || step.is_some_and(|value| {
                value
                    .parse::<u8>()
                    .map_or(true, |value| value == 0 || value > maximum)
            })
        {
            return Err(BackendError::Protocol);
        }
        if range == "*" {
            continue;
        }
        if let Some((start, end)) = range.split_once('-') {
            let start = schedule_value(start, index, minimum, maximum)?;
            let end = schedule_value(end, index, minimum, maximum)?;
            if start > end {
                return Err(BackendError::Protocol);
            }
        } else {
            schedule_value(range, index, minimum, maximum)?;
        }
    }
    Ok(())
}

fn schedule_value(value: &str, index: usize, minimum: u8, maximum: u8) -> Result<u8, BackendError> {
    let numeric = value.parse::<u8>().ok().or_else(|| {
        let value = value.to_ascii_lowercase();
        let names: &[&str] = match index {
            3 => &[
                "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
            ],
            4 => &["sun", "mon", "tue", "wed", "thu", "fri", "sat"],
            _ => return None,
        };
        names
            .iter()
            .position(|candidate| *candidate == value)
            .map(|position| {
                if index == 3 {
                    position as u8 + 1
                } else {
                    position as u8
                }
            })
    });
    numeric
        .filter(|value| *value >= minimum && *value <= maximum)
        .ok_or(BackendError::Protocol)
}

fn is_environment_assignment(line: &str) -> bool {
    let Some((name, _value)) = line.split_once('=') else {
        return false;
    };
    let mut characters = name.chars();
    let Some(first) = characters.next() else {
        return false;
    };
    (first == '_' || first.is_ascii_alphabetic())
        && characters.all(|character| character == '_' || character.is_ascii_alphanumeric())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = PathBuf::from(root).join(format!(
            "thingino-control-crontab-{}-{}-{name}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn validator_accepts_comments_empty_lines_environment_and_cron_commands() {
        let valid = b"# camera maintenance\n\nSHELL=/bin/sh\nMAILTO=\n@reboot /usr/bin/logger started\n*/5 * * * * /usr/bin/logger camera\n0 0 1,15 * 1-5 /bin/echo hello world\n";
        assert!(validate_crontab(valid).is_ok());
    }

    #[test]
    fn validator_rejects_invalid_lines_and_bytes() {
        for invalid in [
            b"* * * *\n".as_slice(),
            b"* * * * *\n".as_slice(),
            b"* * * * * /bin/echo\r\n".as_slice(),
            b"BAD-NAME=value\n".as_slice(),
            b"X =value\n".as_slice(),
            b"@reboot\n".as_slice(),
            b"@unknown /bin/true\n".as_slice(),
            b"99 * * * * /bin/true\n".as_slice(),
            b"* 24 * * * /bin/true\n".as_slice(),
            b"*/0 * * * * /bin/true\n".as_slice(),
            b"1-0 * * * * /bin/true\n".as_slice(),
            b"* * * foo * /bin/true\n".as_slice(),
            b"\0\n".as_slice(),
            b"\xff\n".as_slice(),
        ] {
            assert!(validate_crontab(invalid).is_err(), "{invalid:?}");
        }
        assert!(validate_crontab(b"0 6 * jan mon-fri /bin/true\n").is_ok());
    }

    #[test]
    fn validator_rejects_oversized_document_and_line() {
        let document = vec![b'\n'; MAX_CRONTAB_BYTES + 1];
        assert!(validate_crontab(&document).is_err());

        let line = format!("{}\n", "x".repeat(MAX_CRONTAB_LINE_BYTES + 1));
        assert!(validate_crontab(line.as_bytes()).is_err());
    }

    #[test]
    fn get_and_post_use_canonical_shape_and_in_place_file() {
        let root = task_temp("roundtrip");
        let crontab = root.join("root");
        let initial = b"# initial\n*/10 * * * * /bin/echo initial\n";
        fs::write(&crontab, initial).unwrap();
        let backend = HostBackend::new(CameraPaths {
            crontab: crontab.clone(),
            ..CameraPaths::default()
        });

        let response = backend.crontab().unwrap();
        let value = json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("content"),
            Some(&Value::String(
                String::from_utf8_lossy(initial).into_owned()
            ))
        );
        assert_eq!(
            value.get_path("max_bytes"),
            Some(&number(MAX_CRONTAB_BYTES as u64))
        );

        let updated = b"SHELL=/bin/sh\n0 * * * * /bin/echo updated\n";
        let request = object([(
            "content",
            Value::String(String::from_utf8_lossy(updated).into_owned()),
        )])
        .to_json();
        let response = backend.update_crontab(request.as_bytes()).unwrap();
        let value = json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("status"),
            Some(&Value::String("ok".to_owned()))
        );
        assert_eq!(fs::read(&crontab).unwrap(), updated);
        assert!(
            backend
                .update_crontab(br#"{"content":"ok","extra":true}"#)
                .is_err()
        );
        fs::remove_dir_all(root).unwrap();
    }
}
