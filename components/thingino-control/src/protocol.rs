use std::fmt;
use std::time::Instant;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DayNightMode {
    Auto,
    Day,
    Night,
}

impl DayNightMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Day => "day",
            Self::Night => "night",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BackendRoute {
    Health,
    RuntimeMedia,
    Config,
    Snapshot(u8),
    DayNight(DayNightMode),
}

impl BackendRoute {
    pub(crate) fn method(self) -> &'static str {
        match self {
            Self::Health | Self::RuntimeMedia | Self::Config => "GET",
            Self::Snapshot(_) | Self::DayNight(_) => "POST",
        }
    }

    pub(crate) fn path(self) -> &'static str {
        match self {
            Self::Health => "/api/v1/health",
            Self::RuntimeMedia => "/api/v1/runtime/media",
            Self::Config => "/api/v1/config",
            Self::Snapshot(0) => "/api/v1/actions/snapshot?stream_id=0",
            Self::Snapshot(1) => "/api/v1/actions/snapshot?stream_id=1",
            Self::Snapshot(_) => unreachable!("snapshot route validates stream IDs"),
            Self::DayNight(_) => "/api/v1/actions/daynight",
        }
    }

    pub(crate) fn body(self) -> Vec<u8> {
        match self {
            Self::DayNight(mode) => format!("{{\"mode\":\"{}\"}}", mode.as_str()).into_bytes(),
            Self::Health | Self::RuntimeMedia | Self::Config | Self::Snapshot(_) => Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BackendResponse {
    pub content_type: &'static str,
    pub body: Vec<u8>,
}

impl BackendResponse {
    pub fn json(body: Vec<u8>) -> Self {
        Self {
            content_type: "application/json",
            body,
        }
    }

    pub fn jpeg(body: Vec<u8>) -> Self {
        Self {
            content_type: "image/jpeg",
            body,
        }
    }

    pub fn prometheus(body: Vec<u8>) -> Self {
        Self {
            content_type: "text/plain; version=0.0.4",
            body,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BackendError {
    Connection,
    Timeout,
    Protocol,
    Unavailable,
    Busy,
    Unsupported(&'static str),
    PartialApply(&'static str),
    Upstream(u16),
}

impl fmt::Display for BackendError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Connection => formatter.write_str("backend connection failed"),
            Self::Timeout => formatter.write_str("backend timed out"),
            Self::Protocol => formatter.write_str("invalid backend response"),
            Self::Unavailable => formatter.write_str("backend is unavailable"),
            Self::Busy => formatter
                .write_str("another settings change is in progress; retry after it completes"),
            Self::Unsupported(reason) => formatter.write_str(reason),
            Self::PartialApply(reason) => formatter.write_str(reason),
            Self::Upstream(status) => write!(formatter, "backend returned HTTP {status}"),
        }
    }
}

/// File checked by Control before the ingress opens it for streaming.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MediaFileIdentity {
    pub device: u64,
    pub inode: u64,
    pub size: u64,
    pub modified: i64,
    pub modified_nanos: i64,
}
impl MediaFileIdentity {
    pub(crate) fn header(&self) -> String {
        format!(
            "v1 {} {} {} {} {}",
            self.device, self.inode, self.size, self.modified, self.modified_nanos
        )
    }
}

/// Camera-specific backend contract.
///
/// Implementations must return no later than `deadline`. The synchronous
/// server cannot preempt a backend that violates this contract. `HttpBackend`
/// enforces it with connect, read, and write deadlines.
pub trait Backend: Send + Sync + 'static {
    fn request(
        &self,
        route: BackendRoute,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError>;

    fn api_request(
        &self,
        _method: &str,
        _target: &str,
        _body: &[u8],
        _deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        None
    }

    fn update_management_credential(
        &self,
        _username: &str,
        _password: &str,
        _deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        None
    }

    fn media_file_identity(
        &self,
        _target: &str,
        _deadline: Instant,
    ) -> Result<Option<MediaFileIdentity>, BackendError> {
        Ok(None)
    }

    fn authorize_media(&self, _target: &str) -> bool {
        false
    }

    /// Whether shared authentication routes may change persistent credentials.
    /// Existing backends retain their behavior; volatile backends must opt out.
    fn allows_persistent_auth_mutations(&self) -> bool {
        true
    }
}
