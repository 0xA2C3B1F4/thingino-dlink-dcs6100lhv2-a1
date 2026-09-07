mod commands;
mod config;
mod discovery;
mod motion;
mod mqtt;
mod service;
mod snapshot;
mod state;

use commands::{Command, DayNightCommand};
pub(in crate::camera) use service::HaService;

use super::*;

const MOTION_GUARD_POLL_INTERVAL: Duration = Duration::from_millis(20);

fn wait_for_motion_guard(expected: bool, deadline: Instant, current: impl Fn() -> bool) -> bool {
    loop {
        if current() == expected {
            return true;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return false;
        }
        thread::sleep(remaining.min(MOTION_GUARD_POLL_INTERVAL));
    }
}

impl PrudyntBackend {
    pub(super) fn ha_execute_command(
        &self,
        command: Command,
    ) -> Result<Option<Vec<u8>>, BackendError> {
        match command {
            Command::MotionGuard(enabled) => {
                let _guard = self
                    .config_lock
                    .lock()
                    .map_err(|_| BackendError::Unavailable)?;
                let deadline = Instant::now() + Duration::from_secs(3);
                self.update_prudynt_config(
                    object([("motion", object([("enabled", Value::Bool(enabled))]))])
                        .to_json()
                        .as_bytes(),
                    deadline,
                )?;
                if !wait_for_motion_guard(enabled, deadline, || self.motion.snapshot().monitoring) {
                    return Err(BackendError::Unavailable);
                }
            }
            Command::IrCut(enabled) => {
                let _guard = self
                    .config_lock
                    .lock()
                    .map_err(|_| BackendError::Unavailable)?;
                self.set_ircut(enabled)?;
                self.disable_automatic_daynight()?;
            }
            Command::DayNight(mode) => {
                let mode = match mode {
                    DayNightCommand::Auto => DayNightMode::Auto,
                    DayNightCommand::Day => DayNightMode::Day,
                    DayNightCommand::Night => DayNightMode::Night,
                };
                self.daynight(mode, Instant::now() + Duration::from_secs(3))?;
            }
            Command::Privacy(enabled) => {
                let _guard = self
                    .config_lock
                    .lock()
                    .map_err(|_| BackendError::Unavailable)?;
                self.update_prudynt_config(
                    object([(
                        "privacy",
                        object([
                            ("enabled", Value::Bool(enabled)),
                            ("stream0_enabled", Value::Bool(enabled)),
                            ("stream1_enabled", Value::Bool(enabled)),
                        ]),
                    )])
                    .to_json()
                    .as_bytes(),
                    Instant::now() + Duration::from_secs(3),
                )?;
            }
            Command::Color(enabled) => {
                let _guard = self
                    .config_lock
                    .lock()
                    .map_err(|_| BackendError::Unavailable)?;
                self.update_prudynt_config(
                    object([(
                        "image",
                        object([(
                            "running_mode",
                            number(super::actions::color_running_mode(enabled)),
                        )]),
                    )])
                    .to_json()
                    .as_bytes(),
                    Instant::now() + Duration::from_secs(3),
                )?;
                self.disable_automatic_daynight()?;
            }
            Command::Ir850(enabled) => {
                let _guard = self
                    .config_lock
                    .lock()
                    .map_err(|_| BackendError::Unavailable)?;
                self.set_light("ir850", enabled)?;
                self.disable_automatic_daynight()?;
            }
            Command::Snapshot => return super::ha::snapshot::capture(self, 0).map(Some),
            Command::Reboot => {
                self.reboot()?;
            }
        }
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};

    #[test]
    fn motion_guard_waits_for_delayed_confirmation() {
        let monitoring = Arc::new(AtomicBool::new(false));
        let delayed = Arc::clone(&monitoring);
        let worker = thread::spawn(move || {
            thread::sleep(Duration::from_millis(30));
            delayed.store(true, Ordering::Release);
        });
        assert!(wait_for_motion_guard(
            true,
            Instant::now() + Duration::from_millis(250),
            || monitoring.load(Ordering::Acquire)
        ));
        worker.join().unwrap();
    }

    #[test]
    fn motion_guard_confirmation_timeout_is_bounded() {
        let start = Instant::now();
        assert!(!wait_for_motion_guard(
            true,
            start + Duration::from_millis(30),
            || false
        ));
        assert!(start.elapsed() < Duration::from_millis(250));
    }
}
