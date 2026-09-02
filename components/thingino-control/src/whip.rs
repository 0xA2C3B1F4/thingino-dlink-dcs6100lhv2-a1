use super::*;

const MAX_WHIP_RESPONSE_BYTES: usize = 16 * 1024;

#[derive(Clone, Debug)]
pub struct WhipProxy {
    address: SocketAddr,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct WhipSession {
    pub(crate) answer: Vec<u8>,
    pub(crate) session_id: String,
}

impl WhipProxy {
    pub fn new(address: SocketAddr) -> Self {
        Self { address }
    }

    pub(crate) fn create(
        &self,
        stream_id: u8,
        offer: &[u8],
        deadline: Instant,
    ) -> Result<WhipSession, BackendError> {
        let path = match stream_id {
            0 => "/whip?stream=0",
            1 => "/whip?stream=1",
            _ => return Err(BackendError::Protocol),
        };
        let response = self.exchange("POST", path, "application/sdp", offer, deadline)?;
        parse_create_response(&response)
    }

    pub(crate) fn delete(&self, session_id: &str, deadline: Instant) -> Result<(), BackendError> {
        let path = format!("/whip/{session_id}");
        let response = self.exchange("DELETE", &path, "text/plain", b"", deadline)?;
        let parsed = parse_response(&response)?;
        if parsed.status != 200 || parsed.body != b"OK" {
            return Err(BackendError::Upstream(parsed.status));
        }
        Ok(())
    }

    fn exchange(
        &self,
        method: &str,
        path: &str,
        content_type: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Result<Vec<u8>, BackendError> {
        let connect_budget = remaining(deadline)
            .ok_or(BackendError::Timeout)?
            .min(CONNECTION_TIMEOUT);
        let mut stream = TcpStream::connect_timeout(&self.address, connect_budget)
            .map_err(classify_connect_error)?;
        let request = format!(
            "{method} {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\nAccept: application/sdp\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\n\r\n",
            self.address,
            body.len(),
        );
        write_all_deadline(&mut stream, request.as_bytes(), deadline)?;
        write_all_deadline(&mut stream, body, deadline)?;

        let mut response = Vec::with_capacity(4096);
        let mut chunk = [0_u8; 4096];
        loop {
            set_deadline_timeouts(&stream, deadline).map_err(|_| BackendError::Connection)?;
            match stream.read(&mut chunk) {
                Ok(0) => return Ok(response),
                Ok(count) => {
                    if response.len() + count > MAX_WHIP_RESPONSE_BYTES + MAX_HEADER_BYTES {
                        return Err(BackendError::Protocol);
                    }
                    response.extend_from_slice(&chunk[..count]);
                    if let Some(total) = response_total(&response)? {
                        if response.len() == total {
                            return Ok(response);
                        }
                        if response.len() > total {
                            return Err(BackendError::Protocol);
                        }
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) if is_timeout(&error) => return Err(BackendError::Timeout),
                Err(_) => return Err(BackendError::Connection),
            }
        }
    }
}

struct ParsedResponse<'a> {
    status: u16,
    content_type: &'a str,
    location: Option<&'a str>,
    body: &'a [u8],
}

fn response_total(response: &[u8]) -> Result<Option<usize>, BackendError> {
    let Some(header_end) = find_bytes(response, b"\r\n\r\n") else {
        if response.len() > MAX_HEADER_BYTES {
            return Err(BackendError::Protocol);
        }
        return Ok(None);
    };
    let (_, content_length, _, _) = parse_headers(&response[..header_end])?;
    Ok(Some(header_end + 4 + content_length))
}

fn parse_headers(header: &[u8]) -> Result<(u16, usize, &str, Option<&str>), BackendError> {
    if header.len() + 4 > MAX_HEADER_BYTES {
        return Err(BackendError::Protocol);
    }
    let text = std::str::from_utf8(header).map_err(|_| BackendError::Protocol)?;
    let mut lines = text.split("\r\n");
    let mut status_parts = lines
        .next()
        .ok_or(BackendError::Protocol)?
        .split_ascii_whitespace();
    if status_parts.next() != Some("HTTP/1.1") {
        return Err(BackendError::Protocol);
    }
    let status = status_parts
        .next()
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or(BackendError::Protocol)?;
    let mut content_length = None;
    let mut content_type = None;
    let mut location = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(BackendError::Protocol)?;
        let value = value.trim_ascii();
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some()
                || value.is_empty()
                || !value.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(BackendError::Protocol);
            }
            let length = value.parse::<usize>().map_err(|_| BackendError::Protocol)?;
            if length > MAX_WHIP_RESPONSE_BYTES {
                return Err(BackendError::Protocol);
            }
            content_length = Some(length);
        } else if name.eq_ignore_ascii_case("content-type") {
            if content_type.replace(value).is_some() {
                return Err(BackendError::Protocol);
            }
        } else if name.eq_ignore_ascii_case("location") {
            if location.replace(value).is_some() {
                return Err(BackendError::Protocol);
            }
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(BackendError::Protocol);
        }
    }
    Ok((
        status,
        content_length.ok_or(BackendError::Protocol)?,
        content_type.ok_or(BackendError::Protocol)?,
        location,
    ))
}

fn parse_response(response: &[u8]) -> Result<ParsedResponse<'_>, BackendError> {
    let header_end = find_bytes(response, b"\r\n\r\n").ok_or(BackendError::Protocol)?;
    let (status, content_length, content_type, location) = parse_headers(&response[..header_end])?;
    if response.len() != header_end + 4 + content_length {
        return Err(BackendError::Protocol);
    }
    Ok(ParsedResponse {
        status,
        content_type,
        location,
        body: &response[header_end + 4..],
    })
}

fn parse_create_response(response: &[u8]) -> Result<WhipSession, BackendError> {
    let parsed = parse_response(response)?;
    if parsed.status != 201 || !parsed.content_type.eq_ignore_ascii_case("application/sdp") {
        return Err(BackendError::Upstream(parsed.status));
    }
    let location = parsed.location.ok_or(BackendError::Protocol)?;
    let session_id = location
        .strip_prefix("/whip/")
        .ok_or(BackendError::Protocol)?;
    if !valid_session_id(session_id) || std::str::from_utf8(parsed.body).is_err() {
        return Err(BackendError::Protocol);
    }
    Ok(WhipSession {
        answer: parsed.body.to_vec(),
        session_id: session_id.to_owned(),
    })
}

pub(crate) fn valid_session_id(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_response_requires_exact_sdp_and_session_location() {
        let response = b"HTTP/1.1 201 Created\r\nContent-Type: application/sdp\r\nContent-Length: 5\r\nLocation: /whip/0123456789abcdef0123456789abcdef\r\n\r\nv=0\r\n";
        let session = parse_create_response(response).unwrap();
        assert_eq!(session.answer, b"v=0\r\n");
        assert_eq!(session.session_id, "0123456789abcdef0123456789abcdef");
        assert!(parse_create_response(&response[..response.len() - 1]).is_err());
        assert!(!valid_session_id("../session"));
    }
}
