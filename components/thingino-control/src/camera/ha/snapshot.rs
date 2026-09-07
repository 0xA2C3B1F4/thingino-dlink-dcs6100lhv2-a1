use super::super::*;

const _: () = assert!(MAX_SNAPSHOT_BYTES <= super::mqtt::MAX_PAYLOAD_BYTES);

pub(super) fn capture(backend: &PrudyntBackend, stream_id: u8) -> Result<Vec<u8>, BackendError> {
    if stream_id > 1 {
        return Err(BackendError::Protocol);
    }
    let response = backend.snapshot(stream_id, Instant::now() + Duration::from_secs(3))?;
    if response.content_type != "image/jpeg"
        || response.body.is_empty()
        || response.body.len() > MAX_SNAPSHOT_BYTES
    {
        return Err(BackendError::Protocol);
    }
    Ok(response.body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_limit_matches_the_control_contract() {
        assert_eq!(MAX_SNAPSHOT_BYTES, 2 * 1024 * 1024);
        let backend = PrudyntBackend::new(CameraPaths::default());
        assert_eq!(capture(&backend, 2), Err(BackendError::Protocol));
    }
}
