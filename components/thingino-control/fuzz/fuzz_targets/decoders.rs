#![no_main]
#![allow(clippy::chunks_exact_to_as_chunks)]

use libfuzzer_sys::fuzz_target;

#[allow(dead_code)]
#[path = "../../src/decode.rs"]
mod decode;

use decode::HexSsidDecode;

fuzz_target!(|input: &[u8]| {
    let Some((&tag, payload)) = input.split_first() else {
        return;
    };
    let Ok(text) = std::str::from_utf8(payload) else {
        return;
    };

    match tag {
        b'p' => {
            let decoded = decode::percent_decode_path(text);
            assert_eq!(decoded, decode::percent_decode_path(text));
            if let Some(decoded) = decoded {
                assert!(decoded.len() <= text.len());
            }
        }
        b'f' => {
            let decoded = decode::percent_decode_form(text);
            assert_eq!(decoded, decode::percent_decode_form(text));
            if let Some(decoded) = decoded {
                assert!(decoded.len() <= text.len());
            }
        }
        b'b' => {
            let decoded = decode::decode_base64(text);
            assert_eq!(decoded, decode::decode_base64(text));
            if let Some(decoded) = decoded {
                assert!(decoded.len() <= text.len().saturating_mul(3) / 4);
            }
        }
        b'h' => {
            let decoded = decode::decode_hex_ssid(text);
            assert_eq!(decoded, decode::decode_hex_ssid(text));
            match decoded {
                HexSsidDecode::Plain => {}
                HexSsidDecode::Invalid => {
                    assert!(text.len() >= 2 && text.len().is_multiple_of(2));
                }
                HexSsidDecode::Decoded(decoded) => {
                    assert_eq!(decoded.len(), text.len() / 2);
                }
            }
        }
        b'w' => {
            let decoded = decode::decode_wpa_ssid(text);
            assert_eq!(decoded, decode::decode_wpa_ssid(text));
            if let Some(decoded) = decoded {
                assert!(decoded.len() <= text.len());
            }
        }
        _ => {}
    }
});
