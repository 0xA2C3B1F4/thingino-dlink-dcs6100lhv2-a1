//! The HA worker shares the Raptor camera owner and its single MQTT client.
use super::{Command, config::HaConfig, service::HaService, state};
use crate::{BackendError, camera::CameraPaths};
use std::sync::{Arc, Weak};

#[derive(Clone)]
pub(super) enum HaBackend {
    #[cfg(feature = "raptor-backend")]
    Raptor(Arc<crate::RaptorBackend>),
}

pub(super) enum WeakBackend {
    #[cfg(feature = "raptor-backend")]
    Raptor(Weak<crate::RaptorBackend>),
}

impl WeakBackend {
    pub(super) fn upgrade(&self) -> Option<HaBackend> {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => value.upgrade().map(HaBackend::Raptor),
        }
    }
}

impl HaBackend {
    pub(super) fn downgrade(&self) -> WeakBackend {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => WeakBackend::Raptor(Arc::downgrade(value)),
        }
    }
    pub(super) fn is_raptor(&self) -> bool {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(_) => true,
        }
    }
    pub(super) fn paths(&self) -> &CameraPaths {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => value.ha_paths(),
        }
    }
    pub(super) fn service(&self) -> &Arc<HaService> {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => value.ha_service(),
        }
    }
    pub(super) fn collect(&self, config: &HaConfig) -> Result<state::States, BackendError> {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => state::collect_raptor(value, config),
        }
    }
    pub(super) fn execute(&self, command: Command) -> Result<Option<Vec<u8>>, BackendError> {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => value.ha_execute_command(command),
        }
    }
    pub(super) fn with_snapshot(
        &self,
        _stream: u8,
        current: &dyn Fn() -> bool,
        publish: impl FnOnce(&[u8]) -> Result<(), BackendError>,
    ) -> Result<(), BackendError> {
        let checked_publish = |bytes: &[u8]| {
            if !current() {
                return Err(BackendError::Unavailable);
            }
            publish(bytes)
        };
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => value.ha_publish_snapshot(checked_publish),
        }
    }
    pub(super) fn motion_active(&self) -> Option<bool> {
        match self {
            #[cfg(feature = "raptor-backend")]
            Self::Raptor(value) => value.ha_motion_active(),
        }
    }
}
