use crate::MAX_SNAPSHOT_BYTES;

const _: () = assert!(MAX_SNAPSHOT_BYTES <= super::mqtt::MAX_PAYLOAD_BYTES);
