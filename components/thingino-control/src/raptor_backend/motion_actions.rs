use std::sync::atomic::{AtomicBool, AtomicI8, AtomicU32, Ordering};
use std::sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::recorder::MotionEventStart;
use crate::camera::motion_events::{
    MotionEvent, MotionEventDisposition, MotionEventSink, MotionState,
};
use crate::{BackendError, RaptorBackend};

const QUEUE_CAPACITY: usize = 4;
const OWNER_DEADLINE: Duration = Duration::from_secs(3);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct Config {
    pub video_length: u64,
    pub send2storage: bool,
    pub playonspeaker: bool,
    pub speaker_repeats: u8,
}

struct OwnedRecording {
    channel: u64,
    stop_at: Instant,
    user_generation: u32,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) struct Snapshot {
    pub running: bool,
    pub owned_channel: Option<u8>,
    pub starts: u32,
    pub stops: u32,
    pub failures: u32,
    pub queue_dropped: u32,
    pub speaker_requests: u32,
    pub speaker_failures: u32,
    pub speaker_rate_limited: u32,
}

#[derive(Default)]
struct Runtime {
    running: AtomicBool,
    owned_channel: AtomicI8,
    starts: AtomicU32,
    stops: AtomicU32,
    failures: AtomicU32,
    queue_dropped: AtomicU32,
    speaker_requests: AtomicU32,
    speaker_failures: AtomicU32,
    speaker_rate_limited: AtomicU32,
}

pub(super) struct Service {
    sender: SyncSender<MotionEvent>,
    receiver: Mutex<Option<Receiver<MotionEvent>>>,
    downstream: Arc<dyn MotionEventSink>,
    webhook: Arc<dyn MotionEventSink>,
    ntfy: Arc<dyn MotionEventSink>,
    email: Arc<dyn MotionEventSink>,
    ftp: Arc<dyn MotionEventSink>,
    gotify: Arc<dyn MotionEventSink>,
    telegram: Arc<dyn MotionEventSink>,
    worker: Mutex<Option<JoinHandle<()>>>,
    runtime: Arc<Runtime>,
}

impl Service {
    pub(super) fn new(
        downstream: Arc<dyn MotionEventSink>,
        webhook: Arc<dyn MotionEventSink>,
        ntfy: Arc<dyn MotionEventSink>,
        email: Arc<dyn MotionEventSink>,
        ftp: Arc<dyn MotionEventSink>,
        gotify: Arc<dyn MotionEventSink>,
        telegram: Arc<dyn MotionEventSink>,
    ) -> Self {
        let (sender, receiver) = sync_channel(QUEUE_CAPACITY);
        let runtime = Arc::new(Runtime::default());
        runtime.owned_channel.store(-1, Ordering::Release);
        Self {
            sender,
            receiver: Mutex::new(Some(receiver)),
            downstream,
            webhook,
            ntfy,
            email,
            ftp,
            gotify,
            telegram,
            worker: Mutex::new(None),
            runtime,
        }
    }

    pub(super) fn start(
        self: &Arc<Self>,
        backend: Weak<RaptorBackend>,
    ) -> Result<(), BackendError> {
        let mut worker = self.worker.lock().map_err(|_| BackendError::Unavailable)?;
        if worker.is_some() {
            return Ok(());
        }
        let receiver = self
            .receiver
            .lock()
            .map_err(|_| BackendError::Unavailable)?
            .take()
            .ok_or(BackendError::Unavailable)?;
        let runtime = self.runtime.clone();
        *worker = Some(
            thread::Builder::new()
                .name("control-motion-actions".to_owned())
                .spawn(move || worker_loop(receiver, backend, runtime))
                .map_err(|_| BackendError::Unavailable)?,
        );
        Ok(())
    }

