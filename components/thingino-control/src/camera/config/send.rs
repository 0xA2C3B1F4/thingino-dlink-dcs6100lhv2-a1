use super::super::*;

impl PrudyntBackend {
    pub(in crate::camera) fn send2_config(&self) -> Result<BackendResponse, BackendError> {
        let prudynt = read_json_or_empty(&self.paths.prudynt_config)?;
        let send2 = read_json_or_empty(&self.paths.send2_config)?;
        let mut response = BTreeMap::new();
        response.insert(
            "motion".to_owned(),
            prudynt
                .get_path("motion")
                .cloned()
                .unwrap_or_else(|| Value::Object(BTreeMap::new())),
        );
        for domain in [
            "email", "ftp", "telegram", "gotify", "mqtt", "webhook", "storage", "ntfy", "gphotos",
        ] {
            let mut value = send2
                .get_path(domain)
                .cloned()
                .unwrap_or_else(|| Value::Object(BTreeMap::new()));
            mask_secret_fields(&mut value);
            response.insert(domain.to_owned(), value);
        }
        response.insert(
            "meta".to_owned(),
            object([(
                "mounts",
                Value::Array(storage_mounts(&self.paths.proc_mounts)),
            )]),
        );
        json_response(Value::Object(response))
    }
    pub(in crate::camera) fn update_send2_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let update = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let domains = update.as_object().ok_or(BackendError::Protocol)?;
        if domains.is_empty()
            || domains.keys().any(|key| {
                !matches!(
                    key.as_str(),
                    "motion"
                        | "email"
                        | "ftp"
                        | "telegram"
                        | "gotify"
                        | "mqtt"
                        | "webhook"
                        | "storage"
                        | "ntfy"
                        | "gphotos"
                )
            })
        {
            return Err(BackendError::Protocol);
        }
        if domains.values().any(|value| value.as_object().is_none()) {
            return Err(BackendError::Protocol);
        }
        validate_send2_update(domains)?;
        if let Some(motion) = domains.get("motion") {
            let live = object([("motion", motion.clone())]);
            self.update_prudynt_config(live.to_json().as_bytes(), deadline)?;
        }
        let mut send2_update = BTreeMap::new();
        for (domain, value) in domains {
            if domain != "motion" {
                let mut value = value.clone();
                remove_unchanged_secret_fields(&mut value);
                send2_update.insert(domain.clone(), value);
            }
        }
        if !send2_update.is_empty() {
            self.merge_config_file(
                &self.paths.send2_config,
                Value::Object(send2_update).to_json().as_bytes(),
            )?;
        }
        Ok(BackendResponse::json(
            b"{\"result\":\"success\",\"message\":\"Settings saved\"}\n".to_vec(),
        ))
    }
}

fn validate_send2_update(domains: &BTreeMap<String, Value>) -> Result<(), BackendError> {
    let Some(gotify) = domains.get("gotify").and_then(Value::as_object) else {
        return Ok(());
    };
    let Some(extras) = gotify.get("extras") else {
        return Ok(());
    };
    let Value::String(extras) = extras else {
        return Err(BackendError::Protocol);
    };
    if extras.trim().is_empty() {
        return Ok(());
    }
    if json::parse(extras.as_bytes())
        .ok()
        .and_then(|value| value.as_object().cloned())
        .is_none()
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        fs::create_dir_all(&root).unwrap();
        let path = PathBuf::from(root).join(format!(
            "ta-send-{}-{}-{name}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    fn update(source: &[u8]) -> BTreeMap<String, Value> {
        json::parse(source).unwrap().as_object().unwrap().clone()
    }

    #[test]
    fn gotify_extras_keeps_the_legacy_json_encoded_object_contract() {
        for valid in [
            br#"{"gotify":{"extras":""}}"#.as_slice(),
            br#"{"gotify":{"extras":"{\"client::display\":{\"contentType\":\"text/markdown\"}}"}}"#,
            br#"{"email":{"host":"mail.example"}}"#,
        ] {
            assert!(validate_send2_update(&update(valid)).is_ok());
        }
        for invalid in [
            br#"{"gotify":{"extras":{}}}"#.as_slice(),
            br#"{"gotify":{"extras":"[]"}}"#,
            br#"{"gotify":{"extras":"not-json"}}"#,
        ] {
            assert!(matches!(
                validate_send2_update(&update(invalid)),
                Err(BackendError::Protocol)
            ));
        }
    }

    #[test]
    fn motion_recorder_fields_use_the_lifecycle_aware_prudynt_config_path() {
        let root = task_temp("send-motion-recorder-lifecycle");
        let prudynt = root.join("prudynt.json");
        let send2 = root.join("send2.json");
        fs::write(
            &prudynt,
            b"{\"motion\":{\"enabled\":true,\"send2storage\":false,\"sensitivity\":1,\"video_length\":10}}\n",
        )
        .unwrap();
        fs::write(
            &send2,
            b"{\"storage\":{\"device_path\":\"\",\"mount\":\"\",\"send_photo\":false,\"send_video\":false}}\n",
        )
        .unwrap();
        let backend = PrudyntBackend::new(CameraPaths {
            prudynt_config: prudynt.clone(),
            prudynt_socket: root.join("absent.sock"),
            send2_config: send2.clone(),
            ..CameraPaths::default()
        });

        backend
            .update_send2_config(
                br#"{"motion":{"send2storage":true,"video_length":5},"storage":{"device_path":"thingino/motion","mount":"/media","send_photo":false,"send_video":true}}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();

        let stored_prudynt = json::parse(&fs::read(&prudynt).unwrap()).unwrap();
        assert_eq!(
            stored_prudynt.get_path("motion.send2storage"),
            Some(&Value::Bool(true))
        );
        assert_eq!(
            stored_prudynt.get_path("motion.video_length"),
            Some(&number(5))
        );
        assert_eq!(
            stored_prudynt.get_path("motion.sensitivity"),
            Some(&number(1))
        );
        let stored_send2 = json::parse(&fs::read(&send2).unwrap()).unwrap();
        assert_eq!(
            stored_send2.get_path("storage.mount"),
            Some(&Value::String("/media".to_owned()))
        );
        assert_eq!(
            stored_send2.get_path("storage.device_path"),
            Some(&Value::String("thingino/motion".to_owned()))
        );
        assert_eq!(
            stored_send2.get_path("storage.send_video"),
            Some(&Value::Bool(true))
        );
        fs::remove_dir_all(root).unwrap();
    }
}
