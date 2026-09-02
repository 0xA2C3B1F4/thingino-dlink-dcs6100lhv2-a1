use super::*;

#[cfg(test)]
use crate::decode::decode_hex;
pub(crate) use crate::decode::percent_decode_path as percent_decode;
use crate::request_parse::parse_request;
pub(crate) use crate::request_parse::{Request, RequestError, find_bytes};

pub(crate) fn read_request(
    stream: &mut TcpStream,
    deadline: Instant,
) -> Result<Request, RequestError> {
    let mut received = Vec::with_capacity(1024);
    let mut chunk = [0_u8; 1024];
    loop {
        let read_budget = remaining(deadline)
            .ok_or(RequestError::Timeout)?
            .min(CONNECTION_TIMEOUT);
        stream
            .set_read_timeout(Some(read_budget))
            .map_err(|_| RequestError::BadRequest)?;
        let count = match stream.read(&mut chunk) {
            Ok(0) => return Err(RequestError::BadRequest),
            Ok(count) => count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if is_timeout(&error) => return Err(RequestError::Timeout),
            Err(_) => return Err(RequestError::BadRequest),
        };
        received.extend_from_slice(&chunk[..count]);
        if received.contains(&0) {
            return Err(RequestError::BadRequest);
        }
        if let Some(index) = find_bytes(&received, b"\r\n\r\n") {
            if index + 4 > MAX_HEADER_BYTES {
                return Err(RequestError::BadRequest);
            }
            break;
        }
        if received.len() > MAX_HEADER_BYTES {
            return Err(RequestError::BadRequest);
        }
    }

    loop {
        if let Some(request) = parse_request(&received)? {
            return Ok(request);
        }
        let read_budget = remaining(deadline)
            .ok_or(RequestError::Timeout)?
            .min(CONNECTION_TIMEOUT);
        stream
            .set_read_timeout(Some(read_budget))
            .map_err(|_| RequestError::BadRequest)?;
        let count = match stream.read(&mut chunk) {
            Ok(0) => return Err(RequestError::BadRequest),
            Ok(count) => count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if is_timeout(&error) => return Err(RequestError::Timeout),
            Err(_) => return Err(RequestError::BadRequest),
        };
        received.extend_from_slice(&chunk[..count]);
    }
}

pub(crate) fn parse_daynight_mode(body: &[u8]) -> Option<DayNightMode> {
    let text = trim_json_whitespace(std::str::from_utf8(body).ok()?);
    let inner = trim_json_whitespace(text.strip_prefix('{')?.strip_suffix('}')?);
    let (key, value) = inner.split_once(':')?;
    if trim_json_whitespace(key) != "\"mode\"" {
        return None;
    }
    let value = trim_json_whitespace(value);
    if value.len() < 2 || value.contains(',') || !value.starts_with('"') || !value.ends_with('"') {
        return None;
    }
    match &value[1..value.len() - 1] {
        "auto" => Some(DayNightMode::Auto),
        "day" => Some(DayNightMode::Day),
        "night" => Some(DayNightMode::Night),
        _ => None,
    }
}

pub(crate) fn trim_json_whitespace(value: &str) -> &str {
    value.trim_matches(|character| matches!(character, ' ' | '\t' | '\r' | '\n'))
}

pub(crate) fn authorized(header: Option<&[u8]>, token: &[u8]) -> bool {
    let Some(header) = header else {
        return false;
    };
    let Some(candidate) = header.strip_prefix(b"Bearer ") else {
        return false;
    };
    constant_time_equal(candidate, token)
}

pub(crate) fn accepts_html(request: &Request) -> bool {
    request.accept.as_deref().is_some_and(|accept| {
        accept
            .windows(b"text/html".len())
            .any(|window| window.eq_ignore_ascii_case(b"text/html"))
    })
}

pub(crate) fn has_json_content_type(request: &Request) -> bool {
    request.content_type.as_deref().is_some_and(|value| {
        std::str::from_utf8(value).ok().is_some_and(|value| {
            let value = value.trim().to_ascii_lowercase();
            value == "application/json" || value.starts_with("application/json;")
        })
    })
}

pub(crate) fn has_sdp_content_type(request: &Request) -> bool {
    request.content_type.as_deref().is_some_and(|value| {
        std::str::from_utf8(value).ok().is_some_and(|value| {
            let value = value.trim().to_ascii_lowercase();
            value == "application/sdp" || value.starts_with("application/sdp;")
        })
    })
}

pub(crate) fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    let longest = left.len().max(right.len());
    for index in 0..longest {
        let left_byte = left.get(index).copied().unwrap_or(0);
        let right_byte = right.get(index).copied().unwrap_or(0);
        difference |= usize::from(left_byte ^ right_byte);
    }
    difference == 0
}

