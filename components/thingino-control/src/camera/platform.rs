//! Platform operations shared by the camera modules.
//! Keep camera-facing names here; target ABI and resource ownership live below.

mod abi;
mod file_io;
mod logs;
mod socket_io;
mod sysv;

pub(super) use abi::*;
pub(super) use file_io::*;
pub(super) use logs::*;
pub(super) use socket_io::*;
pub(super) use sysv::read_busybox_syslog;
