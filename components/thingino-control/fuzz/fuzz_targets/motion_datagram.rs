#![no_main]

use libfuzzer_sys::fuzz_target;

#[allow(dead_code)]
#[path = "../../src/json.rs"]
mod json;

#[allow(dead_code)]
#[path = "../../src/camera/motion_datagram.rs"]
mod motion_datagram;

fuzz_target!(|input: &[u8]| {
    let observation = motion_datagram::parse_observation(input);
    assert_eq!(observation, motion_datagram::parse_observation(input));

    if let Ok(observation) = observation {
        assert!(input.len() <= 512);
        assert_eq!(observation.version, 1);
        assert!(observation.channel <= 1);
        assert!(observation.sequence > 0);
        assert!(observation.producer_pid > 0);
        assert_eq!(
            matches!(
                observation.state,
                motion_datagram::ObservationState::Detected
            ),
            observation.roi_mask != 0
        );
    }
});
