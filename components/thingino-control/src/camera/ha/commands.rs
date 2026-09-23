use super::config::HaConfig;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Command {
    MotionGuard(bool),
    IrCut(bool),
    DayNight(DayNightCommand),
    Privacy(bool),
    Color(bool),
    Ir850(bool),
    Snapshot,
    Reboot,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DayNightCommand {
    Auto,
    Day,
    Night,
}

pub(super) fn parse(config: &HaConfig, topic: &str, payload: &[u8]) -> Option<Command> {
    if topic.len() > super::mqtt::MAX_TOPIC_BYTES
        || payload.len() > super::mqtt::MAX_COMMAND_PAYLOAD_BYTES
    {
        return None;
    }
    let entity = topic
        .strip_prefix(&format!("{}/", config.base_topic()))?
        .strip_suffix("/set")?;
    if entity.contains('/') {
        return None;
    }
    match (entity, payload) {
        ("motion_guard", b"ON") if config.entities.motion_guard => Some(Command::MotionGuard(true)),
        ("motion_guard", b"OFF") if config.entities.motion_guard => {
            Some(Command::MotionGuard(false))
        }
        ("ircut", b"ON") if config.entities.ircut => Some(Command::IrCut(true)),
        ("ircut", b"OFF") if config.entities.ircut => Some(Command::IrCut(false)),
        ("daynight", b"auto") if config.entities.daynight => {
            Some(Command::DayNight(DayNightCommand::Auto))
        }
        ("daynight", b"day") if config.entities.daynight => {
            Some(Command::DayNight(DayNightCommand::Day))
        }
        ("daynight", b"night") if config.entities.daynight => {
            Some(Command::DayNight(DayNightCommand::Night))
        }
        ("privacy", b"ON") if config.entities.privacy => Some(Command::Privacy(true)),
        ("privacy", b"OFF") if config.entities.privacy => Some(Command::Privacy(false)),
        ("color", b"ON") if config.entities.color => Some(Command::Color(true)),
        ("color", b"OFF") if config.entities.color => Some(Command::Color(false)),
        ("ir850", b"ON") if config.entities.ir850 => Some(Command::Ir850(true)),
        ("ir850", b"OFF") if config.entities.ir850 => Some(Command::Ir850(false)),
        ("snapshot", b"1") if config.entities.snapshot => Some(Command::Snapshot),
        ("reboot", b"1") if config.entities.reboot => Some(Command::Reboot),
        _ => None,
    }
}

pub(super) fn command_topics(config: &HaConfig) -> Vec<String> {
    let base = config.base_topic();
    [
        ("motion_guard", config.entities.motion_guard),
        ("ircut", config.entities.ircut),
        ("daynight", config.entities.daynight),
        ("privacy", config.entities.privacy),
        ("color", config.entities.color),
        ("ir850", config.entities.ir850),
        ("snapshot", config.entities.snapshot),
        ("reboot", config.entities.reboot),
    ]
    .into_iter()
    .filter(|(_, enabled)| *enabled)
    .map(|(entity, _)| format!("{base}/{entity}/set"))
    .collect()
}

#[cfg(test)]
mod tests {
    use super::super::discovery::test_config;
    use super::*;

    #[test]
    fn commands_accept_only_exact_topics_and_payloads() {
        let mut config = test_config();
        config.entities.reboot = true;
        for (entity, payload, expected) in [
            ("motion_guard", b"ON".as_slice(), Command::MotionGuard(true)),
            ("motion_guard", b"OFF", Command::MotionGuard(false)),
            ("ircut", b"ON", Command::IrCut(true)),
            ("ircut", b"OFF", Command::IrCut(false)),
            (
                "daynight",
                b"auto",
                Command::DayNight(DayNightCommand::Auto),
            ),
            ("daynight", b"day", Command::DayNight(DayNightCommand::Day)),
            (
                "daynight",
                b"night",
                Command::DayNight(DayNightCommand::Night),
            ),
            ("privacy", b"ON", Command::Privacy(true)),
            ("privacy", b"OFF", Command::Privacy(false)),
            ("color", b"ON", Command::Color(true)),
            ("color", b"OFF", Command::Color(false)),
            ("ir850", b"ON", Command::Ir850(true)),
            ("ir850", b"OFF", Command::Ir850(false)),
            ("snapshot", b"1", Command::Snapshot),
            ("reboot", b"1", Command::Reboot),
        ] {
            assert_eq!(
                parse(&config, &format!("cameras/camera/{entity}/set"), payload),
                Some(expected)
            );
        }
        for (topic, payload) in [
            ("cameras/camera/ircut/set", b"on".as_slice()),
            ("cameras/camera/motion_guard/set", b"TRUE"),
            ("cameras/camera/daynight/set", b"AUTO"),
            ("cameras/camera/privacy/set", b"1"),
            ("cameras/camera/color/set", b"toggle"),
            ("cameras/camera/ir850/set", b"on"),
            ("cameras/camera/snapshot/set", b"ON"),
            ("cameras/camera/reboot/set", b"ON"),
            ("cameras/camera/ircut/set/extra", b"ON"),
            ("cameras/other/ircut/set", b"ON"),
            ("cameras/camera/ir940/set", b"ON"),
        ] {
            assert_eq!(parse(&config, topic, payload), None);
        }
        assert_eq!(
            parse(&config, "cameras/camera/ircut/set", &[b'X'; 17]),
            None
        );
        assert_eq!(
            parse(
                &config,
                &format!("cameras/camera/{}/set", "x".repeat(600)),
                b"ON",
            ),
            None
        );
        config.entities.privacy = false;
        assert_eq!(parse(&config, "cameras/camera/privacy/set", b"ON"), None);
    }
}
