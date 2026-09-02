use super::*;

pub(crate) struct BackendGate {
    active: Mutex<usize>,
    changed: Condvar,
}

impl BackendGate {
    fn new() -> Self {
        Self {
            active: Mutex::new(0),
            changed: Condvar::new(),
        }
    }

    pub(crate) fn acquire(self: &Arc<Self>, deadline: Instant) -> Option<BackendPermit> {
        let queue_deadline = Instant::now()
            .checked_add(BACKEND_QUEUE_TIMEOUT)
            .map_or(deadline, |bounded| bounded.min(deadline));
        let mut active = self.active.lock().ok()?;
        loop {
            if *active < MAX_BACKEND_OPERATIONS {
                *active += 1;
                return Some(BackendPermit {
                    gate: Arc::clone(self),
                });
            }
            let budget = remaining(queue_deadline)?;
            let (next, result) = self.changed.wait_timeout(active, budget).ok()?;
            active = next;
            if result.timed_out() {
                return None;
            }
        }
    }
}

pub(crate) struct BackendPermit {
    pub(crate) gate: Arc<BackendGate>,
}

impl Drop for BackendPermit {
    fn drop(&mut self) {
        if let Ok(mut active) = self.gate.active.lock() {
            *active = active.saturating_sub(1);
            self.gate.changed.notify_one();
        }
    }
}

pub(crate) struct SharedState {
    pub(crate) backend: Arc<dyn Backend>,
    pub(crate) token: Vec<u8>,
    pub(crate) web_auth: Option<WebAuth>,
    pub(crate) whip: Option<WhipProxy>,
    pub(crate) gate: Arc<BackendGate>,
}

pub(crate) struct QueuedConnection {
    pub(crate) stream: TcpStream,
    pub(crate) accepted_at: Instant,
}

fn worker(receiver: Arc<Mutex<Receiver<QueuedConnection>>>, state: Arc<SharedState>) {
    loop {
        let stream = {
            let Ok(receiver) = receiver.lock() else {
                return;
            };
            receiver.recv()
        };
        match stream {
            Ok(connection) => handle_client(
                connection.stream,
                &state,
                connection.accepted_at + TOTAL_TIMEOUT,
            ),
            Err(_) => return,
        }
    }
}

fn reject_busy(mut stream: TcpStream) {
    let _ = send_error(
        &mut stream,
        Instant::now() + CONNECTION_TIMEOUT,
        503,
        "server_busy",
        "connection queue is full",
    );
}

pub fn serve(
    listener: TcpListener,
    backend: Arc<dyn Backend>,
    token: Vec<u8>,
    shutdown: Arc<AtomicBool>,
) -> io::Result<()> {
    serve_with_web_auth(listener, backend, token, None, shutdown)
}

pub fn serve_with_web_auth(
    listener: TcpListener,
    backend: Arc<dyn Backend>,
    token: Vec<u8>,
    web_auth: Option<WebAuth>,
    shutdown: Arc<AtomicBool>,
) -> io::Result<()> {
    serve_with_web_auth_and_whip(listener, backend, token, web_auth, None, shutdown)
}

pub fn serve_with_web_auth_and_whip(
    listener: TcpListener,
    backend: Arc<dyn Backend>,
    token: Vec<u8>,
    web_auth: Option<WebAuth>,
    whip: Option<WhipProxy>,
    shutdown: Arc<AtomicBool>,
) -> io::Result<()> {
    if token.is_empty() || token.len() > 4096 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "token must contain 1 to 4096 bytes",
        ));
    }
    listener.set_nonblocking(true)?;
    let state = Arc::new(SharedState {
        backend,
        token,
        web_auth,
        whip,
        gate: Arc::new(BackendGate::new()),
    });
    let (sender, receiver): (SyncSender<QueuedConnection>, Receiver<QueuedConnection>) =
        mpsc::sync_channel(CONNECTION_QUEUE_CAPACITY);
    let receiver = Arc::new(Mutex::new(receiver));
    let mut workers = Vec::with_capacity(WORKER_COUNT);
    for _ in 0..WORKER_COUNT {
        let worker_receiver = Arc::clone(&receiver);
        let worker_state = Arc::clone(&state);
        workers.push(thread::spawn(move || worker(worker_receiver, worker_state)));
    }

    while !shutdown.load(Ordering::Acquire) {
        match listener.accept() {
            Ok((stream, _)) => {
                stream.set_nonblocking(false)?;
                let connection = QueuedConnection {
                    stream,
                    accepted_at: Instant::now(),
                };
                match sender.try_send(connection) {
                    Ok(()) => {}
                    Err(TrySendError::Full(connection)) => reject_busy(connection.stream),
                    Err(TrySendError::Disconnected(_)) => break,
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(1));
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    drop(sender);
    for worker in workers {
        let _ = worker.join();
    }
    Ok(())
}
