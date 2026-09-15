use super::super::*;
use super::config::HaConfig;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct Publication {
    pub(super) topic: String,
    pub(super) payload: Vec<u8>,
    pub(super) retained: bool,
}

struct Entity<'a> {
    component: &'a str,
    object_id: &'a str,
    name: &'a str,
    enabled: bool,
    kind: EntityKind<'a>,
}

enum EntityKind<'a> {
    BinarySensor {
        class: &'a str,
    },
    Switch {
        icon: &'a str,
    },
    Select,
    Sensor {
        icon: &'a str,
        unit: Option<&'a str>,
    },
    Button {
        icon: &'a str,
    },
    Camera,
}

pub(super) fn discovery_publications(config: &HaConfig, os_release: &Value) -> Vec<Publication> {
    let flags = config.entities;
    let entities = [
        Entity {
            component: "binary_sensor",
            object_id: "motion",
            name: "Motion",
            enabled: flags.motion,
            kind: EntityKind::BinarySensor { class: "motion" },
        },
        Entity {
            component: "switch",
            object_id: "motion_guard",
            name: "Motion Guard",
            enabled: flags.motion_guard,
            kind: EntityKind::Switch {
                icon: "mdi:motion-sensor",
            },
        },
        Entity {
            component: "switch",
            object_id: "ircut",
            name: "IR-cut",
            enabled: flags.ircut,
            kind: EntityKind::Switch {
                icon: "mdi:camera-iris",
            },
        },
        Entity {
            component: "select",
            object_id: "daynight",
            name: "Day/Night",
            enabled: flags.daynight,
            kind: EntityKind::Select,
        },
        Entity {
            component: "switch",
            object_id: "privacy",
            name: "Privacy",
            enabled: flags.privacy,
            kind: EntityKind::Switch {
                icon: "mdi:eye-off",
            },
        },
        Entity {
            component: "switch",
            object_id: "color",
            name: "Color mode",
            enabled: flags.color,
            kind: EntityKind::Switch {
                icon: "mdi:palette",
            },
        },
        Entity {
            component: "switch",
            object_id: "ir850",
            name: "850 nm IR LED",
            enabled: flags.ir850,
            kind: EntityKind::Switch { icon: "mdi:led-on" },
        },
        Entity {
            component: "sensor",
            object_id: "camera_config",
            name: "Camera configuration",
            enabled: true,
            kind: EntityKind::Sensor {
                icon: "mdi:cog",
                unit: None,
            },
        },
        Entity {
            component: "sensor",
            object_id: "firmware_version",
            name: "Firmware version",
            enabled: flags.firmware_version,
            kind: EntityKind::Sensor {
                icon: "mdi:memory",
                unit: None,
            },
        },
        Entity {
            component: "sensor",
            object_id: "firmware_timestamp",
            name: "Firmware build timestamp",
            enabled: flags.firmware_timestamp,
            kind: EntityKind::Sensor {
                icon: "mdi:calendar-clock",
                unit: None,
            },
        },
        Entity {
            component: "sensor",
            object_id: "gain",
            name: "Gain",
            enabled: flags.gain,
            kind: EntityKind::Sensor {
                icon: "mdi:brightness-6",
                unit: None,
            },
        },
        Entity {
            component: "sensor",
            object_id: "rssi",
            name: "Wi-Fi RSSI",
            enabled: flags.rssi,
            kind: EntityKind::Sensor {
                icon: "mdi:wifi",
                unit: Some("dBm"),
            },
        },
        Entity {
            component: "button",
            object_id: "snapshot",
            name: "Snapshot",
            enabled: flags.snapshot,
            kind: EntityKind::Button { icon: "mdi:camera" },
        },
        Entity {
            component: "camera",
            object_id: "live_view",
            name: "Camera preview",
            enabled: flags.live_view,
            kind: EntityKind::Camera,
        },
        Entity {
            component: "button",
            object_id: "reboot",
            name: "Reboot",
            enabled: flags.reboot,
            kind: EntityKind::Button {
                icon: "mdi:restart",
            },
        },
    ];
    let mut publications = entities
        .iter()
        .map(|entity| publication(config, os_release, entity))
        .collect::<Vec<_>>();
    // Clear retained legacy entities that do not exist or are not confirmed on A1.
    for (component, object_id) in [
        ("binary_sensor", "doorbell"),
        ("switch", "ir940"),
        ("switch", "white"),
        ("update", "ota"),
        ("update", "firmware"),
        ("sensor", "firmware_latest"),
        ("button", "ptz_up"),
        ("button", "ptz_down"),
        ("button", "ptz_left"),
        ("button", "ptz_right"),
        ("button", "ptz_home"),
    ] {
        publications.push(Publication {
            topic: discovery_topic(config, component, object_id),
            payload: Vec::new(),
            retained: true,
        });
    }
    publications
}

