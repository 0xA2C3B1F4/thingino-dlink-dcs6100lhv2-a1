#![no_main]

use libfuzzer_sys::fuzz_target;

#[allow(dead_code)]
#[path = "../../src/request_parse.rs"]
mod request_parse;

use request_parse::{
    MAX_BODY_BYTES, MAX_HEADER_BYTES, MAX_WHIP_SDP_BODY_BYTES, RequestError, find_bytes,
    parse_request,
};

fuzz_target!(|input: &[u8]| {
    let result = parse_request(input);
    assert_eq!(result, parse_request(input));

    match result {
        Ok(Some(request)) => {
            let header_end = find_bytes(input, b"\r\n\r\n")
                .expect("a complete request must have a header terminator");
            assert!(header_end + 4 <= MAX_HEADER_BYTES);
            assert!(!request.method.is_empty());
            assert!(!request.target.is_empty());
            assert!(request.body.len() <= MAX_WHIP_SDP_BODY_BYTES);
            if request.body.len() > MAX_BODY_BYTES {
                assert_eq!(request.method, "POST");
                assert!(matches!(
                    request.target.as_str(),
                    "/api/v1/media/webrtc/whip?stream=0"
                        | "/api/v1/media/webrtc/whip?stream=1"
                ));
            }
            assert_eq!(request.body, input[header_end + 4..]);
        }
        Ok(None) | Err(RequestError::BadRequest | RequestError::PayloadTooLarge) => {}
        Err(RequestError::Timeout) => panic!("the pure parser cannot report a socket timeout"),
    }
});
