use super::*;
use std::collections::VecDeque;
use std::panic::{AssertUnwindSafe, catch_unwind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Failure {
    Shmget,
    Shmctl,
    Shmat,
    Semget,
    Lock,
    Read,
    Unlock,
    Detach,
}

struct FakeIpc {
    failure: Option<Failure>,
    events: Vec<&'static str>,
}

impl FakeIpc {
    fn new(failure: Option<Failure>) -> Self {
        Self {
            failure,
            events: Vec::new(),
        }
    }

    fn fails(&self, stage: Failure) -> bool {
        self.failure == Some(stage)
    }
}

impl BusyboxIpc for FakeIpc {
    type Attachment = usize;

    fn shared_id(&mut self) -> Result<i32, BackendError> {
        self.events.push("shmget");
        if self.fails(Failure::Shmget) {
            Err(BackendError::Unavailable)
        } else {
            Ok(11)
        }
    }

    fn segment_size(&mut self, _shared_id: i32) -> Result<usize, BackendError> {
        self.events.push("shmctl");
        if self.fails(Failure::Shmctl) {
            Err(BackendError::Unavailable)
        } else {
            Ok(SYSLOG_HEADER_LEN + 32)
        }
    }

    fn attach(&mut self, _shared_id: i32) -> Result<Self::Attachment, BackendError> {
        self.events.push("shmat");
        if self.fails(Failure::Shmat) {
            Err(BackendError::Unavailable)
        } else {
            Ok(0x1000)
        }
    }

    fn semaphore(&mut self) -> Result<i32, BackendError> {
        self.events.push("semget");
        if self.fails(Failure::Semget) {
            Err(BackendError::Unavailable)
        } else {
            Ok(17)
        }
    }

    fn lock(&mut self, _semaphore: i32) -> Result<(), BackendError> {
        self.events.push("lock");
        if self.fails(Failure::Lock) {
            Err(BackendError::Unavailable)
        } else {
            Ok(())
        }
    }

    fn read(
        &mut self,
        _attachment: Self::Attachment,
        _segment_size: usize,
    ) -> Result<Vec<u8>, BackendError> {
        self.events.push("read");
        if self.fails(Failure::Read) {
            Err(BackendError::Protocol)
        } else {
            Ok(b"log\n".to_vec())
        }
    }

    fn unlock(&mut self, _semaphore: i32) -> Result<(), BackendError> {
        self.events.push("unlock");
        if self.fails(Failure::Unlock) {
            Err(BackendError::Unavailable)
        } else {
            Ok(())
        }
    }

    fn detach(&mut self, _attachment: Self::Attachment) -> Result<(), BackendError> {
        self.events.push("detach");
        if self.fails(Failure::Detach) {
            Err(BackendError::Unavailable)
        } else {
            Ok(())
        }
    }
}

#[test]
fn busybox_layout_rejects_segments_shorter_than_the_header() {
    for segment_size in 0..SYSLOG_HEADER_LEN {
        assert_eq!(
            checked_busybox_layout(segment_size, 1, 0),
            Err(BackendError::Protocol)
        );
    }
}

#[test]
fn busybox_layout_rejects_invalid_header_values_and_capacity() {
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN, 1, 0),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + 1, 0, 0),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + 1, -1, 0),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + 1, 1, -1),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + 1, 1, 1),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + SYSLOG_LIMIT + 1, 65_537, 0),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + 7, 8, 0),
        Err(BackendError::Protocol)
    );
}

#[test]
fn busybox_layout_uses_checked_arithmetic_at_usize_edges() {
    assert_eq!(
        checked_busybox_layout(usize::MAX, i32::MAX, 0),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        checked_busybox_layout(usize::MAX, SYSLOG_LIMIT as i32, 0),
        Ok((SYSLOG_LIMIT, 0))
    );
    assert_eq!(
        checked_busybox_layout(SYSLOG_HEADER_LEN + SYSLOG_LIMIT, SYSLOG_LIMIT as i32, 0),
        Ok((SYSLOG_LIMIT, 0))
    );
}

