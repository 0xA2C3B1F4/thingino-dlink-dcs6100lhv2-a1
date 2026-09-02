use super::*;

impl PrudyntBackend {
    pub(super) fn files(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        if let Some(path) = query_param(target, "cd")? {
            if method != "GET" || !body.is_empty() {
                return Err(BackendError::Protocol);
            }
            return file_directory(&path, &self.paths.media_roots);
        }
        if let Some(path) = query_param(target, "rm")? {
            if method != "POST" || !body.is_empty() {
                return Err(BackendError::Protocol);
            }
            let path = validated_absolute_path(&path)?;
            let parent = path.parent().ok_or(BackendError::Protocol)?;
            let resolved_parent =
                fs::canonicalize(parent).map_err(|_| BackendError::Unavailable)?;
            if !path_is_within_roots(&resolved_parent, &self.paths.media_roots) {
                return Err(BackendError::Protocol);
            }
            let metadata = path
                .symlink_metadata()
                .map_err(|_| BackendError::Unavailable)?;
            if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
                return Err(BackendError::Protocol);
            }
            fs::remove_file(path).map_err(|_| BackendError::Unavailable)?;
            return json_response(object([("result", Value::String("ok".to_owned()))]));
        }
        // Large downloads are intentionally kept off the bounded Rust workers. The
        // media ingress handles these after the same Control authorization preflight.
        if query_param(target, "dl")?.is_some() || query_param(target, "play")?.is_some() {
            return Err(BackendError::Unavailable);
        }
        Err(BackendError::Protocol)
    }
    pub(super) fn text_file(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let path = query_param(target, "file")?.ok_or(BackendError::Protocol)?;
        let path = canonical_media_path(&path, &self.paths.media_roots)?;
        let metadata = path
            .symlink_metadata()
            .map_err(|_| BackendError::Unavailable)?;
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            return Err(BackendError::Protocol);
        }
        match method {
            "GET" if body.is_empty() => {
                let content = read_bounded(&path, FILE_LIMIT)?;
                if content.contains(&0) {
                    return Err(BackendError::Protocol);
                }
                let lines = content.iter().filter(|byte| **byte == b'\n').count() as u64;
                json_response(object([
                    ("file", Value::String(path.to_string_lossy().into_owned())),
                    ("content", Value::String(base64_encode(&content))),
                    ("content_encoding", Value::String("base64".to_owned())),
                    ("size", number(content.len() as u64)),
                    ("lines", number(lines)),
                    ("writable", Value::Bool(!metadata.permissions().readonly())),
                ]))
            }
            "POST" => {
                if body.len() as u64 > FILE_LIMIT || body.contains(&0) {
                    return Err(BackendError::Protocol);
                }
                write_in_place(&path, body)?;
                json_response(object([
                    ("success", Value::Bool(true)),
                    ("file", Value::String(path.to_string_lossy().into_owned())),
                    ("size", number(body.len() as u64)),
                    (
                        "lines",
                        number(body.iter().filter(|byte| **byte == b'\n').count() as u64),
                    ),
                ]))
            }
            _ => Err(BackendError::Protocol),
        }
    }
}