    pub(super) fn snapshot(&self) -> Snapshot {
        let owned_channel = self.runtime.owned_channel.load(Ordering::Acquire);
        Snapshot {
            running: self.runtime.running.load(Ordering::Acquire),
            owned_channel: u8::try_from(owned_channel).ok(),
            starts: self.runtime.starts.load(Ordering::Relaxed),
            stops: self.runtime.stops.load(Ordering::Relaxed),
            failures: self.runtime.failures.load(Ordering::Relaxed),
            queue_dropped: self.runtime.queue_dropped.load(Ordering::Relaxed),
            speaker_requests: self.runtime.speaker_requests.load(Ordering::Relaxed),
            speaker_failures: self.runtime.speaker_failures.load(Ordering::Relaxed),
            speaker_rate_limited: self.runtime.speaker_rate_limited.load(Ordering::Relaxed),
        }
    }
}

impl MotionEventSink for Service {
    fn try_send_motion(&self, event: MotionEvent) -> MotionEventDisposition {
        let downstream = self.downstream.try_send_motion(event.clone());
        let webhook = self.webhook.try_send_motion(event.clone());
        let ntfy = self.ntfy.try_send_motion(event.clone());
        let email = self.email.try_send_motion(event.clone());
        let ftp = self.ftp.try_send_motion(event.clone());
        let gotify = self.gotify.try_send_motion(event.clone());
        let telegram = self.telegram.try_send_motion(event.clone());
        if event.state == MotionState::Active {
            match self.sender.try_send(event) {
                Ok(()) => {
                    return if matches!(
                        downstream,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        downstream
                    } else if matches!(
                        webhook,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        webhook
                    } else if matches!(
                        ntfy,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        ntfy
                    } else if matches!(
                        email,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        email
                    } else if matches!(
                        ftp,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        ftp
                    } else if matches!(
                        gotify,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        gotify
                    } else if matches!(
                        telegram,
                        MotionEventDisposition::Queued | MotionEventDisposition::Coalesced
                    ) {
                        telegram
                    } else {
                        MotionEventDisposition::Queued
                    };
                }
                Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {
                    self.runtime.queue_dropped.fetch_add(1, Ordering::Relaxed);
                    if downstream == MotionEventDisposition::Disabled
                        && webhook == MotionEventDisposition::Disabled
                        && ntfy == MotionEventDisposition::Disabled
                        && email == MotionEventDisposition::Disabled
                        && ftp == MotionEventDisposition::Disabled
                        && gotify == MotionEventDisposition::Disabled
                        && telegram == MotionEventDisposition::Disabled
                    {
                        return MotionEventDisposition::Dropped;
                    }
                }
            }
        }
        if downstream == MotionEventDisposition::Disabled {
            if webhook == MotionEventDisposition::Disabled {
                if ntfy != MotionEventDisposition::Disabled {
                    ntfy
                } else if email != MotionEventDisposition::Disabled {
                    email
                } else if ftp != MotionEventDisposition::Disabled {
                    ftp
                } else if gotify != MotionEventDisposition::Disabled {
                    gotify
                } else {
                    telegram
                }
            } else {
                webhook
            }
        } else {
            downstream
        }
    }
}