#[test]
fn busybox_parser_accepts_direct_and_wrapped_rings() {
    let mut direct = vec![b'x'; 32];
    direct[3] = 0;
    direct[4..10].copy_from_slice(b"line\n\0");
    assert_eq!(
        parse_busybox_syslog_ring(32, 10, &direct).unwrap(),
        b"line\n"
    );

    let mut wrapped = vec![b'x'; 32];
    wrapped[23] = 0;
    wrapped[24..32].copy_from_slice(b"wrapped ");
    wrapped[..6].copy_from_slice(b"line\n\0");
    wrapped[6..12].copy_from_slice(b"next\n\0");
    assert_eq!(
        parse_busybox_syslog_ring(32, 12, &wrapped).unwrap(),
        b"wrapped line\nnext\n"
    );
}

#[test]
fn bounded_lock_retries_eintr_against_the_original_deadline() {
    let mut elapsed = VecDeque::from([Duration::ZERO, Duration::from_millis(40)]);
    let mut calls = 0;
    let mut observed = Vec::new();
    bounded_sem_lock_with(
        || elapsed.pop_front().unwrap(),
        |timeout| {
            observed.push(*timeout);
            calls += 1;
            if calls == 1 { Err(EINTR) } else { Ok(()) }
        },
    )
    .unwrap();
    assert_eq!(observed[0].seconds, 0);
    assert_eq!(observed[0].nanoseconds, 100_000_000);
    assert_eq!(observed[1].seconds, 0);
    assert_eq!(observed[1].nanoseconds, 60_000_000);
}

#[test]
fn bounded_lock_stops_after_timeout_without_busy_looping() {
    let mut elapsed = VecDeque::from([Duration::ZERO, BUSYBOX_LOCK_TIMEOUT]);
    let mut calls = 0;
    let result = bounded_sem_lock_with(
        || elapsed.pop_front().unwrap(),
        |_| {
            calls += 1;
            Err(EINTR)
        },
    );
    assert_eq!(result, Err(BackendError::Unavailable));
    assert_eq!(calls, 1);
}

#[test]
fn busybox_lifecycle_does_not_attach_after_shmctl_failure() {
    let mut ipc = FakeIpc::new(Some(Failure::Shmctl));
    assert_eq!(
        read_busybox_syslog_from(&mut ipc),
        Err(BackendError::Unavailable)
    );
    assert_eq!(ipc.events, ["shmget", "shmctl"]);
}

#[test]
fn busybox_lifecycle_stops_at_shared_memory_lookup_failure() {
    let mut ipc = FakeIpc::new(Some(Failure::Shmget));
    assert_eq!(
        read_busybox_syslog_from(&mut ipc),
        Err(BackendError::Unavailable)
    );
    assert_eq!(ipc.events, ["shmget"]);
}

#[test]
fn busybox_lifecycle_detaches_after_semaphore_lookup_failure() {
    let mut ipc = FakeIpc::new(Some(Failure::Semget));
    assert_eq!(
        read_busybox_syslog_from(&mut ipc),
        Err(BackendError::Unavailable)
    );
    assert_eq!(
        ipc.events,
        ["shmget", "shmctl", "shmat", "semget", "detach"]
    );
}

#[test]
fn bounded_lock_does_not_retry_non_eintr_errors() {
    let mut calls = 0;
    let result = bounded_sem_lock_with(
        || Duration::ZERO,
        |_| {
            calls += 1;
            assert_eq!(calls, 1, "only EINTR permits a retry");
            Err(22)
        },
    );
    assert_eq!(result, Err(BackendError::Unavailable));
    assert_eq!(calls, 1);
}

#[test]
fn bounded_lock_does_not_call_the_kernel_after_the_deadline() {
    for elapsed in [
        BUSYBOX_LOCK_TIMEOUT,
        BUSYBOX_LOCK_TIMEOUT + Duration::from_nanos(1),
    ] {
        assert_eq!(
            bounded_sem_lock_with(|| elapsed, |_| panic!("deadline already expired")),
            Err(BackendError::Unavailable),
        );
    }
}

