use super::*;

pub(super) struct Lease {
    generation: u32,
    privacy_generation: u32,
    mode: DayNightMode,
    original: [bool; 3],
    expected: [bool; 3],
    changed: [bool; 3],
}
fn mode(backend: &RaptorBackend, deadline: Instant) -> Result<DayNightMode, BackendError> {
    let reply = backend.command(RaptorDaemon::Ric, br#"{"cmd":"mode"}"#, deadline)?;
    let mode = match reply.value.get_path("mode").and_then(Value::as_str) {
        Some("day") => DayNightMode::Day,
        Some("night") => DayNightMode::Night,
        _ => return Err(BackendError::Unavailable),
    };
    validate_ric_mode(&reply, mode)?;
    Ok(mode)
}
fn field(backend: &RaptorBackend, index: usize, deadline: Instant) -> Result<bool, BackendError> {
    if index == 2 {
        return backend.color_state(deadline);
    }
    let fields = backend.ir_output_state(deadline)?;
    if !fields[index].0 {
        return Err(BackendError::Unavailable);
    }
    fields[index].1.ok_or(BackendError::Unavailable)
}
fn set(
    backend: &RaptorBackend,
    index: usize,
    value: bool,
    deadline: Instant,
) -> Result<(), BackendError> {
    if index == 2 {
        backend.set_color(value, deadline)?;
    } else {
        backend.set_ir_output(["ircut", "ir850"][index], value, deadline)?;
    }
    if field(backend, index, deadline)? != value {
        return Err(BackendError::Unavailable);
    }
    Ok(())
}
impl Lease {
    pub fn read(backend: &RaptorBackend) -> Result<Self, BackendError> {
        let generation = backend.timelapse_user_generation.load(Ordering::Acquire);
        let privacy_generation = backend.ha_privacy_generation.load(Ordering::Acquire);
        let deadline = Instant::now() + Duration::from_millis(300);
        let selected_mode = mode(backend, deadline)?;
        let original = [
            field(backend, 0, deadline)?,
            field(backend, 1, deadline)?,
            field(backend, 2, deadline)?,
        ];
        if backend.timelapse_user_generation.load(Ordering::Acquire) != generation {
            return Err(BackendError::Busy);
        }
        Ok(Self {
            generation,
            privacy_generation,
            mode: selected_mode,
            original,
            expected: original,
            changed: [false; 3],
        })
    }
    fn check(&self, backend: &RaptorBackend, deadline: Instant) -> Result<(), BackendError> {
        if backend.ha_privacy_generation.load(Ordering::Acquire) != self.privacy_generation
            || backend.timelapse_user_generation.load(Ordering::Acquire) != self.generation
            || backend.privacy_state(deadline)?
            || mode(backend, deadline)? != self.mode
        {
            return Err(BackendError::Busy);
        }
        Ok(())
    }
    pub fn apply(
        &mut self,
        backend: &RaptorBackend,
        desired: [bool; 3],
    ) -> Result<(), BackendError> {
        for (index, value) in desired.into_iter().enumerate() {
            if value == self.original[index] {
                continue;
            }
            let _guard = backend.lock_mutation()?;
            let deadline = Instant::now() + Duration::from_millis(200);
            self.check(backend, deadline)?;
            if field(backend, index, deadline)? != self.expected[index] {
                return Err(BackendError::Busy);
            }
            self.changed[index] = true;
            self.expected[index] = value;
            set(backend, index, value, deadline)?;
        }
        Ok(())
    }
    pub fn restore(
        &mut self,
        backend: &RaptorBackend,
        between_fields: impl Fn(),
    ) -> Result<(), BackendError> {
        for index in (0..3).rev() {
            if !self.changed[index] {
                continue;
            }
            {
                let _guard = backend.lock_mutation()?;
                let deadline = Instant::now() + Duration::from_millis(200);
                self.check(backend, deadline)?;
                let current = field(backend, index, deadline)?;
                if current != self.original[index] {
                    if current != self.expected[index] {
                        return Err(BackendError::Busy);
                    }
                    set(backend, index, self.original[index], deadline)?;
                }
                if field(backend, index, deadline)? != self.original[index] {
                    return Err(BackendError::Unavailable);
                }
                self.changed[index] = false;
            }
            // User changes can proceed here. The next field rechecks generation and ownership.
            between_fields();
        }
        Ok(())
    }
}
