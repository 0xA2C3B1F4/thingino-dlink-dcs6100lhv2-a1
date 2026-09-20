mod backend;
mod commands;
mod config;
mod discovery;
mod motion;
mod mqtt;
mod service;
mod snapshot;
mod state;

pub(crate) use commands::{Command, DayNightCommand};
pub(in crate::camera) use config::{
    MIN_CAMERA_INTERVAL, MIN_DISCOVERY_INTERVAL, MIN_STATE_INTERVAL,
};
pub(crate) use service::HaService;

#[cfg(test)]
use super::*;

#[cfg(all(test, feature = "raptor-backend"))]
mod raptor_tests;