#[test]
fn sysv_timeout_conversion_preserves_subseconds_and_rejects_overflow() {
    let timeout = duration_to_sysv_timespec(Duration::new(3, 999_999_999)).unwrap();
    assert_eq!(timeout.seconds, 3);
    assert_eq!(timeout.nanoseconds, 999_999_999);
    assert_eq!(
        duration_to_sysv_timespec(Duration::from_secs(u64::MAX)),
        Err(BackendError::Unavailable),
    );
}

#[test]
fn busybox_resources_release_once_during_unwinding() {
    let mut ipc = FakeIpc::new(None);
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        let mut resources = BusyboxResources::new(&mut ipc, 0x1000);
        resources.semaphore = Some(17);
        panic!("simulated caller panic while resources are owned");
    }));
    assert!(outcome.is_err());
    assert_eq!(ipc.events, ["unlock", "detach"]);
}

#[test]
fn socket_line_reader_preserves_framing_and_limit_errors() {
    use super::super::socket_io::read_line;
    use std::io::Write;
    use std::os::unix::net::UnixStream;
    use std::time::Instant;

    for (input, limit, expected) in [
        (b"ok\n".as_slice(), 3, Ok("ok".to_owned())),
        (b"ok\n".as_slice(), 2, Err(BackendError::Protocol)),
        (b"x\r\n".as_slice(), 8, Err(BackendError::Protocol)),
        (b"x\0\n".as_slice(), 8, Err(BackendError::Protocol)),
        (b"\xff\n".as_slice(), 8, Err(BackendError::Protocol)),
        (b"x".as_slice(), 8, Err(BackendError::Protocol)),
    ] {
        let (mut reader, mut writer) = UnixStream::pair().unwrap();
        reader.set_nonblocking(true).unwrap();
        writer.write_all(input).unwrap();
        drop(writer);
        assert_eq!(
            read_line(&mut reader, Instant::now() + Duration::from_secs(1), limit),
            expected
        );
    }
}

#[test]
fn socket_deadline_helpers_preserve_expired_and_empty_request_results() {
    use super::super::socket_io::{read_exact_deadline, read_to_end_deadline, write_deadline};
    use std::os::unix::net::UnixStream;
    use std::time::Instant;

    let (mut stream, _peer) = UnixStream::pair().unwrap();
    stream.set_nonblocking(true).unwrap();
    let expired = Instant::now() - Duration::from_secs(1);
    assert_eq!(
        write_deadline(&mut stream, b"x", expired),
        Err(BackendError::Timeout)
    );
    assert_eq!(
        read_exact_deadline(&mut stream, &mut [0], expired),
        Err(BackendError::Timeout)
    );
    assert_eq!(
        read_to_end_deadline(&mut stream, expired, 8),
        Err(BackendError::Timeout)
    );
    assert_eq!(write_deadline(&mut stream, b"", expired), Ok(()));
    assert_eq!(read_exact_deadline(&mut stream, &mut [], expired), Ok(()));
}

#[test]
fn busybox_lifecycle_does_not_detach_after_shmat_failure() {
    let mut ipc = FakeIpc::new(Some(Failure::Shmat));
    assert_eq!(
        read_busybox_syslog_from(&mut ipc),
        Err(BackendError::Unavailable)
    );
    assert_eq!(ipc.events, ["shmget", "shmctl", "shmat"]);
}

#[test]
fn busybox_lifecycle_detaches_without_unlock_after_lock_timeout() {
    let mut ipc = FakeIpc::new(Some(Failure::Lock));
    assert_eq!(
        read_busybox_syslog_from(&mut ipc),
        Err(BackendError::Unavailable)
    );
    assert_eq!(
        ipc.events,
        ["shmget", "shmctl", "shmat", "semget", "lock", "detach"]
    );
}

