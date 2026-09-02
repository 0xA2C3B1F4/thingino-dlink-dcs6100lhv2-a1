use super::*;

pub(crate) fn classify_connect_error(error: io::Error) -> BackendError {
    if is_timeout(&error) {
        BackendError::Timeout
    } else {
        BackendError::Connection
    }
}

pub(crate) fn backend_response_total(response: &[u8]) -> Result<Option<usize>, BackendError> {
    if response.len() > MAX_HEADER_BYTES && find_bytes(response, b"\r\n\r\n").is_none() {
        return Err(BackendError::Protocol);
    }
    let Some(header_end) = find_bytes(response, b"\r\n\r\n") else {
        return Ok(None);
    };
    let (_, content_length, _) = parse_backend_header(&response[..header_end])?;
    Ok(Some(header_end + 4 + content_length))
}

pub(crate) fn parse_backend_header(
    header_bytes: &[u8],
) -> Result<(u16, usize, &'static str), BackendError> {
    if header_bytes.len() + 4 > MAX_HEADER_BYTES {
        return Err(BackendError::Protocol);
    }
    let header = std::str::from_utf8(header_bytes).map_err(|_| BackendError::Protocol)?;
    let mut lines = header.split("\r\n");
    let status_line = lines.next().ok_or(BackendError::Protocol)?;
    let mut parts = status_line.split_ascii_whitespace();
    if parts.next() != Some("HTTP/1.1") {
        return Err(BackendError::Protocol);
    }
    let status = parts
        .next()
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or(BackendError::Protocol)?;
    let mut content_length = None;
    let mut content_type = None;
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
            content_length = Some(value.parse::<usize>().map_err(|_| BackendError::Protocol)?);
        } else if name.eq_ignore_ascii_case("content-type") {
            if content_type.is_some() {
                return Err(BackendError::Protocol);
            }
            content_type = if value.eq_ignore_ascii_case("application/json")
                || value.to_ascii_lowercase().starts_with("application/json;")
            {
                Some("application/json")
            } else if value.eq_ignore_ascii_case("image/jpeg") {
                Some("image/jpeg")
            } else {
                return Err(BackendError::Protocol);
            };
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(BackendError::Protocol);
        }
    }
    let content_length = content_length.ok_or(BackendError::Protocol)?;
    let content_type = content_type.ok_or(BackendError::Protocol)?;
    let limit = if content_type == "image/jpeg" {
        MAX_SNAPSHOT_BYTES
    } else {
        MAX_BACKEND_RESPONSE_BYTES
    };
    if content_length > limit {
        return Err(BackendError::Protocol);
    }
    Ok((status, content_length, content_type))
}

pub(crate) fn parse_backend_response(response: &[u8]) -> Result<BackendResponse, BackendError> {
    let header_end = find_bytes(response, b"\r\n\r\n").ok_or(BackendError::Protocol)?;
    let (status, content_length, content_type) = parse_backend_header(&response[..header_end])?;
    if response.len() != header_end + 4 + content_length {
        return Err(BackendError::Protocol);
    }
    if !(200..300).contains(&status) {
        return Err(BackendError::Upstream(status));
    }
    let body = &response[header_end + 4..];
    if content_type == "application/json"
        && (body.len() > MAX_BACKEND_RESPONSE_BYTES || std::str::from_utf8(body).is_err())
    {
        return Err(BackendError::Protocol);
    }
    Ok(BackendResponse {
        content_type,
        body: body.to_vec(),
    })
}

