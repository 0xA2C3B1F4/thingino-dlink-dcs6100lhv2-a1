use super::*;

pub(super) const SIGHUP: i32 = 1;
pub(super) const RB_AUTOBOOT: i32 = 0x0123_4567;
pub(super) const FILE_LIMIT: u64 = 128 * 1024;
#[cfg(target_os = "linux")]
pub(super) const AF_UNIX: c_int = 1;
// Linux/MIPS follows the MIPS ABI where SOCK_DGRAM is 1 and SOCK_STREAM is 2;
// the values are reversed on the other Linux targets used by host tests.
#[cfg(all(target_os = "linux", target_arch = "mips"))]
pub(super) const SOCK_DGRAM: c_int = 1;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
pub(super) const SOCK_DGRAM: c_int = 2;
#[cfg(target_os = "linux")]
pub(super) const SOCK_CLOEXEC: c_int = 0x80000;
#[cfg(target_os = "linux")]
pub(super) const MSG_DONTWAIT: c_int = 0x40;
#[cfg(any(target_os = "linux", test))]
pub(super) const EINTR: i32 = 4;
#[cfg(target_os = "linux")]
pub(super) const EAGAIN: i32 = 11;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
pub(super) const O_NONBLOCK: i32 = 0x80;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
pub(super) const O_NONBLOCK: i32 = 0x800;

#[cfg(target_os = "linux")]
pub(super) const O_NOFOLLOW: i32 = 0x20000;
#[cfg(target_os = "macos")]
pub(super) const O_NOFOLLOW: i32 = 0x100;
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub(super) const O_NOFOLLOW: i32 = 0;

unsafe extern "C" {
    pub(super) fn kill(pid: i32, signal: i32) -> i32;
    pub(super) fn clock_settime(clock_id: i32, value: *const Timespec) -> i32;
    pub(super) fn reboot(command: i32) -> i32;
    pub(super) fn sync();
    #[cfg(target_os = "linux")]
    pub(super) fn statvfs(path: *const std::os::raw::c_char, output: *mut Statvfs) -> i32;
    #[cfg(target_os = "linux")]
    #[link_name = "socket"]
    pub(super) fn libc_socket(domain: c_int, kind: c_int, protocol: c_int) -> c_int;
    #[cfg(target_os = "linux")]
    #[link_name = "bind"]
    pub(super) fn libc_bind(fd: c_int, address: *const SockaddrUn, length: u32) -> c_int;
    #[cfg(target_os = "linux")]
    #[link_name = "connect"]
    pub(super) fn libc_connect(fd: c_int, address: *const SockaddrUn, length: u32) -> c_int;
    #[cfg(target_os = "linux")]
    #[link_name = "send"]
    pub(super) fn libc_send(fd: c_int, data: *const c_void, length: usize, flags: c_int) -> isize;
    #[cfg(target_os = "linux")]
    #[link_name = "recv"]
    pub(super) fn libc_recv(fd: c_int, data: *mut c_void, length: usize, flags: c_int) -> isize;
    #[cfg(target_os = "linux")]
    #[link_name = "close"]
    pub(super) fn libc_close(fd: c_int) -> c_int;
    #[cfg(target_os = "linux")]
    pub(super) fn getpid() -> c_int;
}

#[cfg(target_os = "linux")]
#[repr(C)]
pub(super) struct SockaddrUn {
    pub(super) family: u16,
    pub(super) path: [c_char; 108],
}

#[repr(C)]
pub(super) struct Timespec {
    pub(super) seconds: i64,
    pub(super) nanoseconds: i64,
}

#[cfg(target_os = "linux")]
#[repr(C)]
#[derive(Default)]
pub(super) struct Statvfs {
    pub(super) block_size: std::os::raw::c_ulong,
    pub(super) fragment_size: std::os::raw::c_ulong,
    pub(super) blocks: std::os::raw::c_ulong,
    pub(super) blocks_free: std::os::raw::c_ulong,
    pub(super) blocks_available: std::os::raw::c_ulong,
    pub(super) files: std::os::raw::c_ulong,
    pub(super) files_free: std::os::raw::c_ulong,
    pub(super) files_available: std::os::raw::c_ulong,
    pub(super) filesystem_id: std::os::raw::c_ulong,
    #[cfg(target_pointer_width = "32")]
    pub(super) unused: i32,
    pub(super) flags: std::os::raw::c_ulong,
    pub(super) name_max: std::os::raw::c_ulong,
    pub(super) filesystem_type: u32,
    pub(super) spare: [i32; 5],
}