#[test]
fn busybox_lifecycle_cleans_up_after_parser_error() {
    let mut ipc = FakeIpc::new(Some(Failure::Read));
    assert_eq!(
        read_busybox_syslog_from(&mut ipc),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        ipc.events,
        [
            "shmget", "shmctl", "shmat", "semget", "lock", "read", "unlock", "detach"
        ]
    );
}

#[test]
fn busybox_lifecycle_fails_closed_on_unlock_or_detach_error() {
    for failure in [Failure::Unlock, Failure::Detach] {
        let mut ipc = FakeIpc::new(Some(failure));
        assert_eq!(
            read_busybox_syslog_from(&mut ipc),
            Err(BackendError::Unavailable)
        );
        assert_eq!(
            ipc.events,
            [
                "shmget", "shmctl", "shmat", "semget", "lock", "read", "unlock", "detach"
            ]
        );
        assert_eq!(
            ipc.events
                .iter()
                .filter(|event| **event == "unlock")
                .count(),
            1
        );
        assert_eq!(
            ipc.events
                .iter()
                .filter(|event| **event == "detach")
                .count(),
            1
        );
    }
}

#[test]
fn busybox_lifecycle_success_unlocks_and_detaches_exactly_once() {
    let mut ipc = FakeIpc::new(None);
    assert_eq!(read_busybox_syslog_from(&mut ipc), Ok(b"log\n".to_vec()));
    assert_eq!(
        ipc.events
            .iter()
            .filter(|event| **event == "unlock")
            .count(),
        1
    );
    assert_eq!(
        ipc.events
            .iter()
            .filter(|event| **event == "detach")
            .count(),
        1
    );
}

