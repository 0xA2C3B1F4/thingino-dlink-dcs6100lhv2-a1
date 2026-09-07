#[cfg(target_os = "linux")]
use super::super::{c_char, c_int, c_void};

pub(in crate::camera) const SIGHUP: i32 = 1;
pub(in crate::camera) const RB_AUTOBOOT: i32 = 0x0123_4567;
pub(in crate::camera) const FILE_LIMIT: u64 = 128 * 1024;
#[cfg(target_os = "linux")]
pub(in crate::camera) const AF_UNIX: c_int = 1;
// Linux/MIPS follows the MIPS ABI where SOCK_DGRAM is 1 and SOCK_STREAM is 2;
// the values are reversed on the other Linux targets used by host tests.
#[cfg(all(target_os = "linux", target_arch = "mips"))]
pub(in crate::camera) const SOCK_DGRAM: c_int = 1;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
pub(in crate::camera) const SOCK_DGRAM: c_int = 2;
#[cfg(target_os = "linux")]
pub(in crate::camera) const SOCK_CLOEXEC: c_int = 0x80000;
#[cfg(target_os = "linux")]
pub(in crate::camera) const MSG_DONTWAIT: c_int = 0x40;
#[cfg(any(target_os = "linux", test))]
pub(in crate::camera) const EINTR: i32 = 4;
#[cfg(target_os = "linux")]
pub(in crate::camera) const EAGAIN: i32 = 11;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
pub(in crate::camera) const O_NONBLOCK: i32 = 0x80;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
pub(in crate::camera) const O_NONBLOCK: i32 = 0x800;

#[cfg(target_os = "linux")]
pub(in crate::camera) const O_NOFOLLOW: i32 = 0x20000;
#[cfg(target_os = "macos")]
pub(in crate::camera) const O_NOFOLLOW: i32 = 0x100;
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub(in crate::camera) const O_NOFOLLOW: i32 = 0;

unsafe extern "C" {
    pub(in crate::camera) fn kill(pid: i32, signal: i32) -> i32;
    pub(in crate::camera) fn clock_settime(clock_id: i32, value: *const Timespec) -> i32;
    pub(in crate::camera) fn reboot(command: i32) -> i32;
    pub(in crate::camera) fn sync();
    #[cfg(target_os = "linux")]
    pub(in crate::camera) fn statvfs(
        path: *const std::os::raw::c_char,
        output: *mut Statvfs,
    ) -> i32;
    #[cfg(target_os = "linux")]
    #[link_name = "socket"]
    pub(in crate::camera) fn libc_socket(domain: c_int, kind: c_int, protocol: c_int) -> c_int;
    #[cfg(target_os = "linux")]
    #[link_name = "bind"]
    pub(in crate::camera) fn libc_bind(fd: c_int, address: *const SockaddrUn, length: u32)
    -> c_int;
    #[cfg(target_os = "linux")]
    #[link_name = "connect"]
    pub(in crate::camera) fn libc_connect(
        fd: c_int,
        address: *const SockaddrUn,
        length: u32,
    ) -> c_int;
    #[cfg(target_os = "linux")]
    #[link_name = "send"]
    pub(in crate::camera) fn libc_send(
        fd: c_int,
        data: *const c_void,
        length: usize,
        flags: c_int,
    ) -> isize;
    #[cfg(target_os = "linux")]
    #[link_name = "recv"]
    pub(in crate::camera) fn libc_recv(
        fd: c_int,
        data: *mut c_void,
        length: usize,
        flags: c_int,
    ) -> isize;
    #[cfg(target_os = "linux")]
    #[link_name = "close"]
    pub(in crate::camera) fn libc_close(fd: c_int) -> c_int;
    #[cfg(target_os = "linux")]
    pub(in crate::camera) fn getpid() -> c_int;
}

#[cfg(target_os = "linux")]
#[repr(C)]
pub(in crate::camera) struct SockaddrUn {
    pub(in crate::camera) family: u16,
    pub(in crate::camera) path: [c_char; 108],
}

#[repr(C)]
pub(in crate::camera) struct Timespec {
    pub(in crate::camera) seconds: i64,
    pub(in crate::camera) nanoseconds: i64,
}

#[cfg(target_os = "linux")]
#[repr(C)]
#[derive(Default)]
pub(in crate::camera) struct Statvfs {
    pub(in crate::camera) block_size: std::os::raw::c_ulong,
    pub(in crate::camera) fragment_size: std::os::raw::c_ulong,
    pub(in crate::camera) blocks: std::os::raw::c_ulong,
    pub(in crate::camera) blocks_free: std::os::raw::c_ulong,
    pub(in crate::camera) blocks_available: std::os::raw::c_ulong,
    pub(in crate::camera) files: std::os::raw::c_ulong,
    pub(in crate::camera) files_free: std::os::raw::c_ulong,
    pub(in crate::camera) files_available: std::os::raw::c_ulong,
    pub(in crate::camera) filesystem_id: std::os::raw::c_ulong,
    #[cfg(target_pointer_width = "32")]
    pub(in crate::camera) unused: i32,
    pub(in crate::camera) flags: std::os::raw::c_ulong,
    pub(in crate::camera) name_max: std::os::raw::c_ulong,
    pub(in crate::camera) filesystem_type: u32,
    pub(in crate::camera) spare: [i32; 5],
}

#[cfg(all(target_os = "linux", target_pointer_width = "32"))]
const _: [(); 72] = [(); std::mem::size_of::<Statvfs>()];
#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const _: [(); 112] = [(); std::mem::size_of::<Statvfs>()];
