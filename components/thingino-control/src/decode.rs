#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum HexSsidDecode {
    Plain,
    Invalid,
    Decoded(String),
}

pub(crate) fn percent_decode_path(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut output = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'%' if index + 2 < bytes.len() => {
                let high = decode_hex(bytes[index + 1])?;
                let low = decode_hex(bytes[index + 2])?;
                output.push(high << 4 | low);
                index += 3;
            }
            b'%' | b'+' | 0 | b'\r' | b'\n' => return None,
            byte => {
                output.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8(output).ok()
}

pub(crate) fn percent_decode_form(value: &str) -> Option<String> {
    let mut decoded = Vec::with_capacity(value.len());
    let bytes = value.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                decoded.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                decoded.push(decode_hex(bytes[index + 1])? << 4 | decode_hex(bytes[index + 2])?);
                index += 3;
            }
            b'%' => return None,
            value => {
                decoded.push(value);
                index += 1;
            }
        }
    }
    String::from_utf8(decoded).ok()
}

pub(crate) fn decode_base64(input: &str) -> Option<Vec<u8>> {
    if input.is_empty() || !input.len().is_multiple_of(4) || input.len() > 512 {
        return None;
    }
    let mut output = Vec::with_capacity(input.len() / 4 * 3);
    let blocks = input.len() / 4;
    for (index, block) in input.as_bytes().chunks_exact(4).enumerate() {
        let a = base64_value(block[0])?;
        let b = base64_value(block[1])?;
        let c_padding = block[2] == b'=';
        let d_padding = block[3] == b'=';
        if (c_padding || d_padding) && index + 1 != blocks
            || c_padding && (!d_padding || b & 0x0f != 0)
        {
            return None;
        }
        let c = if c_padding {
            0
        } else {
            base64_value(block[2])?
        };
        let d = if d_padding {
            if c & 0x03 != 0 {
                return None;
            }
            0
        } else {
            base64_value(block[3])?
        };
        output.push((a << 2) | (b >> 4));
        if !c_padding {
            output.push((b << 4) | (c >> 2));
        }
        if !d_padding {
            output.push((c << 6) | d);
        }
    }
    Some(output)
}

pub(crate) fn decode_wpa_ssid(input: &str) -> Option<String> {
    let mut output = Vec::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\\' && index + 3 < bytes.len() && bytes[index + 1] == b'x' {
            output.push(decode_hex(bytes[index + 2])? << 4 | decode_hex(bytes[index + 3])?);
            index += 4;
        } else if bytes[index] == b'\\' && index + 1 < bytes.len() {
            output.push(bytes[index + 1]);
            index += 2;
        } else {
            output.push(bytes[index]);
            index += 1;
        }
    }
    let value = String::from_utf8(output).ok()?;
    (!value.is_empty() && value.len() <= 32 && !value.contains('\0')).then_some(value)
}

pub(crate) fn decode_hex_ssid(input: &str) -> HexSsidDecode {
    if input.is_empty()
        || input.len() > 64
        || !input.len().is_multiple_of(2)
        || !input.bytes().all(|value| value.is_ascii_hexdigit())
    {
        return HexSsidDecode::Plain;
    }
    let Some(bytes) = input
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Some(decode_hex(pair[0])? << 4 | decode_hex(pair[1])?))
        .collect::<Option<Vec<_>>>()
    else {
        return HexSsidDecode::Invalid;
    };
    let Ok(value) = String::from_utf8(bytes) else {
        return HexSsidDecode::Invalid;
    };
    if value.is_empty() || value.len() > 32 || value.contains('\0') {
        HexSsidDecode::Invalid
    } else {
        HexSsidDecode::Decoded(value)
    }
}

pub(crate) fn decode_hex(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn base64_value(byte: u8) -> Option<u8> {
    match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}