/// Raptor state availability is independent for each confirmed observation.
pub(super) fn raptor_publications(config: &HaConfig, release: &Value) -> Vec<Publication> {
    discovery_publications(config, release)
        .into_iter()
        .map(|mut publication| {
            if publication.payload.is_empty() {
                return publication;
            }
            let mut value = json::parse(&publication.payload).expect("generated discovery JSON");
            let entity = publication
                .topic
                .rsplit('/')
                .nth(1)
                .expect("generated entity topic");
            let global = value
                .get_path("availability")
                .expect("generated availability")
                .clone();
            value
                .set_path(
                    "availability",
                    Value::Array(vec![
                        global,
                        object([
                            (
                                "topic",
                                Value::String(format!(
                                    "{}/{entity}/availability",
                                    config.base_topic()
                                )),
                            ),
                            ("payload_available", Value::String("online".to_owned())),
                            ("payload_not_available", Value::String("offline".to_owned())),
                        ]),
                    ]),
                )
                .expect("generated discovery object");
            value
                .set_path("availability_mode", Value::String("all".to_owned()))
                .expect("generated discovery object");
            publication.payload = value.to_json().into_bytes();
            publication
        })
        .collect()
}

fn publication(config: &HaConfig, os_release: &Value, entity: &Entity<'_>) -> Publication {
    let topic = discovery_topic(config, entity.component, entity.object_id);
    if !entity.enabled {
        return Publication {
            topic,
            payload: Vec::new(),
            retained: true,
        };
    }
    let base = config.base_topic();
    let state_topic = format!("{base}/{}/state", entity.object_id);
    let command_topic = format!("{base}/{}/set", entity.object_id);
    let mut fields = BTreeMap::from([
        (
            "availability".to_owned(),
            object([
                ("topic", Value::String(config.availability_topic())),
                ("payload_available", Value::String("online".to_owned())),
                ("payload_not_available", Value::String("offline".to_owned())),
            ]),
        ),
        ("device".to_owned(), device(config, os_release)),
        ("name".to_owned(), Value::String(entity.name.to_owned())),
        ("qos".to_owned(), number(1)),
        (
            "unique_id".to_owned(),
            Value::String(format!(
                "thingino_{}_{}",
                config.device_id, entity.object_id
            )),
        ),
    ]);
    match entity.kind {
        EntityKind::BinarySensor { class } => {
            fields.insert("device_class".to_owned(), Value::String(class.to_owned()));
            fields.insert("payload_off".to_owned(), Value::String("OFF".to_owned()));
            fields.insert("payload_on".to_owned(), Value::String("ON".to_owned()));
            fields.insert("state_topic".to_owned(), Value::String(state_topic));
        }
        EntityKind::Switch { icon } => {
            fields.insert("command_topic".to_owned(), Value::String(command_topic));
            fields.insert("icon".to_owned(), Value::String(icon.to_owned()));
            fields.insert("payload_off".to_owned(), Value::String("OFF".to_owned()));
            fields.insert("payload_on".to_owned(), Value::String("ON".to_owned()));
            fields.insert("state_topic".to_owned(), Value::String(state_topic));
        }
        EntityKind::Select => {
            fields.insert("command_topic".to_owned(), Value::String(command_topic));
            fields.insert(
                "icon".to_owned(),
                Value::String("mdi:theme-light-dark".to_owned()),
            );
            fields.insert(
                "options".to_owned(),
                Value::Array(
                    ["auto", "day", "night"]
                        .into_iter()
                        .map(|value| Value::String(value.to_owned()))
                        .collect(),
                ),
            );
            fields.insert("state_topic".to_owned(), Value::String(state_topic));
        }
        EntityKind::Sensor { icon, unit } => {
            fields.insert(
                "entity_category".to_owned(),
                Value::String("diagnostic".to_owned()),
            );
            fields.insert("icon".to_owned(), Value::String(icon.to_owned()));
            fields.insert("state_topic".to_owned(), Value::String(state_topic));
            if let Some(unit) = unit {
                fields.insert(
                    "unit_of_measurement".to_owned(),
                    Value::String(unit.to_owned()),
                );
            }
            if entity.object_id == "rssi" {
                fields.insert(
                    "device_class".to_owned(),
                    Value::String("signal_strength".to_owned()),
                );
                fields.insert(
                    "state_class".to_owned(),
                    Value::String("measurement".to_owned()),
                );
            }
        }
        EntityKind::Button { icon } => {
            fields.insert("command_topic".to_owned(), Value::String(command_topic));
            fields.insert("icon".to_owned(), Value::String(icon.to_owned()));
            fields.insert("payload_press".to_owned(), Value::String("1".to_owned()));
        }
        EntityKind::Camera => {
            fields.insert(
                "topic".to_owned(),
                Value::String(format!("{base}/live_view/image")),
            );
        }
    }
    Publication {
        topic,
        payload: Value::Object(fields).to_json().into_bytes(),
        retained: true,
    }
}

