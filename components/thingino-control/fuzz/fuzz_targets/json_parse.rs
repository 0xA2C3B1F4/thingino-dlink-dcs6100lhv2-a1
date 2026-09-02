#![no_main]

use libfuzzer_sys::fuzz_target;

#[allow(dead_code)]
#[path = "../../src/json.rs"]
mod json;

fuzz_target!(|input: &[u8]| {
    if let Ok(value) = json::parse(input) {
        let encoded = value.to_json();
        let reparsed = json::parse(encoded.as_bytes()).expect("serialized JSON must parse");

        assert_eq!(reparsed, value);
        assert_eq!(reparsed.to_json(), encoded);
    }
});
