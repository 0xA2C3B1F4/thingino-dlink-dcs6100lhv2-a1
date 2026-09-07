use std::path::PathBuf;

pub const MOTION_EVENT_VERSION: u8 = 1;
pub const MAX_ARTIFACT_ID_BYTES: usize = 128;
pub const MAX_ARTIFACT_CONTENT_TYPE_BYTES: usize = 96;
pub const MAX_ARTIFACT_PATH_BYTES: usize = 255;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MotionState {
    Active,
    Inactive,
    Resync,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[allow(dead_code)]
pub enum MediaArtifactKind {
    Snapshot,
    Clip,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MediaArtifact {
    pub kind: MediaArtifactKind,
    pub id: String,
    pub local_path: Option<PathBuf>,
    pub content_type: String,
    pub size_bytes: u64,
    pub created_unix_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MotionEvent {
    pub version: u8,
    pub sequence: u64,
    pub state: MotionState,
    pub channel: u8,
    pub monotonic_ms: u64,
    pub occurred_unix_ms: Option<u64>,
    pub snapshot: Option<MediaArtifact>,
    pub clip: Option<MediaArtifact>,
}

impl MotionEvent {
    pub fn has_bounded_metadata(&self) -> bool {
        self.version == MOTION_EVENT_VERSION
            && self.channel <= 1
            && self
                .snapshot
                .as_ref()
                .is_none_or(MediaArtifact::has_bounded_metadata)
            && self
                .clip
                .as_ref()
                .is_none_or(MediaArtifact::has_bounded_metadata)
    }
}

impl MediaArtifact {
    pub fn has_bounded_metadata(&self) -> bool {
        !self.id.is_empty()
            && self.id.len() <= MAX_ARTIFACT_ID_BYTES
            && !self.content_type.is_empty()
            && self.content_type.len() <= MAX_ARTIFACT_CONTENT_TYPE_BYTES
            && self.local_path.as_ref().is_none_or(|path| {
                path.is_absolute()
                    && path.as_os_str().as_encoded_bytes().len() <= MAX_ARTIFACT_PATH_BYTES
                    && path.symlink_metadata().is_ok_and(|metadata| {
                        metadata.is_file()
                            && !metadata.file_type().is_symlink()
                            && metadata.len() == self.size_bytes
                    })
            })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[allow(dead_code)]
pub enum MotionEventDisposition {
    Queued,
    Coalesced,
    Dropped,
    Disabled,
}

pub trait MotionEventSink: Send + Sync {
    fn try_send_motion(&self, event: MotionEvent) -> MotionEventDisposition;
}

#[derive(Debug, Default)]
pub struct DisabledMotionEventSink;

impl MotionEventSink for DisabledMotionEventSink {
    fn try_send_motion(&self, _event: MotionEvent) -> MotionEventDisposition {
        MotionEventDisposition::Disabled
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn disabled_sink_never_queues_or_blocks() {
        let event = MotionEvent {
            version: 1,
            sequence: 7,
            state: MotionState::Active,
            channel: 0,
            monotonic_ms: 12_345,
            occurred_unix_ms: Some(1_787_500_000_000),
            snapshot: None,
            clip: None,
        };
        assert_eq!(
            DisabledMotionEventSink.try_send_motion(event),
            MotionEventDisposition::Disabled
        );
    }

    #[test]
    fn artifact_metadata_is_bounded_and_local() {
        let root = PathBuf::from(std::env::var_os("TMPDIR").expect("TMPDIR is required"))
            .join(format!("thingino-motion-artifact-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("motion-7.mp4");
        fs::write(&path, vec![0_u8; 1_024]).unwrap();
        let valid = MediaArtifact {
            kind: MediaArtifactKind::Clip,
            id: "motion-7".to_owned(),
            local_path: Some(path),
            content_type: "video/mp4".to_owned(),
            size_bytes: 1_024,
            created_unix_ms: 1_787_500_000_000,
        };
        assert!(valid.has_bounded_metadata());
        assert!(
            !MediaArtifact {
                id: "x".repeat(MAX_ARTIFACT_ID_BYTES + 1),
                ..valid.clone()
            }
            .has_bounded_metadata()
        );
        assert!(
            !MediaArtifact {
                local_path: Some("relative.mp4".into()),
                ..valid.clone()
            }
            .has_bounded_metadata()
        );
        assert!(
            !MediaArtifact {
                size_bytes: valid.size_bytes + 1,
                ..valid
            }
            .has_bounded_metadata()
        );
        fs::remove_dir_all(root).unwrap();
    }
}
