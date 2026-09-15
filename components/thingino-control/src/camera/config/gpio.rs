use super::super::*;

fn hardware_io(gpio: u64, function: &str, direction: &str, owner: &str) -> Value {
    object([
        ("gpio", number(gpio)),
        ("function", Value::String(function.to_owned())),
        ("direction", Value::String(direction.to_owned())),
        ("owner", Value::String(owner.to_owned())),
        ("read_only", Value::Bool(true)),
    ])
}

fn dlink_a1_hardware_io() -> Vec<Value> {
    vec![
        hardware_io(18, "Sensor reset", "output", "sensor driver"),
        hardware_io(49, "IR-cut coil A", "output", "day/night control"),
        hardware_io(50, "IR-cut coil B", "output", "day/night control"),
        hardware_io(52, "Green status LED", "output", "kernel LED class"),
        hardware_io(54, "Red status LED", "output", "kernel LED class"),
        hardware_io(57, "Wi-Fi power", "output", "network startup"),
        hardware_io(59, "SD card detect", "input", "MMC driver"),
        hardware_io(60, "Reset button", "input", "button service"),
        hardware_io(61, "850 nm IR LED", "output", "day/night control"),
        hardware_io(63, "Speaker enable", "output", "audio control"),
    ]
}

impl HostBackend {
    pub(in crate::camera) fn gpio_config(&self) -> Result<BackendResponse, BackendError> {
        let thingino = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let startup_indicator = thingino
            .get_path("led.startup_indicator")
            .and_then(Value::as_str)
            .filter(|value| matches!(*value, "off" | "green" | "red"))
            .unwrap_or("off");
        json_response(object([
            (
                "profile",
                Value::String("D-Link DCS-6100LHV2 A1".to_owned()),
            ),
            (
                "gpio",
                object([
                    ("ircut", Value::String("50 49".to_owned())),
                    ("ir850", number(61)),
                ]),
            ),
            (
                "led",
                object([(
                    "startup_indicator",
                    Value::String(startup_indicator.to_owned()),
                )]),
            ),
            (
                "available_startup_indicators",
                Value::String("green,red".to_owned()),
            ),
            ("hardware_io", Value::Array(dlink_a1_hardware_io())),
            // The D-Link A1 profile has no GPIO-backed PWM output.
            ("pwm_pins", Value::String(String::new())),
        ]))
    }

    pub(in crate::camera) fn update_gpio_config(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let update = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = update.as_object().ok_or(BackendError::Protocol)?;
        if fields.len() != 1 || !fields.contains_key("startup_indicator") {
            // Fixed A1 assignments, including ircut, ir850, ir940 and white,
            // are never accepted through the configuration route.
            return Err(BackendError::Protocol);
        }
        let color = fields
            .get("startup_indicator")
            .and_then(Value::as_str)
            .filter(|value| matches!(*value, "off" | "green" | "red"))
            .ok_or(BackendError::Protocol)?;
        let mut thingino = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        thingino
            .set_path("led.startup_indicator", Value::String(color.to_owned()))
            .map_err(|_| BackendError::Protocol)?;
        let mut serialized = thingino.to_json().into_bytes();
        serialized.push(b'\n');
        write_in_place(&self.paths.thingino_config, &serialized)?;
        json_response(object([
            ("status", Value::String("ok".to_owned())),
            (
                "message",
                Value::String("Startup indicator saved".to_owned()),
            ),
        ]))
    }
}