fn discovery_topic(config: &HaConfig, component: &str, object_id: &str) -> String {
    format!(
        "{}/{}/thingino_{}/{}/config",
        config.discovery_prefix, component, config.device_id, object_id
    )
}

fn device(config: &HaConfig, os_release: &Value) -> Value {
    let version = os_release
        .get_path("COMMIT_ID")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let hardware = os_release
        .get_path("IMAGE_ID")
        .and_then(Value::as_str)
        .unwrap_or("A1");
    object([
        (
            "configuration_url",
            Value::String(format!("http://{}", config.device_id)),
        ),
        ("hw_version", Value::String(hardware.to_owned())),
        (
            "identifiers",
            Value::Array(vec![Value::String(format!(
                "thingino_{}",
                config.device_id
            ))]),
        ),
        (
            "manufacturer",
            Value::String("D-Link / Thingino".to_owned()),
        ),
        ("model", Value::String(config.device_model.clone())),
        ("name", Value::String(config.device_name.clone())),
        ("sw_version", Value::String(version.to_owned())),
    ])
}

#[cfg(test)]
pub(super) fn test_config() -> HaConfig {
    use super::config::EntityFlags;
    HaConfig {
        enabled: true,
        host: "broker".to_owned(),
        port: 1883,
        username: String::new(),
        password: String::new(),
        client_id: "thingino-ha-camera".to_owned(),
        use_tls: false,
        tls_skip_verify: false,
        device_id: "camera".to_owned(),
        device_name: "Front camera".to_owned(),
        device_model: "D-Link DCS-6100LHV2 A1".to_owned(),
        discovery_prefix: "homeassistant".to_owned(),
        state_interval: Duration::from_secs(15),
        discovery_interval: Duration::from_secs(300),
        camera_interval: Duration::from_secs(5),
        entities: EntityFlags {
            motion: true,
            motion_guard: true,
            ircut: true,
            daynight: true,
            privacy: true,
            color: true,
            ir850: true,
            gain: true,
            rssi: true,
            snapshot: true,
            live_view: true,
            firmware_version: true,
            firmware_timestamp: true,
            reboot: false,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raptor_discovery_requires_global_and_entity_availability() {
        let config = fixture();
        let release = object([]);
        let legacy = discovery_publications(&config, &release);
        let raptor = raptor_publications(&config, &release);
        for (old, new) in legacy.iter().zip(&raptor) {
            assert_eq!(old.topic, new.topic);
            assert!(new.retained);
            if old.payload.is_empty() {
                assert!(new.payload.is_empty());
                continue;
            }
            let old = json::parse(&old.payload).unwrap();
            let new = json::parse(&new.payload).unwrap();
            assert!(old.get_path("availability").unwrap().as_object().is_some());
            assert_eq!(
                new.get_path("availability_mode").and_then(Value::as_str),
                Some("all")
            );
            let availability = new.get_path("availability").unwrap().as_array().unwrap();
            assert_eq!(availability.len(), 2);
            assert_eq!(availability[0], *old.get_path("availability").unwrap());
            let topic = availability[1]
                .get_path("topic")
                .and_then(Value::as_str)
                .unwrap();
            assert!(topic.starts_with(&config.base_topic()));
            assert!(topic.ends_with("/availability"));
            assert_eq!(old.get_path("unique_id"), new.get_path("unique_id"));
            assert_eq!(old.get_path("command_topic"), new.get_path("command_topic"));
        }
    }

    fn fixture() -> HaConfig {
        test_config()
    }

    #[test]
    fn discovery_json_and_topics_cover_only_a1_entities() {
        let config = fixture();
        let release = object([("COMMIT_ID", Value::String("r5".to_owned()))]);
        let publications = discovery_publications(&config, &release);
        let ircut = publications
            .iter()
            .find(|item| item.topic.ends_with("/ircut/config"))
            .unwrap();
        assert_eq!(
            ircut.topic,
            "homeassistant/switch/thingino_camera/ircut/config"
        );
        let payload = json::parse(&ircut.payload).unwrap();
        assert_eq!(
            payload.get_path("command_topic"),
            Some(&Value::String("cameras/camera/ircut/set".to_owned()))
        );
        assert_eq!(payload.get_path("qos"), Some(&number(1)));
        let daynight = publications
            .iter()
            .find(|item| item.topic.ends_with("/daynight/config"))
            .unwrap();
        let daynight = json::parse(&daynight.payload).unwrap();
        assert_eq!(
            daynight.get_path("options"),
            Some(&Value::Array(vec![
                Value::String("auto".to_owned()),
                Value::String("day".to_owned()),
                Value::String("night".to_owned()),
            ]))
        );
        let rssi = publications
            .iter()
            .find(|item| item.topic.ends_with("/rssi/config"))
            .unwrap();
        let rssi = json::parse(&rssi.payload).unwrap();
        assert_eq!(
            rssi.get_path("device_class"),
            Some(&Value::String("signal_strength".to_owned()))
        );
        assert_eq!(
            rssi.get_path("state_class"),
            Some(&Value::String("measurement".to_owned()))
        );
        let camera = publications
            .iter()
            .find(|item| item.topic.ends_with("/live_view/config"))
            .unwrap();
        let camera = json::parse(&camera.payload).unwrap();
        assert_eq!(
            camera.get_path("name"),
            Some(&Value::String("Camera preview".to_owned()))
        );
        assert_eq!(
            camera.get_path("topic"),
            Some(&Value::String("cameras/camera/live_view/image".to_owned()))
        );
        assert_eq!(camera.get_path("stream_source"), None);
        assert_eq!(camera.get_path("image_topic"), None);
        for unsupported in [
            "doorbell",
            "ir940",
            "white",
            "ota",
            "firmware",
            "firmware_latest",
            "ptz_up",
            "ptz_down",
            "ptz_left",
            "ptz_right",
            "ptz_home",
        ] {
            assert!(publications.iter().any(|item| {
                item.topic.ends_with(&format!("/{unsupported}/config")) && item.payload.is_empty()
            }));
        }
    }

    #[test]
    fn disabling_entity_clears_its_retained_discovery() {
        let mut config = fixture();
        config.entities.privacy = false;
        let publications = discovery_publications(&config, &object([]));
        let privacy = publications
            .iter()
            .find(|item| item.topic.ends_with("/privacy/config"))
            .unwrap();
        assert!(privacy.retained);
        assert!(privacy.payload.is_empty());
    }
}