#[test]
fn busybox_resource_drop_never_panics_or_retries_cleanup() {
    let mut ipc = FakeIpc::new(Some(Failure::Unlock));
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        let mut resources = BusyboxResources::new(&mut ipc, 0x1000);
        resources.semaphore = Some(17);
    }));
    assert!(outcome.is_ok());
    assert_eq!(ipc.events, ["unlock", "detach"]);
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod linux_integration {
    use super::*;

    const IPC_PRIVATE: c_int = 0;
    const IPC_CREAT: c_int = 0o1000;
    const IPC_RMID: c_int = 0;
    const GETVAL: c_int = 12;
    const SETVAL: c_int = 16;

    unsafe extern "C" {
        fn semctl(id: c_int, number: c_int, command: c_int, ...) -> c_int;
    }

    struct PrivateResources {
        shared_id: c_int,
        semaphore: c_int,
    }

    impl PrivateResources {
        fn create(segment_size: usize) -> Self {
            // SAFETY: IPC_PRIVATE creates a new test-owned segment.
            let shared_id = unsafe { shmget(IPC_PRIVATE, segment_size, IPC_CREAT | 0o600) };
            assert!(shared_id >= 0);
            // SAFETY: IPC_PRIVATE creates a new test-owned semaphore set.
            let semaphore = unsafe { semget(IPC_PRIVATE, 2, IPC_CREAT | 0o600) };
            if semaphore < 0 {
                // SAFETY: remove the segment created immediately above.
                unsafe { shmctl(shared_id, IPC_RMID, std::ptr::null_mut()) };
                panic!("failed to create private semaphore set");
            }
            Self {
                shared_id,
                semaphore,
            }
        }

        fn write_direct_ring(&self) {
            // SAFETY: attach the test-owned segment read-write for setup.
            let shared = unsafe { shmat(self.shared_id, std::ptr::null(), 0) };
            assert_ne!(shared as isize, -1);
            let bytes = shared.cast::<u8>();
            // SAFETY: the test segment has an eight-byte header and 32-byte body.
            unsafe {
                std::ptr::write_unaligned(bytes.cast::<i32>(), 32);
                std::ptr::write_unaligned(bytes.add(4).cast::<i32>(), 10);
                let data = std::slice::from_raw_parts_mut(bytes.add(SYSLOG_HEADER_LEN), 32);
                data.fill(b'x');
                data[3] = 0;
                data[4..10].copy_from_slice(b"line\n\0");
                assert_eq!(shmdt(shared), 0);
            }
        }

        fn attachment_count(&self) -> usize {
            let mut info = ShmidDs { bytes: [0; 112] };
            // SAFETY: info is the amd64 glibc shmid_ds layout proven by the C probe.
            assert_eq!(unsafe { shmctl(self.shared_id, 2, &raw mut info) }, 0);
            usize::from_ne_bytes(
                info.bytes[88..96]
                    .try_into()
                    .expect("verified shm_nattch offset"),
            )
        }
    }

    impl Drop for PrivateResources {
        fn drop(&mut self) {
            // SAFETY: both IPC_PRIVATE resources are owned only by this test.
            unsafe {
                shmctl(self.shared_id, IPC_RMID, std::ptr::null_mut());
                semctl(self.semaphore, 0, IPC_RMID);
            }
        }
    }

    struct PrivateIpc {
        shared_id: c_int,
        semaphore: c_int,
    }

    impl BusyboxIpc for PrivateIpc {
        type Attachment = *mut c_void;

        fn shared_id(&mut self) -> Result<i32, BackendError> {
            Ok(self.shared_id)
        }

        fn segment_size(&mut self, shared_id: i32) -> Result<usize, BackendError> {
            LinuxBusyboxIpc.segment_size(shared_id)
        }

        fn attach(&mut self, shared_id: i32) -> Result<Self::Attachment, BackendError> {
            LinuxBusyboxIpc.attach(shared_id)
        }

        fn semaphore(&mut self) -> Result<i32, BackendError> {
            Ok(self.semaphore)
        }

        fn lock(&mut self, semaphore: i32) -> Result<(), BackendError> {
            LinuxBusyboxIpc.lock(semaphore)
        }

        fn read(
            &mut self,
            attachment: Self::Attachment,
            segment_size: usize,
        ) -> Result<Vec<u8>, BackendError> {
            LinuxBusyboxIpc.read(attachment, segment_size)
        }

        fn unlock(&mut self, semaphore: i32) -> Result<(), BackendError> {
            LinuxBusyboxIpc.unlock(semaphore)
        }

        fn detach(&mut self, attachment: Self::Attachment) -> Result<(), BackendError> {
            LinuxBusyboxIpc.detach(attachment)
        }
    }

    #[test]
    fn private_sysv_resources_round_trip_and_release_the_lock() {
        let resources = PrivateResources::create(SYSLOG_HEADER_LEN + 32);
        resources.write_direct_ring();
        let mut ipc = PrivateIpc {
            shared_id: resources.shared_id,
            semaphore: resources.semaphore,
        };
        assert_eq!(read_busybox_syslog_from(&mut ipc), Ok(b"line\n".to_vec()));
        assert_eq!(resources.attachment_count(), 0);
        // SAFETY: GETVAL reads the test-owned semaphore value.
        assert_eq!(unsafe { semctl(resources.semaphore, 0, GETVAL) }, 0);
    }

    #[test]
    fn private_sysv_lock_timeout_is_bounded_and_detaches() {
        let resources = PrivateResources::create(SYSLOG_HEADER_LEN + 32);
        resources.write_direct_ring();
        // SAFETY: SETVAL changes only semaphore 1 in the test-owned set.
        assert_eq!(unsafe { semctl(resources.semaphore, 1, SETVAL, 1) }, 0);
        let mut ipc = PrivateIpc {
            shared_id: resources.shared_id,
            semaphore: resources.semaphore,
        };
        let start = Instant::now();
        assert_eq!(
            read_busybox_syslog_from(&mut ipc),
            Err(BackendError::Unavailable)
        );
        assert!(start.elapsed() < Duration::from_secs(1));
        assert_eq!(resources.attachment_count(), 0);
    }
}