pub(crate) fn drain_pending_input(stream: &mut TcpStream) {
    if stream.set_nonblocking(true).is_err() {
        return;
    }
    let mut drained = 0;
    let mut buffer = [0_u8; 1024];
    while drained <= MAX_HEADER_BYTES + MAX_BODY_BYTES {
        match stream.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => drained += count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
            Err(_) => break,
        }
    }
    let _ = stream.set_nonblocking(false);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::{Shutdown, TcpListener};
    use std::thread;

    fn read_fixture(bytes: Vec<u8>) -> Result<Request, RequestError> {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let sender = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(&bytes).unwrap();
            stream.shutdown(Shutdown::Write).unwrap();
        });
        let (mut stream, _) = listener.accept().unwrap();
        let result = read_request(&mut stream, Instant::now() + Duration::from_secs(1));
        sender.join().unwrap();
        result
    }

    #[test]
    fn percent_and_hex_decoders_reject_malformed_and_odd_inputs() {
        assert_eq!(
            percent_decode("/onvif%2Fimage.cgi"),
            Some("/onvif/image.cgi".to_owned())
        );
        for invalid in ["%", "%a", "%gg", "+", "line\rbreak", "line\nbreak", "%ff"] {
            assert_eq!(percent_decode(invalid), None, "accepted {invalid:?}");
        }
        for (digit, value) in [
            (b'0', 0),
            (b'9', 9),
            (b'a', 10),
            (b'f', 15),
            (b'A', 10),
            (b'F', 15),
        ] {
            assert_eq!(decode_hex(digit), Some(value));
        }
        for invalid in [b'/', b':', b'@', b'G', b'`', b'g'] {
            assert_eq!(decode_hex(invalid), None);
        }
    }

    #[test]
    fn request_line_and_crlf_framing_are_strictly_bounded() {
        let request =
            read_fixture(b"GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\n\r\n".to_vec()).unwrap();
        assert_eq!(request.method, "GET");
        assert_eq!(request.target, "/api/v1/health");
        for invalid in [
            b"GET HTTP/1.1\r\n\r\n".as_slice(),
            b"GET /api/v1/health HTTP/1.0\r\n\r\n".as_slice(),
            b"GET /api/v1/health extra HTTP/1.1\r\n\r\n".as_slice(),
            b"GET /api/v1/health HTTP/1.1\n\n".as_slice(),
        ] {
            assert!(matches!(
                read_fixture(invalid.to_vec()),
                Err(RequestError::BadRequest)
            ));
        }
    }

    #[test]
    fn aggregate_header_count_and_length_stop_at_the_byte_ceiling() {
        let prefix = b"GET / HTTP/1.1\r\nX-Fill:";
        let mut at_limit = prefix.to_vec();
        at_limit.extend(std::iter::repeat_n(
            b'a',
            MAX_HEADER_BYTES - prefix.len() - 4,
        ));
        at_limit.extend_from_slice(b"\r\n\r\n");
        assert_eq!(at_limit.len(), MAX_HEADER_BYTES);
        assert!(read_fixture(at_limit.clone()).is_ok());
        at_limit.insert(at_limit.len() - 4, b'a');
        assert!(matches!(
            read_fixture(at_limit),
            Err(RequestError::BadRequest)
        ));

        let mut too_many_headers = b"GET / HTTP/1.1\r\n".to_vec();
        while too_many_headers.len() <= MAX_HEADER_BYTES {
            too_many_headers.extend_from_slice(b"X: a\r\n");
        }
        too_many_headers.extend_from_slice(b"\r\n");
        assert!(matches!(
            read_fixture(too_many_headers),
            Err(RequestError::BadRequest)
        ));
    }

    #[test]
    fn content_length_rejects_duplicates_overflow_truncation_and_oversize() {
        let exact =
            read_fixture(b"POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nbody".to_vec()).unwrap();
        assert_eq!(exact.body, b"body");
        for invalid in [
            b"POST / HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\nok".to_vec(),
            b"POST / HTTP/1.1\r\nContent-Length: +1\r\n\r\nx".to_vec(),
            b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\nxy".to_vec(),
            b"POST / HTTP/1.1\r\nContent-Length: 2\r\n\r\nxyz".to_vec(),
            b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n".to_vec(),
        ] {
            assert!(matches!(
                read_fixture(invalid),
                Err(RequestError::BadRequest)
            ));
        }
        let declared_oversize = format!(
            "POST / HTTP/1.1\r\nContent-Length: {}\r\n\r\n",
            MAX_BODY_BYTES + 1
        );
        assert!(matches!(
            read_fixture(declared_oversize.into_bytes()),
            Err(RequestError::PayloadTooLarge)
        ));
        let overflow =
            b"POST / HTTP/1.1\r\nContent-Length: 999999999999999999999999999999999999\r\n\r\n";
        assert!(matches!(
            read_fixture(overflow.to_vec()),
            Err(RequestError::BadRequest)
        ));
    }
}
