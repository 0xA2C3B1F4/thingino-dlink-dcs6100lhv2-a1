#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
use super::{
    abi::EINTR,
    logs::{SYSLOG_LIMIT, parse_busybox_syslog_ring},
};
use crate::BackendError;
#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
use std::time::Duration;
#[cfg(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips")))]
use std::{
    io,
    os::raw::{c_int, c_void},
    time::Instant,
};

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
const SYSLOG_HEADER_LEN: usize = 2 * std::mem::size_of::<i32>();
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
pub(in crate::camera) fn read_busybox_syslog() -> Result<Vec<u8>, BackendError> {
    read_busybox_syslog_from(&mut LinuxBusyboxIpc)
}

#[cfg(not(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))))]
pub(in crate::camera) fn read_busybox_syslog() -> Result<Vec<u8>, BackendError> {
    Err(BackendError::Unavailable)
}

#[cfg(test)]
#[path = "../platform_tests.rs"]
mod sysv_ipc_tests;