pub(crate) fn write_all_deadline(
    stream: &mut TcpStream,
    mut data: &[u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !data.is_empty() {
        set_deadline_timeouts(stream, deadline).map_err(|_| BackendError::Connection)?;
        match stream.write(data) {
            Ok(0) => return Err(BackendError::Connection),
            Ok(count) => data = &data[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if is_timeout(&error) => return Err(BackendError::Timeout),
            Err(_) => return Err(BackendError::Connection),
        }
    }
    Ok(())
}

pub(crate) fn set_deadline_timeouts(stream: &TcpStream, deadline: Instant) -> io::Result<()> {
    let budget = remaining(deadline).unwrap_or(Duration::from_nanos(1));
    stream.set_read_timeout(Some(budget))?;
    stream.set_write_timeout(Some(budget))
}

pub(crate) fn remaining(deadline: Instant) -> Option<Duration> {
    deadline.checked_duration_since(Instant::now())
}

pub(crate) fn is_timeout(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    )
}

pub(crate) fn send_backend_result(
    stream: &mut TcpStream,
    deadline: Instant,
    result: Result<BackendResponse, BackendError>,
    snapshot: Option<u8>,
) {
    match result {
        Ok(response) => {
            let result = if let Some(stream_id) = snapshot {
                send_snapshot_response(
                    stream,
                    deadline,
                    stream_id,
                    response.content_type,
                    &response.body,
                )
            } else {
                send_response(
                    stream,
                    deadline,
                    200,
                    "OK",
                    response.content_type,
                    &response.body,
                )
            };
            let _ = result;
        }
        Err(BackendError::Timeout) => {
            let _ = send_error(
                stream,
                deadline,
                504,
                "backend_timeout",
                "backend timed out",
            );
        }
        Err(BackendError::Unavailable) => {
            let _ = send_error(
                stream,
                deadline,
                503,
                "service_unavailable",
                "backend is unavailable",
            );
        }
        Err(BackendError::Protocol) => {
            let _ = send_error(
                stream,
                deadline,
                400,
                "invalid_request",
                "request body or parameters are invalid",
            );
        }
        Err(BackendError::Connection | BackendError::Upstream(_)) => {
            let _ = send_error(
                stream,
                deadline,
                502,
                "bad_gateway",
                "backend request failed",
            );
        }
    }
}

pub(crate) fn send_error(
    stream: &mut TcpStream,
    deadline: Instant,
    status: u16,
    code: &str,
    message: &str,
) -> io::Result<()> {
    let body = format!(
        "{{\"status\":\"error\",\"error\":{{\"code\":\"{code}\",\"message\":\"{message}\"}}}}\n"
    );
    let reason = match status {
        400 => "Bad Request",
        401 => "Unauthorized",
        415 => "Unsupported Media Type",
        413 => "Payload Too Large",
        404 => "Not Found",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Error",
    };
    send_response(
        stream,
        deadline,
        status,
        reason,
        "application/json",
        body.as_bytes(),
    )
}

pub(crate) fn send_redirect(
    stream: &mut TcpStream,
    deadline: Instant,
    location: &str,
) -> io::Result<()> {
    let header = format!(
        "HTTP/1.1 302 Found\r\nConnection: close\r\nLocation: {location}\r\nCache-Control: no-store\r\nContent-Length: 0\r\n\r\n"
    );
    write_client_all(stream, header.as_bytes(), deadline)
}

pub(crate) fn send_response(
    stream: &mut TcpStream,
    deadline: Instant,
    status: u16,
    reason: &str,
    content_type: &str,
    body: &[u8],
) -> io::Result<()> {
    send_response_headers(stream, deadline, status, reason, content_type, body, &[])
}

pub(crate) fn send_response_headers(
    stream: &mut TcpStream,
    deadline: Instant,
    status: u16,
    reason: &str,
    content_type: &str,
    body: &[u8],
    extra_headers: &[(&str, &str)],
) -> io::Result<()> {
    let mut header = format!(
        "HTTP/1.1 {status} {reason}\r\nConnection: close\r\nContent-Type: {content_type}\r\nCache-Control: no-store\r\nContent-Length: {}\r\n",
        body.len()
    );
    for (name, value) in extra_headers {
        if name.contains(['\r', '\n']) || value.contains(['\r', '\n']) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid response header",
            ));
        }
        header.push_str(name);
        header.push_str(": ");
        header.push_str(value);
        header.push_str("\r\n");
    }
    header.push_str("\r\n");
    write_client_all(stream, header.as_bytes(), deadline)?;
    write_client_all(stream, body, deadline)
}

pub(crate) fn send_snapshot_response(
    stream: &mut TcpStream,
    deadline: Instant,
    stream_id: u8,
    content_type: &str,
    body: &[u8],
) -> io::Result<()> {
    let header = format!(
        "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: {content_type}\r\nContent-Disposition: attachment; filename=\"snapshot-ch{stream_id}.jpg\"\r\nCache-Control: no-store\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    write_client_all(stream, header.as_bytes(), deadline)?;
    write_client_all(stream, body, deadline)
}

pub(crate) fn write_client_all(
    stream: &mut TcpStream,
    mut data: &[u8],
    deadline: Instant,
) -> io::Result<()> {
    while !data.is_empty() {
        let budget = remaining(deadline)
            .unwrap_or(Duration::from_millis(1))
            .min(CONNECTION_TIMEOUT);
        stream.set_write_timeout(Some(budget))?;
        match stream.write(data) {
            Ok(0) => return Err(io::Error::from(io::ErrorKind::WriteZero)),
            Ok(count) => data = &data[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}