#[cfg(all(target_os = "linux", target_pointer_width = "32"))]
const _: [(); 72] = [(); std::mem::size_of::<Statvfs>()];
#[cfg(all(target_os = "linux", target_pointer_width = "64"))]
const _: [(); 112] = [(); std::mem::size_of::<Statvfs>()];
pub(super) fn write_fifo(path: &Path, content: &[u8]) -> Result<(), BackendError> {
    let metadata = path
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if !metadata.file_type().is_fifo() {
        return Err(BackendError::Protocol);
    }
    #[cfg(target_os = "linux")]
    let flags = O_NOFOLLOW | O_NONBLOCK;
    #[cfg(not(target_os = "linux"))]
    let flags = O_NOFOLLOW;
    let mut file = OpenOptions::new()
        .write(true)
        .custom_flags(flags)
        .open(path)
        .map_err(|_| BackendError::Unavailable)?;
    file.write_all(content)
        .map_err(|_| BackendError::Unavailable)
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
const SYSLOG_HEADER_LEN: usize = 2 * std::mem::size_of::<i32>();
const SYSLOG_LIMIT: usize = 64 * 1024;
#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
const BUSYBOX_LOCK_TIMEOUT: Duration = Duration::from_millis(100);

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
#[repr(C)]
struct SemBuf {
    sem_num: std::os::raw::c_ushort,
    sem_op: std::os::raw::c_short,
    sem_flg: std::os::raw::c_short,
}

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
const _: [(); 6] = [(); std::mem::size_of::<SemBuf>()];

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
#[repr(C)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SysvTimespec {
    seconds: std::os::raw::c_long,
    nanoseconds: std::os::raw::c_long,
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
const _: [(); 16] = [(); std::mem::size_of::<SysvTimespec>()];
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
const _: [(); 8] = [(); std::mem::align_of::<SysvTimespec>()];
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const _: [(); 8] = [(); std::mem::size_of::<SysvTimespec>()];
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const _: [(); 4] = [(); std::mem::align_of::<SysvTimespec>()];

// These are opaque output buffers for shmctl(IPC_STAT), not hand-written
// models of libc's fields. Their size, alignment, and shm_segsz offset are
// verified against the matching C headers by the task's amd64 and MIPS/O32
// ABI probes.
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
#[repr(C, align(8))]
struct ShmidDs {
    bytes: [u8; 112],
}

#[cfg(all(target_os = "linux", target_arch = "mips"))]
#[repr(C, align(4))]
struct ShmidDs {
    bytes: [u8; 72],
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
const _: [(); 112] = [(); std::mem::size_of::<ShmidDs>()];
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
const _: [(); 8] = [(); std::mem::align_of::<ShmidDs>()];
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const _: [(); 72] = [(); std::mem::size_of::<ShmidDs>()];
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const _: [(); 4] = [(); std::mem::align_of::<ShmidDs>()];

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
const SHMID_DS_SEGSZ_OFFSET: usize = 48;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const SHMID_DS_SEGSZ_OFFSET: usize = 36;

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
unsafe extern "C" {
    fn shmget(key: c_int, size: usize, flags: c_int) -> c_int;
    fn shmctl(id: c_int, command: c_int, output: *mut ShmidDs) -> c_int;
    fn shmat(id: c_int, address: *const c_void, flags: c_int) -> *mut c_void;
    fn shmdt(address: *const c_void) -> c_int;
    fn semget(key: c_int, count: c_int, flags: c_int) -> c_int;
    fn semop(id: c_int, operations: *mut SemBuf, count: usize) -> c_int;
    fn semtimedop(
        id: c_int,
        operations: *mut SemBuf,
        count: usize,
        timeout: *const SysvTimespec,
    ) -> c_int;
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(super) fn parse_busybox_syslog_ring(
    size: usize,
    tail: usize,
    data: &[u8],
) -> Result<Vec<u8>, BackendError> {
    if size == 0 || size > SYSLOG_LIMIT || tail >= size || data.len() < size {
        return Err(BackendError::Protocol);
    }
    let mut current = tail;
    current += data[current..size]
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(size - current);
    if current >= size {
        current = data[..tail]
            .iter()
            .position(|byte| *byte == 0)
            .unwrap_or(tail);
        if current == tail {
            return Ok(Vec::new());
        }
    }
    current += 1;
    if current >= size {
        current = 0;
    }

    let mut output = Vec::with_capacity(size);
    let mut traversed = 0usize;
    while current != tail && traversed <= size {
        let end = if current < tail { tail } else { size };
        if let Some(length) = data[current..end].iter().position(|byte| *byte == 0) {
            output.extend_from_slice(&data[current..current + length]);
            traversed = traversed.saturating_add(length + 1);
            current += length + 1;
            if current >= size {
                current = 0;
            }
        } else if current >= tail {
            output.extend_from_slice(&data[current..end]);
            traversed = traversed.saturating_add(end - current);
            current = 0;
        } else {
            return Err(BackendError::Protocol);
        }
        if output.len() > SYSLOG_LIMIT {
            return Err(BackendError::Protocol);
        }
    }
    if current != tail || traversed > size {
        return Err(BackendError::Protocol);
    }
    Ok(output)
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
fn checked_busybox_layout(
    segment_size: usize,
    data_size: i32,
    tail: i32,
) -> Result<(usize, usize), BackendError> {
    let capacity = segment_size
        .checked_sub(SYSLOG_HEADER_LEN)
        .ok_or(BackendError::Protocol)?;
    if data_size <= 0 || tail < 0 {
        return Err(BackendError::Protocol);
    }
    let data_size = usize::try_from(data_size).map_err(|_| BackendError::Protocol)?;
    let tail = usize::try_from(tail).map_err(|_| BackendError::Protocol)?;
    if data_size > SYSLOG_LIMIT || data_size > capacity || tail >= data_size {
        return Err(BackendError::Protocol);
    }
    let end = SYSLOG_HEADER_LEN
        .checked_add(data_size)
        .filter(|end| *end <= segment_size)
        .ok_or(BackendError::Protocol)?;
    debug_assert_eq!(end, SYSLOG_HEADER_LEN + data_size);
    Ok((data_size, tail))
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
fn duration_to_sysv_timespec(duration: Duration) -> Result<SysvTimespec, BackendError> {
    let nanoseconds = duration.subsec_nanos();
    debug_assert!(nanoseconds <= 999_999_999);
    Ok(SysvTimespec {
        seconds: std::os::raw::c_long::try_from(duration.as_secs())
            .map_err(|_| BackendError::Unavailable)?,
        nanoseconds: nanoseconds as std::os::raw::c_long,
    })
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
fn bounded_sem_lock_with(
    mut elapsed: impl FnMut() -> Duration,
    mut operation: impl FnMut(&SysvTimespec) -> Result<(), i32>,
) -> Result<(), BackendError> {
    loop {
        let remaining = BUSYBOX_LOCK_TIMEOUT
            .checked_sub(elapsed())
            .filter(|remaining| !remaining.is_zero())
            .ok_or(BackendError::Unavailable)?;
        let timeout = duration_to_sysv_timespec(remaining)?;
        match operation(&timeout) {
            Ok(()) => return Ok(()),
            Err(EINTR) => {}
            Err(_) => return Err(BackendError::Unavailable),
        }
    }
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
trait BusyboxIpc {
    type Attachment: Copy;

    fn shared_id(&mut self) -> Result<i32, BackendError>;
    fn segment_size(&mut self, shared_id: i32) -> Result<usize, BackendError>;
    fn attach(&mut self, shared_id: i32) -> Result<Self::Attachment, BackendError>;
    fn semaphore(&mut self) -> Result<i32, BackendError>;
    fn lock(&mut self, semaphore: i32) -> Result<(), BackendError>;
    fn read(
        &mut self,
        attachment: Self::Attachment,
        segment_size: usize,
    ) -> Result<Vec<u8>, BackendError>;
    fn unlock(&mut self, semaphore: i32) -> Result<(), BackendError>;
    fn detach(&mut self, attachment: Self::Attachment) -> Result<(), BackendError>;
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
struct BusyboxResources<'a, I: BusyboxIpc> {
    ipc: &'a mut I,
    attachment: Option<I::Attachment>,
    semaphore: Option<i32>,
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
impl<'a, I: BusyboxIpc> BusyboxResources<'a, I> {
    fn new(ipc: &'a mut I, attachment: I::Attachment) -> Self {
        Self {
            ipc,
            attachment: Some(attachment),
            semaphore: None,
        }
    }

    fn read(&mut self, segment_size: usize) -> Result<Vec<u8>, BackendError> {
        let semaphore = self.ipc.semaphore()?;
        self.ipc.lock(semaphore)?;
        self.semaphore = Some(semaphore);
        let attachment = self.attachment.ok_or(BackendError::Unavailable)?;
        self.ipc.read(attachment, segment_size)
    }

    fn cleanup(&mut self) -> Result<(), BackendError> {
        let mut failed = false;
        if let Some(semaphore) = self.semaphore.take()
            && self.ipc.unlock(semaphore).is_err()
        {
            failed = true;
        }
        if let Some(attachment) = self.attachment.take()
            && self.ipc.detach(attachment).is_err()
        {
            failed = true;
        }
        if failed {
            Err(BackendError::Unavailable)
        } else {
            Ok(())
        }
    }
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
impl<I: BusyboxIpc> Drop for BusyboxResources<'_, I> {
    fn drop(&mut self) {
        let _ = self.cleanup();
    }
}

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
fn read_busybox_syslog_from<I: BusyboxIpc>(ipc: &mut I) -> Result<Vec<u8>, BackendError> {
    let shared_id = ipc.shared_id()?;
    let segment_size = ipc.segment_size(shared_id)?;
    let attachment = ipc.attach(shared_id)?;
    let mut resources = BusyboxResources::new(ipc, attachment);
    let result = resources.read(segment_size);
    let cleanup = resources.cleanup();
    if cleanup.is_err() {
        Err(BackendError::Unavailable)
    } else {
        result
    }
}

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
fn segment_size_from_shmid_ds(info: &ShmidDs) -> usize {
    usize::from_ne_bytes(
        info.bytes[SHMID_DS_SEGSZ_OFFSET..SHMID_DS_SEGSZ_OFFSET + std::mem::size_of::<usize>()]
            .try_into()
            .expect("verified shmid_ds offset"),
    )
}

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
struct LinuxBusyboxIpc;

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
impl BusyboxIpc for LinuxBusyboxIpc {
    type Attachment = *mut c_void;

    fn shared_id(&mut self) -> Result<i32, BackendError> {
        const KEY_ID: c_int = 0x414e4547;
        // SAFETY: this only resolves BusyBox syslogd's fixed shared-memory key.
        let shared_id = unsafe { shmget(KEY_ID, 0, 0) };
        (shared_id >= 0)
            .then_some(shared_id)
            .ok_or(BackendError::Unavailable)
    }

    fn segment_size(&mut self, shared_id: i32) -> Result<usize, BackendError> {
        const IPC_STAT: c_int = 2;
        let mut info = ShmidDs {
            bytes: [0; std::mem::size_of::<ShmidDs>()],
        };
        // SAFETY: info has the size and alignment of this target libc's
        // shmid_ds, proven by the corresponding C ABI probe.
        if unsafe { shmctl(shared_id, IPC_STAT, &raw mut info) } != 0 {
            return Err(BackendError::Unavailable);
        }
        Ok(segment_size_from_shmid_ds(&info))
    }

    fn attach(&mut self, shared_id: i32) -> Result<Self::Attachment, BackendError> {
        const SHM_RDONLY: c_int = 0o10000;
        // SAFETY: SHM_RDONLY prevents Control from mutating the syslog ring.
        let shared = unsafe { shmat(shared_id, std::ptr::null(), SHM_RDONLY) };
        (shared as isize != -1)
            .then_some(shared)
            .ok_or(BackendError::Unavailable)
    }

    fn semaphore(&mut self) -> Result<i32, BackendError> {
        const KEY_ID: c_int = 0x414e4547;
        // SAFETY: this only resolves BusyBox syslogd's fixed semaphore set.
        let semaphore = unsafe { semget(KEY_ID, 0, 0) };
        (semaphore >= 0)
            .then_some(semaphore)
            .ok_or(BackendError::Unavailable)
    }

    fn lock(&mut self, semaphore: i32) -> Result<(), BackendError> {
        const SEM_UNDO: std::os::raw::c_short = 0x1000;
        let mut operations = [
            SemBuf {
                sem_num: 1,
                sem_op: 0,
                sem_flg: 0,
            },
            SemBuf {
                sem_num: 0,
                sem_op: 1,
                sem_flg: SEM_UNDO,
            },
        ];
        let start = Instant::now();
        bounded_sem_lock_with(
            || start.elapsed(),
            |timeout| {
                // SAFETY: both operations are submitted atomically against
                // BusyBox's two-semaphore set. timeout is a valid relative
                // timespec for this target's C ABI.
                let status = unsafe {
                    semtimedop(
                        semaphore,
                        operations.as_mut_ptr(),
                        operations.len(),
                        timeout,
                    )
                };
                if status == 0 {
                    Ok(())
                } else {
                    Err(io::Error::last_os_error().raw_os_error().unwrap_or(0))
                }
            },
        )
    }

    fn read(
        &mut self,
        attachment: Self::Attachment,
        segment_size: usize,
    ) -> Result<Vec<u8>, BackendError> {
        if segment_size < SYSLOG_HEADER_LEN {
            return Err(BackendError::Protocol);
        }
        let bytes = attachment.cast::<u8>();
        // SAFETY: shmctl proved that the attached segment has at least the
        // complete two-i32 header before either field is dereferenced.
        let data_size = unsafe { std::ptr::read_unaligned(bytes.cast::<i32>()) };
        // SAFETY: the same segment-size proof covers the second i32 field.
        let tail = unsafe {
            std::ptr::read_unaligned(bytes.add(std::mem::size_of::<i32>()).cast::<i32>())
        };
        let (data_size, tail) = checked_busybox_layout(segment_size, data_size, tail)?;
        // SAFETY: checked_busybox_layout proved header + data_size is within
        // the shmctl-reported segment, and the mapping stays attached and
        // locked for the lifetime of this slice.
        let data = unsafe { std::slice::from_raw_parts(bytes.add(SYSLOG_HEADER_LEN), data_size) };
        parse_busybox_syslog_ring(data_size, tail, data)
    }

    fn unlock(&mut self, semaphore: i32) -> Result<(), BackendError> {
        const IPC_NOWAIT: std::os::raw::c_short = 0o4000;
        const SEM_UNDO: std::os::raw::c_short = 0x1000;
        let mut operation = [SemBuf {
            sem_num: 0,
            sem_op: -1,
            sem_flg: IPC_NOWAIT | SEM_UNDO,
        }];
        // SAFETY: this releases exactly the reader count acquired by lock.
        (unsafe { semop(semaphore, operation.as_mut_ptr(), operation.len()) } == 0)
            .then_some(())
            .ok_or(BackendError::Unavailable)
    }

    fn detach(&mut self, attachment: Self::Attachment) -> Result<(), BackendError> {
        // SAFETY: resources calls detach exactly once for a successful shmat.
        (unsafe { shmdt(attachment) } == 0)
            .then_some(())
            .ok_or(BackendError::Unavailable)
    }
}

#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
pub(super) fn read_busybox_syslog() -> Result<Vec<u8>, BackendError> {
    read_busybox_syslog_from(&mut LinuxBusyboxIpc)
}

#[cfg(not(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))))]
pub(super) fn read_busybox_syslog() -> Result<Vec<u8>, BackendError> {
    Err(BackendError::Unavailable)
}

pub(super) fn read_system_log(path: &Path) -> Result<Vec<u8>, BackendError> {
    read_bounded(path, FILE_LIMIT).or_else(|_| read_busybox_syslog())
}

#[cfg(target_os = "linux")]
pub(super) fn read_kernel_log() -> Result<Vec<u8>, BackendError> {
    const SYSLOG_ACTION_READ_ALL: c_int = 3;
    const SYSLOG_ACTION_SIZE_BUFFER: c_int = 10;
    unsafe extern "C" {
        fn klogctl(kind: c_int, buffer: *mut c_char, length: c_int) -> c_int;
    }
    // SAFETY: SIZE_BUFFER does not dereference the null buffer and only reports
    // the kernel ring capacity.
    let available = unsafe { klogctl(SYSLOG_ACTION_SIZE_BUFFER, std::ptr::null_mut(), 0) };
    if available <= 0 {
        return Err(BackendError::Unavailable);
    }
    let capacity = usize::try_from(available)
        .map_err(|_| BackendError::Protocol)?
        .min(64 * 1024);
    let mut output = vec![0u8; capacity];
    let length = c_int::try_from(capacity).map_err(|_| BackendError::Protocol)?;
    // SAFETY: output owns `length` writable bytes. READ_ALL is non-destructive
    // and does not change the kernel log ring cursor.
    let read = unsafe { klogctl(SYSLOG_ACTION_READ_ALL, output.as_mut_ptr().cast(), length) };
    if read < 0 {
        return Err(BackendError::Unavailable);
    }
    output.truncate(usize::try_from(read).map_err(|_| BackendError::Protocol)?);
    Ok(output)
}

#[cfg(not(target_os = "linux"))]
pub(super) fn read_kernel_log() -> Result<Vec<u8>, BackendError> {
    Err(BackendError::Unavailable)
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(super) fn parse_android_log_entry(entry: &[u8]) -> Result<String, BackendError> {
    const LEGACY_HEADER: usize = 20;
    if entry.len() < LEGACY_HEADER {
        return Err(BackendError::Protocol);
    }
    let payload_len = usize::from(u16::from_le_bytes([entry[0], entry[1]]));
    // /dev/log_main returns the legacy logger_entry layout unless the reader
    // first selects a newer ABI with LOGGER_SET_VERSION. The second u16 is
    // padding in that layout and is not initialized by this kernel, so it must
    // never be interpreted as a header length.
    let header_len = LEGACY_HEADER;
    if header_len > entry.len() || payload_len > entry.len().saturating_sub(header_len) {
        return Err(BackendError::Protocol);
    }
    let pid = i32::from_le_bytes(entry[4..8].try_into().map_err(|_| BackendError::Protocol)?);
    let seconds = i32::from_le_bytes(
        entry[12..16]
            .try_into()
            .map_err(|_| BackendError::Protocol)?,
    );
    let nanoseconds = i32::from_le_bytes(
        entry[16..20]
            .try_into()
            .map_err(|_| BackendError::Protocol)?,
    );
    let payload = &entry[header_len..header_len + payload_len];
    if payload.is_empty() {
        return Err(BackendError::Protocol);
    }
    let priority = *b"??VDIWEFS".get(usize::from(payload[0])).unwrap_or(&b'?') as char;
    let fields = &payload[1..];
    let tag_end = fields
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(fields.len());
    let message_start = (tag_end + 1).min(fields.len());
    let message_end = fields[message_start..]
        .iter()
        .position(|byte| *byte == 0)
        .map(|value| message_start + value)
        .unwrap_or(fields.len());
    let clean = |bytes: &[u8]| {
        String::from_utf8_lossy(bytes)
            .chars()
            .map(|value| {
                if value == '\n' || value == '\t' || !value.is_control() {
                    value
                } else {
                    '?'
                }
            })
            .collect::<String>()
    };
    let tag = clean(&fields[..tag_end]);
    let message = clean(&fields[message_start..message_end]);
    Ok(format!(
        "{seconds}.{nanoseconds:09} {priority}/{tag}({pid:>5}): {message}\n"
    ))
}

#[cfg(target_os = "linux")]
pub(super) fn read_streamer_log(path: &Path) -> Result<Vec<u8>, BackendError> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NONBLOCK | O_NOFOLLOW)
        .open(path)
        .map_err(|_| BackendError::Unavailable)?;
    let metadata = file.metadata().map_err(|_| BackendError::Unavailable)?;
    if !metadata.file_type().is_char_device() {
        return Err(BackendError::Protocol);
    }
    let mut output = Vec::new();
    let mut record = vec![0u8; 8192];
    for _ in 0..1024 {
        match file.read(&mut record) {
            Ok(0) => break,
            Ok(length) => {
                let line = parse_android_log_entry(&record[..length])?;
                output.extend_from_slice(line.as_bytes());
                if output.len() >= 64 * 1024 {
                    output.truncate(64 * 1024);
                    break;
                }
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
            Err(_) => return Err(BackendError::Unavailable),
        }
    }
    Ok(output)
}

#[cfg(not(target_os = "linux"))]
pub(super) fn read_streamer_log(_path: &Path) -> Result<Vec<u8>, BackendError> {
    Err(BackendError::Unavailable)
}

pub(super) fn write_gpio(root: &Path, pin: u64, enabled: bool) -> Result<(), BackendError> {
    let path = root.join(format!("gpio{pin}/value"));
    let metadata = path
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if !metadata.is_file() {
        return Err(BackendError::Protocol);
    }
    // GPIO value attributes are sysfs control files. They accept a bounded
    // write but reject fsync, so the durable configuration writer cannot be
    // used here.
    let mut file = OpenOptions::new()
        .write(true)
        .custom_flags(O_NOFOLLOW)
        .open(path)
        .map_err(|_| BackendError::Unavailable)?;
    file.write_all(if enabled { b"1\n" } else { b"0\n" })
        .map_err(|_| BackendError::Unavailable)
}

pub(super) fn read_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, BackendError> {
    let file = File::open(path).map_err(|_| BackendError::Unavailable)?;
    let metadata = file.metadata().map_err(|_| BackendError::Unavailable)?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err(BackendError::Protocol);
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| BackendError::Unavailable)?;
    if bytes.len() as u64 > limit {
        return Err(BackendError::Protocol);
    }
    Ok(bytes)
}

pub(super) fn read_virtual_bounded(path: &Path, limit: usize) -> Result<Vec<u8>, BackendError> {
    let file = File::open(path).map_err(|_| BackendError::Unavailable)?;
    let mut bytes = Vec::with_capacity(limit.min(4096));
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| BackendError::Unavailable)?;
    if bytes.len() > limit {
        return Err(BackendError::Protocol);
    }
    Ok(bytes)
}

pub(super) fn read_virtual_text_value(path: &Path, limit: usize) -> Option<String> {
    String::from_utf8(read_virtual_bounded(path, limit).ok()?)
        .ok()
        .map(|value| value.trim().to_owned())
}

pub(super) fn write_in_place(path: &Path, content: &[u8]) -> Result<(), BackendError> {
    write_atomic_replace(path, content)
}

pub(super) fn write_atomic_replace(path: &Path, content: &[u8]) -> Result<(), BackendError> {
    let metadata = path
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if !metadata.is_file() || content.len() as u64 > FILE_LIMIT {
        return Err(BackendError::Protocol);
    }
    let parent = path.parent().ok_or(BackendError::Protocol)?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or(BackendError::Protocol)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| BackendError::Unavailable)?
        .as_nanos();
    let temporary = parent.join(format!(
        ".{name}.thingino-control-{}-{nonce}.tmp",
        std::process::id()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(metadata.mode() & 0o777)
            .custom_flags(O_NOFOLLOW)
            .open(&temporary)
            .map_err(|_| BackendError::Unavailable)?;
        file.write_all(content)
            .and_then(|_| file.sync_all())
            .map_err(|_| BackendError::Unavailable)?;
        fs::rename(&temporary, path).map_err(|_| BackendError::Unavailable)?;
        // Once rename succeeds, the new canonical bytes and the already
        // applied live state form one logical commit. A directory fsync error
        // cannot safely be reported as a failed write: the caller would roll
        // back live state while the new file is already visible. Keep the
        // committed state aligned and report only the reduced crash-durability
        // guarantee.
        if File::open(parent)
            .and_then(|directory| directory.sync_all())
            .is_err()
        {
            eprintln!("thingino-controld: Prudynt config committed but directory sync failed");
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub(super) fn write_config_file(
    path: &Path,
    content: &[u8],
    mode: u32,
) -> Result<(), BackendError> {
    if content.len() as u64 > FILE_LIMIT {
        return Err(BackendError::Protocol);
    }
    if path.exists() {
        return write_in_place(path, content);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(O_NOFOLLOW)
        .open(path)
        .map_err(|_| BackendError::Unavailable)?;
    file.write_all(content)
        .and_then(|_| file.sync_all())
        .map_err(|_| BackendError::Unavailable)
}

pub(super) fn connect_socket(path: &Path, deadline: Instant) -> Result<UnixStream, BackendError> {
    loop {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match UnixStream::connect(path) {
            Ok(stream) => {
                stream
                    .set_nonblocking(true)
                    .map_err(|_| BackendError::Connection)?;
                return Ok(stream);
            }
            Err(error) if retryable_unix_connect_error(&error) => {
                let budget = deadline
                    .checked_duration_since(Instant::now())
                    .ok_or(BackendError::Timeout)?;
                thread::sleep(budget.min(Duration::from_millis(20)));
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
}

pub(super) fn retryable_unix_connect_error(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::NotFound
            | io::ErrorKind::ConnectionRefused
            | io::ErrorKind::ConnectionReset
            | io::ErrorKind::Interrupted
            | io::ErrorKind::WouldBlock
    )
}

pub(super) fn wait_for_socket(deadline: Instant) -> Result<(), BackendError> {
    let budget = deadline
        .checked_duration_since(Instant::now())
        .ok_or(BackendError::Timeout)?;
    thread::sleep(budget.min(Duration::from_millis(1)));
    Ok(())
}

pub(super) fn write_deadline(
    stream: &mut UnixStream,
    mut bytes: &[u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !bytes.is_empty() {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match stream.write(bytes) {
            Ok(0) => return Err(BackendError::Connection),
            Ok(count) => bytes = &bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => wait_for_socket(deadline)?,
            Err(error) if error.kind() == io::ErrorKind::TimedOut => {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
    Ok(())
}

pub(super) fn read_line(
    stream: &mut UnixStream,
    deadline: Instant,
    limit: usize,
) -> Result<String, BackendError> {
    let mut bytes = Vec::new();
    while bytes.len() < limit {
        let mut byte = [0_u8; 1];
        read_exact_deadline(stream, &mut byte, deadline)?;
        if byte[0] == b'\n' {
            return String::from_utf8(bytes).map_err(|_| BackendError::Protocol);
        }
        if byte[0] == b'\r' || byte[0] == 0 {
            return Err(BackendError::Protocol);
        }
        bytes.push(byte[0]);
    }
    Err(BackendError::Protocol)
}

pub(super) fn read_exact_deadline(
    stream: &mut UnixStream,
    mut buffer: &mut [u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !buffer.is_empty() {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match stream.read(buffer) {
            Ok(0) => return Err(BackendError::Protocol),
            Ok(count) => buffer = &mut buffer[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => wait_for_socket(deadline)?,
            Err(error) if error.kind() == io::ErrorKind::TimedOut => {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
    Ok(())
}

pub(super) fn read_to_end_deadline(
    stream: &mut UnixStream,
    deadline: Instant,
    limit: usize,
) -> Result<Vec<u8>, BackendError> {
    let mut output = Vec::with_capacity(4096);
    let mut buffer = [0_u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match stream.read(&mut buffer) {
            Ok(0) => return Ok(output),
            Ok(count) => {
                if output.len() + count > limit {
                    return Err(BackendError::Protocol);
                }
                output.extend_from_slice(&buffer[..count]);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => wait_for_socket(deadline)?,
            Err(error) if error.kind() == io::ErrorKind::TimedOut => {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
}

pub(super) fn read_pid(path: &Path) -> Option<i32> {
    let text = String::from_utf8(read_bounded(path, 32).ok()?).ok()?;
    let pid = text.trim().parse::<i32>().ok()?;
    (pid > 1).then_some(pid)
}

pub(super) fn process_matches(pid_path: &Path, executable: &Path) -> bool {
    let Some(pid) = read_pid(pid_path) else {
        return false;
    };
    fs::read_link(format!("/proc/{pid}/exe"))
        .map(|path| path == executable)
        .unwrap_or(false)
}

pub(super) fn read_exact_line(path: &Path, expected: &str) -> bool {
    read_bounded(path, 64)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .map(|line| line.trim_end_matches(['\r', '\n']) == expected)
        .unwrap_or(false)
}

pub(super) fn first_number(path: &Path) -> Option<f64> {
    read_numbers(path, 1).into_iter().next()
}

pub(super) fn read_text_value(path: &Path, limit: u64) -> Option<String> {
    String::from_utf8(read_bounded(path, limit).ok()?)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

pub(super) fn read_attribute_text(path: &Path, limit: u64) -> Option<String> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW)
        .open(path)
        .ok()?;
    let mut bytes = Vec::with_capacity(limit.min(64) as usize);
    file.take(limit + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > limit {
        return None;
    }
    String::from_utf8(bytes)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

pub(super) fn read_number_value(path: &Path) -> Option<String> {
    let value = read_text_value(path, 64)?;
    value.parse::<f64>().ok()?;
    Some(value)
}

pub(super) fn read_numbers(path: &Path, count: usize) -> Vec<f64> {
    read_bounded(path, 512)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .map(|text| {
            text.split_ascii_whitespace()
                .take(count)
                .filter_map(|value| value.parse().ok())
                .collect()
        })
        .unwrap_or_default()
}

#[derive(Default)]
pub(super) struct Memory {
    pub(super) total: u64,
    pub(super) active: u64,
    pub(super) free: u64,
    pub(super) buffers: u64,
    pub(super) cached: u64,
}

pub(super) fn read_memory(path: &Path) -> Memory {
    let mut memory = Memory::default();
    let Some(text) = read_bounded(path, 16 * 1024)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
    else {
        return memory;
    };
    for line in text.lines() {
        let mut fields = line.split_ascii_whitespace();
        let key = fields.next().unwrap_or_default();
        let value = fields
            .next()
            .and_then(|field| field.parse().ok())
            .unwrap_or(0);
        match key {
            "MemTotal:" => memory.total = value,
            "Active:" => memory.active = value,
            "MemFree:" => memory.free = value,
            "Buffers:" => memory.buffers = value,
            "Cached:" => memory.cached = value,
            _ => {}
        }
    }
    memory
}

#[cfg(test)]
#[path = "platform_tests.rs"]
mod sysv_ipc_tests;