fn worker_loop(
    receiver: Receiver<MotionEvent>,
    backend: Weak<RaptorBackend>,
    runtime: Arc<Runtime>,
) {
    runtime.running.store(true, Ordering::Release);
    let mut owned: Option<OwnedRecording> = None;
    let mut last_speaker_request: Option<Instant> = None;
    loop {
        let timeout = owned
            .as_ref()
            .map(|item| item.stop_at.saturating_duration_since(Instant::now()))
            .unwrap_or(Duration::from_millis(200))
            .min(Duration::from_millis(200));
        match receiver.recv_timeout(timeout) {
            Ok(event) => {
                let Some(backend) = backend.upgrade() else {
                    break;
                };
                match backend.motion_action_config(Instant::now() + Duration::from_millis(300)) {
                    Ok(config) => {
                        if config.playonspeaker {
                            let now = Instant::now();
                            if last_speaker_request.is_some_and(|last| {
                                now.duration_since(last) < Duration::from_secs(1)
                            }) {
                                runtime.speaker_rate_limited.fetch_add(1, Ordering::Relaxed);
                            } else {
                                last_speaker_request = Some(now);
                                match backend.motion_event_play_speaker(
                                    config.speaker_repeats,
                                    now + Duration::from_millis(300),
                                ) {
                                    Ok(()) => {
                                        runtime.speaker_requests.fetch_add(1, Ordering::Relaxed);
                                    }
                                    Err(_) => {
                                        runtime.speaker_failures.fetch_add(1, Ordering::Relaxed);
                                    }
                                }
                            }
                        }
                        if config.send2storage {
                            let stop_at = Instant::now() + Duration::from_secs(config.video_length);
                            if let Some(current) = owned.as_mut() {
                                if current.channel == u64::from(event.channel) {
                                    current.stop_at = current.stop_at.max(stop_at);
                                }
                            } else {
                                let channel = u64::from(event.channel);
                                match backend.motion_event_start_recording(
                                    channel,
                                    Instant::now() + OWNER_DEADLINE,
                                ) {
                                    Ok(MotionEventStart::Owned {
                                        generation,
                                        confirmed,
                                    }) => {
                                        if confirmed {
                                            runtime.starts.fetch_add(1, Ordering::Relaxed);
                                        } else {
                                            runtime.failures.fetch_add(1, Ordering::Relaxed);
                                        }
                                        runtime.owned_channel.store(
                                            i8::try_from(channel).unwrap_or(-1),
                                            Ordering::Release,
                                        );
                                        owned = Some(OwnedRecording {
                                            channel,
                                            stop_at,
                                            user_generation: generation,
                                        });
                                    }
                                    Ok(MotionEventStart::NotOwned) => {}
                                    Err(_) => {
                                        runtime.failures.fetch_add(1, Ordering::Relaxed);
                                    }
                                }
                            }
                        }
                    }
                    Err(_) => {
                        runtime.failures.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
        if owned
            .as_ref()
            .is_some_and(|current| Instant::now() >= current.stop_at)
        {
            let Some(backend) = backend.upgrade() else {
                break;
            };
            if let Some(current) = owned.as_ref() {
                match backend.motion_event_stop_recording(
                    current.channel,
                    current.user_generation,
                    Instant::now() + OWNER_DEADLINE,
                ) {
                    Ok(stopped) => {
                        if stopped {
                            runtime.stops.fetch_add(1, Ordering::Relaxed);
                        }
                        runtime.owned_channel.store(-1, Ordering::Release);
                        owned = None;
                    }
                    Err(_) => {
                        runtime.failures.fetch_add(1, Ordering::Relaxed);
                        if let Some(current) = owned.as_mut() {
                            current.stop_at = Instant::now() + Duration::from_secs(1);
                        }
                    }
                }
            }
        }
    }
    runtime.owned_channel.store(-1, Ordering::Release);
    runtime.running.store(false, Ordering::Release);
}

#[cfg(test)]
mod tests {
    use super::super::motion::tests::sequence as motion_sequence;
    use super::super::recorder::tests::{get, sequence as recorder_sequence, set, state};
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use crate::camera::motion_events::MOTION_EVENT_VERSION;
    use std::fs;
    use std::io::Write;
    use std::os::unix::net::UnixListener;
    use std::sync::Condvar;

    fn active() -> MotionEvent {
        MotionEvent {
            version: MOTION_EVENT_VERSION,
            sequence: 1,
            state: MotionState::Active,
            channel: 1,
            monotonic_ms: 1,
            occurred_unix_ms: None,
            snapshot: None,
            clip: None,
        }
    }

    fn recording_motion_sequence(
        root: &std::path::Path,
        mut pairs: Vec<(Vec<u8>, Vec<u8>)>,
        checks: usize,
    ) -> thread::JoinHandle<()> {
        let mut guards = Vec::new();
        for _ in 0..checks {
            guards.push((
                br#"{"cmd":"get-stream-enabled","stream_id":1}"#.to_vec(),
                br#"{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":1,"supported":true,"editable":true,"required":false,"active_enabled":true,"configured_enabled":true,"pending_restart":false,"motion_blocks_disable":false,"recorder_blocks_disable":false}"#.to_vec(),
            ));
            guards.push((
                br#"{"cmd":"config-read-section","section":"stream1"}"#.to_vec(),
                br#"{"status":"ok","section":"stream1","keys":{"enabled":"true"}}"#.to_vec(),
            ));
        }
        pairs.splice(1..1, guards);
        motion_sequence(root, pairs)
    }

    struct HeldWebhook {
        release: Arc<(Mutex<bool>, Condvar)>,
    }

    struct HeldNtfy {
        release: Arc<(Mutex<bool>, Condvar)>,
    }

    impl super::super::motion_ntfy::Transport for HeldNtfy {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _url: &str, _token: &str, _body: &[u8]) -> Result<u16, ()> {
            let (lock, wake) = &*self.release;
            let held = lock.lock().unwrap();
            let _ = wake
                .wait_timeout_while(held, Duration::from_secs(2), |released| !*released)
                .unwrap();
            Err(())
        }
    }

    struct CountingWebhook(AtomicU32);

    impl super::super::motion_webhook::Transport for CountingWebhook {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _url: &str, _body: &[u8]) -> Result<u16, ()> {
            self.0.fetch_add(1, Ordering::Relaxed);
            Ok(204)
        }
    }

    impl super::super::motion_webhook::Transport for HeldWebhook {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _url: &str, _body: &[u8]) -> Result<u16, ()> {
            let (lock, wake) = &*self.release;
            let held = lock.lock().unwrap();
            let _ = wake
                .wait_timeout_while(held, Duration::from_secs(2), |released| !*released)
                .unwrap();
            Err(())
        }
    }

    #[test]
    fn stalled_webhook_does_not_delay_owned_recording_expiry() {
        let root = task_temp("motion-action-worker");
        let rvd = recording_motion_sequence(&root, vec![(
            br#"{"cmd":"get-motion-actions"}"#.to_vec(),
            br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
        )], 1);
        let stopped = state(1, Some(false), true, true, None);
        let running = state(1, Some(true), false, true, None);
        let rmr = recorder_sequence(
            &root,
            1,
            vec![
                (get(), stopped),
                (set(true), running.clone()),
                (get(), running.clone()),
                (get(), running.clone()),
                (get(), running),
                (set(false), state(1, Some(false), true, true, None)),
                (get(), state(1, Some(false), true, true, None)),
            ],
        );
        let release = Arc::new((Mutex::new(false), Condvar::new()));
        let webhook = Arc::new(super::super::motion_webhook::Service::with_transport(
            Arc::new(HeldWebhook {
                release: release.clone(),
            }),
        ));
        webhook
            .apply(super::super::motion_webhook::Config {
                enabled: true,
                url: "http://127.0.0.1/motion".to_owned(),
            })
            .unwrap();
        webhook.start().unwrap();
        let mut fixture = backend(&root, "127.0.0.1:9".parse().unwrap());
        fixture.motion_webhook = webhook.clone();
        fixture.motion_actions = Arc::new(Service::new(
            fixture.ha.clone(),
            webhook,
            fixture.motion_ntfy.clone(),
            fixture.motion_email.clone(),
            fixture.motion_ftp.clone(),
            fixture.motion_gotify.clone(),
            fixture.motion_telegram.clone(),
        ));
        let backend = Arc::new(fixture);
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        let deadline = Instant::now() + Duration::from_secs(4);
        while backend.motion_actions.snapshot().stops == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(
            backend.motion_actions.snapshot(),
            Snapshot {
                running: true,
                owned_channel: None,
                starts: 1,
                stops: 1,
                failures: 0,
                queue_dropped: 0,
                speaker_requests: 0,
                speaker_failures: 0,
                speaker_rate_limited: 0,
            }
        );
        let (lock, wake) = &*release;
        *lock.lock().unwrap() = true;
        wake.notify_all();
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn stalled_ntfy_does_not_delay_webhook_or_owned_recording_expiry() {
        let root = task_temp("motion-action-ntfy-worker");
        let rvd = recording_motion_sequence(&root, vec![(
            br#"{"cmd":"get-motion-actions"}"#.to_vec(),
            br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
        )], 1);
        let stopped = state(1, Some(false), true, true, None);
        let running = state(1, Some(true), false, true, None);
        let rmr = recorder_sequence(
            &root,
            1,
            vec![
                (get(), stopped),
                (set(true), running.clone()),
                (get(), running.clone()),
                (get(), running.clone()),
                (get(), running),
                (set(false), state(1, Some(false), true, true, None)),
                (get(), state(1, Some(false), true, true, None)),
            ],
        );
        let release = Arc::new((Mutex::new(false), Condvar::new()));
        let ntfy = Arc::new(super::super::motion_ntfy::Service::with_transport(
            Arc::new(HeldNtfy {
                release: release.clone(),
            }),
        ));
        ntfy.apply(super::super::motion_ntfy::Config {
            enabled: true,
            url: "http://127.0.0.1/private-topic".to_owned(),
            token: String::new(),
        })
        .unwrap();
        ntfy.start().unwrap();
        let webhook_transport = Arc::new(CountingWebhook(AtomicU32::new(0)));
        let webhook = Arc::new(super::super::motion_webhook::Service::with_transport(
            webhook_transport.clone(),
        ));
        webhook
            .apply(super::super::motion_webhook::Config {
                enabled: true,
                url: "http://127.0.0.1/motion".to_owned(),
            })
            .unwrap();
        webhook.start().unwrap();
        let mut fixture = backend(&root, "127.0.0.1:9".parse().unwrap());
        fixture.motion_ntfy = ntfy.clone();
        fixture.motion_webhook = webhook.clone();
        fixture.motion_actions = Arc::new(Service::new(
            fixture.ha.clone(),
            webhook,
            ntfy,
            fixture.motion_email.clone(),
            fixture.motion_ftp.clone(),
            fixture.motion_gotify.clone(),
            fixture.motion_telegram.clone(),
        ));
        let backend = Arc::new(fixture);
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        let deadline = Instant::now() + Duration::from_secs(4);
        while (backend.motion_actions.snapshot().stops == 0
            || webhook_transport.0.load(Ordering::Acquire) == 0)
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(backend.motion_actions.snapshot().starts, 1);
        assert_eq!(backend.motion_actions.snapshot().stops, 1);
        assert_eq!(webhook_transport.0.load(Ordering::Acquire), 1);
        let (lock, wake) = &*release;
        *lock.lock().unwrap() = true;
        wake.notify_all();
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn action_queue_is_bounded_and_still_forwards_downstream() {
        struct Downstream(AtomicU32);
        impl MotionEventSink for Downstream {
            fn try_send_motion(&self, _event: MotionEvent) -> MotionEventDisposition {
                self.0.fetch_add(1, Ordering::Relaxed);
                MotionEventDisposition::Queued
            }
        }
        let downstream = Arc::new(Downstream(AtomicU32::new(0)));
        let service = Service::new(
            downstream.clone(),
            Arc::new(crate::camera::motion_events::DisabledMotionEventSink),
            Arc::new(crate::camera::motion_events::DisabledMotionEventSink),
            Arc::new(crate::camera::motion_events::DisabledMotionEventSink),
            Arc::new(crate::camera::motion_events::DisabledMotionEventSink),
            Arc::new(crate::camera::motion_events::DisabledMotionEventSink),
            Arc::new(crate::camera::motion_events::DisabledMotionEventSink),
        );
        for _ in 0..=QUEUE_CAPACITY {
            assert_eq!(
                service.try_send_motion(active()),
                MotionEventDisposition::Queued
            );
        }
        assert_eq!(downstream.0.load(Ordering::Relaxed), 5);
        assert_eq!(service.snapshot().queue_dropped, 1);
    }

    #[test]
    fn invalid_event_channel_is_counted_without_indexing_an_owner() {
        let root = task_temp("motion-action-channel");
        let rvd = motion_sequence(&root, vec![(
            br#"{"cmd":"get-motion-actions"}"#.to_vec(),
            br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
        )]);
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        let mut invalid = active();
        invalid.channel = 2;
        backend.motion_actions.try_send_motion(invalid);
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_actions.snapshot().failures == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(backend.motion_actions.snapshot().failures, 1);
        rvd.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn preexisting_recording_is_never_event_owned_or_stopped() {
        let root = task_temp("motion-action-existing");
        let rvd = recording_motion_sequence(
            &root,
            vec![(
                br#"{"cmd":"get-motion-actions"}"#.to_vec(),
                br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
            )], 1,
        );
        let rmr = recorder_sequence(
            &root,
            1,
            vec![(get(), state(1, Some(true), false, true, None))],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        rvd.join().unwrap();
        rmr.join().unwrap();
        thread::sleep(Duration::from_millis(30));
        let snapshot = backend.motion_actions.snapshot();
        assert_eq!(snapshot.starts, 0);
        assert_eq!(snapshot.stops, 0);
        assert_eq!(snapshot.owned_channel, None);
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unknown_preexisting_writer_is_never_claimed_or_started() {
        let root = task_temp("motion-action-existing-unknown");
        let rvd = recording_motion_sequence(
            &root,
            vec![(
                br#"{"cmd":"get-motion-actions"}"#.to_vec(),
                br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
            )], 1,
        );
        let rmr = recorder_sequence(
            &root,
            1,
            vec![(get(), state(1, None, false, true, Some("starting")))],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        let observed = Instant::now() + Duration::from_secs(1);
        while backend.motion_actions.snapshot().failures == 0 && Instant::now() < observed {
            thread::sleep(Duration::from_millis(5));
        }
        let snapshot = backend.motion_actions.snapshot();
        assert_eq!(snapshot.failures, 1, "{snapshot:?}");
        assert_eq!(snapshot.starts, 0);
        assert_eq!(snapshot.stops, 0);
        assert_eq!(snapshot.owned_channel, None);
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn expired_recording_stops_after_an_inflight_config_timeout() {
        let root = task_temp("motion-action-expiry");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let rvd = thread::spawn(move || {
            let mut motion_requests = 0;
            for _ in 0..4 {
                let (mut socket, _) = listener.accept().unwrap();
                let request = read_request(&mut socket);
                let response = match request.as_slice() {
                    br#"{"cmd":"get-motion-actions"}"# => {
                        motion_requests += 1;
                        if motion_requests == 2 {
                            thread::sleep(Duration::from_millis(500));
                            br#"{"status":"error"}"#.as_slice()
                        } else {
                            br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.as_slice()
                        }
                    }
                    br#"{"cmd":"get-stream-enabled","stream_id":1}"# => br#"{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":1,"supported":true,"editable":true,"required":false,"active_enabled":true,"configured_enabled":true,"pending_restart":false,"motion_blocks_disable":false,"recorder_blocks_disable":false}"#,
                    br#"{"cmd":"config-read-section","section":"stream1"}"# => br#"{"status":"ok","section":"stream1","keys":{"enabled":"true"}}"#,
                    other => panic!("unexpected RVD request: {other:?}"),
                };
                let _ = socket.write_all(&framed(response));
            }
        });
        let stopped = state(1, Some(false), true, true, None);
        let running = state(1, Some(true), false, true, None);
        let rmr = recorder_sequence(
            &root,
            1,
            vec![
                (get(), stopped),
                (set(true), running.clone()),
                (get(), running.clone()),
                (get(), running.clone()),
                (get(), running),
                (set(false), state(1, Some(false), true, true, None)),
                (get(), state(1, Some(false), true, true, None)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        let started = Instant::now() + Duration::from_secs(2);
        while backend.motion_actions.snapshot().starts == 0 && Instant::now() < started {
            thread::sleep(Duration::from_millis(5));
        }
        thread::sleep(Duration::from_millis(850));
        backend.motion_actions.try_send_motion(active());
        let prompt_stop = Instant::now() + Duration::from_millis(420);
        while backend.motion_actions.snapshot().stops == 0 && Instant::now() < prompt_stop {
            thread::sleep(Duration::from_millis(5));
        }
        let snapshot = backend.motion_actions.snapshot();
        assert_eq!(snapshot.stops, 1, "{snapshot:?}");
        assert_eq!(snapshot.failures, 1);
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn disabled_storage_traffic_does_not_extend_or_defer_an_owned_recording() {
        let root = task_temp("motion-action-disabled-storage");
        let enabled = br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec();
        let disabled = br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":false,"playonspeaker":false,"speaker_repeats":1}"#.to_vec();
        let mut actions = vec![(br#"{"cmd":"get-motion-actions"}"#.to_vec(), enabled)];
        actions.extend((0..12).map(|_| {
            (
                br#"{"cmd":"get-motion-actions"}"#.to_vec(),
                disabled.clone(),
            )
        }));
        let rvd = recording_motion_sequence(&root, actions, 1);
        let running = state(1, Some(true), false, true, None);
        let rmr = recorder_sequence(
            &root,
            1,
            vec![
                (get(), state(1, Some(false), true, true, None)),
                (set(true), running.clone()),
                (get(), running.clone()),
                (get(), running.clone()),
                (get(), running),
                (set(false), state(1, Some(false), true, true, None)),
                (get(), state(1, Some(false), true, true, None)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        let began = Instant::now();
        backend.motion_actions.try_send_motion(active());
        let start_deadline = began + Duration::from_secs(1);
        while backend.motion_actions.snapshot().starts == 0 && Instant::now() < start_deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(backend.motion_actions.snapshot().starts, 1);
        thread::sleep(Duration::from_millis(550));
        let mut stopped_at = None;
        for _ in 0..12 {
            backend.motion_actions.try_send_motion(active());
            thread::sleep(Duration::from_millis(100));
            if stopped_at.is_none() && backend.motion_actions.snapshot().stops == 1 {
                stopped_at = Some(Instant::now());
            }
        }
        let stopped_at = stopped_at.expect("owned recording was not stopped during event traffic");
        assert!(stopped_at.duration_since(began) < Duration::from_millis(1400));
        let snapshot = backend.motion_actions.snapshot();
        assert_eq!(snapshot.starts, 1, "{snapshot:?}");
        assert_eq!(snapshot.stops, 1, "{snapshot:?}");
        assert_eq!(snapshot.owned_channel, None, "{snapshot:?}");
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn uncertain_event_start_keeps_a_cleanup_obligation() {
        let root = task_temp("motion-action-uncertain-start");
        let rvd = recording_motion_sequence(&root, vec![(
            br#"{"cmd":"get-motion-actions"}"#.to_vec(),
            br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
        )], 1);
        let stopped = state(1, Some(false), true, true, None);
        let running = state(1, Some(true), false, true, None);
        let rmr = recorder_sequence(
            &root,
            1,
            vec![
                (get(), stopped),
                (set(true), br#"{"status":"error"}"#.to_vec()),
                (get(), running),
                (get(), state(1, Some(true), false, true, None)),
                (set(false), state(1, Some(false), true, true, None)),
                (get(), state(1, Some(false), true, true, None)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        let deadline = Instant::now() + Duration::from_secs(4);
        while backend.motion_actions.snapshot().stops == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(
            backend.motion_actions.snapshot(),
            Snapshot {
                running: true,
                owned_channel: None,
                starts: 0,
                stops: 1,
                failures: 1,
                queue_dropped: 0,
                speaker_requests: 0,
                speaker_failures: 0,
                speaker_rate_limited: 0,
            }
        );
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn manual_generation_takes_over_an_uncertain_event_start() {
        let root = task_temp("motion-action-uncertain-takeover");
        let rvd = recording_motion_sequence(&root, vec![(
            br#"{"cmd":"get-motion-actions"}"#.to_vec(),
            br#"{"status":"ok","persistence":"checked-control-actions","video_length":1,"send2storage":true,"playonspeaker":false,"speaker_repeats":1}"#.to_vec(),
        )], 2);
        let stopped = state(1, Some(false), true, true, None);
        let running = state(1, Some(true), false, true, None);
        let rmr = recorder_sequence(
            &root,
            1,
            vec![
                (get(), stopped),
                (set(true), running.clone()),
                (get(), br#"{"status":"error"}"#.to_vec()),
                (get(), running),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        let owned = Instant::now() + Duration::from_secs(1);
        while backend.motion_actions.snapshot().owned_channel != Some(1) && Instant::now() < owned {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(backend.motion_actions.snapshot().owned_channel, Some(1));
        assert!(
            backend
                .live_control(
                    br#"{"mp4":{"start":{"channel":1}}}"#,
                    Instant::now() + Duration::from_secs(1),
                )
                .is_ok()
        );
        let released = Instant::now() + Duration::from_secs(2);
        while backend.motion_actions.snapshot().owned_channel.is_some() && Instant::now() < released
        {
            thread::sleep(Duration::from_millis(10));
        }
        let snapshot = backend.motion_actions.snapshot();
        assert_eq!(snapshot.owned_channel, None, "{snapshot:?}");
        assert_eq!(snapshot.starts, 0);
        assert_eq!(snapshot.stops, 0);
        assert_eq!(snapshot.failures, 1);
        rvd.join().unwrap();
        rmr.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn speaker_action_is_bounded_and_rate_limited_without_starting_storage() {
        let root = task_temp("motion-action-speaker");
        let action = br#"{"status":"ok","persistence":"checked-control-actions","video_length":10,"send2storage":false,"playonspeaker":true,"speaker_repeats":2}"#.to_vec();
        let rvd = motion_sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), action.clone()),
                (br#"{"cmd":"get-motion-actions"}"#.to_vec(), action),
            ],
        );
        let listener = UnixListener::bind(root.join("rad.sock")).unwrap();
        let rad = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(
                read_request(&mut socket),
                br#"{"cmd":"ao-play-clip","clip":"motion","repeats":2}"#
            );
            socket.write_all(&framed(br#"{"status":"ok","clip":"motion","playback":"accepted","repeats":2,"sample_rate":16000,"max_duration_ms":10000,"talkback_active":false,"generation":1}"#)).unwrap();
        });
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        backend
            .motion_actions
            .start(Arc::downgrade(&backend))
            .unwrap();
        backend.motion_actions.try_send_motion(active());
        backend.motion_actions.try_send_motion(active());
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_actions.snapshot().speaker_rate_limited == 0
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(5));
        }
        let snapshot = backend.motion_actions.snapshot();
        assert_eq!(snapshot.speaker_requests, 1, "{snapshot:?}");
        assert_eq!(snapshot.speaker_failures, 0, "{snapshot:?}");
        assert_eq!(snapshot.speaker_rate_limited, 1, "{snapshot:?}");
        assert_eq!(snapshot.starts, 0, "{snapshot:?}");
        rvd.join().unwrap();
        rad.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }
}
