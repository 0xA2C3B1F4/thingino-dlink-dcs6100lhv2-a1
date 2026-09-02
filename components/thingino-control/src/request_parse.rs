#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct Request {
    pub(crate) method: String,
    pub(crate) target: String,
    pub(crate) authorization: Option<Vec<u8>>,
    pub(crate) accept: Option<Vec<u8>>,
    pub(crate) api_key: Option<Vec<u8>>,
    pub(crate) cookie: Option<Vec<u8>>,
    pub(crate) proxy_marker: Option<Vec<u8>>,
    pub(crate) remote_address: Option<Vec<u8>>,
    pub(crate) content_type: Option<Vec<u8>>,
    pub(crate) requested_with: Option<Vec<u8>>,
    pub(crate) body: Vec<u8>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum RequestError {
    BadRequest,
    PayloadTooLarge,
    Timeout,
}

pub const MAX_HEADER_BYTES: usize = 8 * 1024;
pub const MAX_BODY_BYTES: usize = 4 * 1024;

pub(crate) fn parse_request(input: &[u8]) -> Result<Option<Request>, RequestError> {
    let Some(header_end) = find_bytes(input, b"\r\n\r\n") else {
        return if input.len() > MAX_HEADER_BYTES {
            Err(RequestError::BadRequest)
        } else {
            Ok(None)
        };
    };
    if header_end + 4 > MAX_HEADER_BYTES {
        return Err(RequestError::BadRequest);
    }

    let header_text =
        std::str::from_utf8(&input[..header_end]).map_err(|_| RequestError::BadRequest)?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines.next().ok_or(RequestError::BadRequest)?;
    let request_parts: Vec<&str> = request_line.split_ascii_whitespace().collect();
    if request_parts.len() != 3 || request_parts[2] != "HTTP/1.1" {
        return Err(RequestError::BadRequest);
    }
    let method = request_parts[0].to_owned();
    let target = request_parts[1].to_owned();

    let mut authorization = None;
    let mut accept = None;
    let mut api_key = None;
    let mut cookie = None;
    let mut proxy_marker = None;
    let mut remote_address = None;
    let mut content_type = None;
    let mut requested_with = None;
    let mut content_length = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(RequestError::BadRequest)?;
        let value = value.trim_ascii();
        if name.eq_ignore_ascii_case("authorization") {
            if authorization.is_some() {
                return Err(RequestError::BadRequest);
            }
            authorization = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("accept") {
            if accept.is_some() {
                return Err(RequestError::BadRequest);
            }
            accept = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("x-api-key") {
            if api_key.is_some() {
                return Err(RequestError::BadRequest);
            }
            api_key = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("cookie") {
            if cookie.is_some() {
                return Err(RequestError::BadRequest);
            }
            cookie = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("x-thingino-proxy") {
            if proxy_marker.is_some() {
                return Err(RequestError::BadRequest);
            }
            proxy_marker = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("x-thingino-remote-addr") {
            if remote_address.is_some() {
                return Err(RequestError::BadRequest);
            }
            remote_address = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some()
                || value.is_empty()
                || !value.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(RequestError::BadRequest);
            }
            content_length = Some(
                value
                    .parse::<usize>()
                    .map_err(|_| RequestError::BadRequest)?,
            );
        } else if name.eq_ignore_ascii_case("content-type") {
            if content_type.is_some() || value.is_empty() {
                return Err(RequestError::BadRequest);
            }
            content_type = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("x-requested-with") {
            if requested_with.is_some() || value.is_empty() {
                return Err(RequestError::BadRequest);
            }
            requested_with = Some(value.as_bytes().to_vec());
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(RequestError::BadRequest);
        }
    }

    let content_length = content_length.unwrap_or(0);
    if content_length > MAX_BODY_BYTES {
        return Err(RequestError::PayloadTooLarge);
    }
    let body_start = header_end + 4;
    let request_length = body_start + content_length;
    if input.len() < request_length {
        return Ok(None);
    }
    if input.len() > request_length {
        return Err(RequestError::BadRequest);
    }

    Ok(Some(Request {
        method,
        target,
        authorization,
        accept,
        api_key,
        cookie,
        proxy_marker,
        remote_address,
        content_type,
        requested_with,
        body: input[body_start..].to_vec(),
    }))
}

pub(crate) fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pure_parser_distinguishes_incomplete_complete_and_excess_body() {
        assert_eq!(parse_request(b"GET / HTTP/1.1\r\n"), Ok(None));

        let complete = parse_request(b"POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nbody");
        let Ok(Some(request)) = complete else {
            panic!("complete request was not accepted");
        };
        assert_eq!(request.method, "POST");
        assert_eq!(request.target, "/");
        assert_eq!(request.body, b"body");

        assert_eq!(
            parse_request(b"POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nbo"),
            Ok(None)
        );
        assert_eq!(
            parse_request(b"POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nbodyx"),
            Err(RequestError::BadRequest)
        );
    }

    #[test]
    fn requested_with_is_a_nonempty_singleton_header() {
        let accepted = parse_request(
            b"POST / HTTP/1.1\r\nX-Requested-With: Thingino-WebUI\r\nContent-Length: 0\r\n\r\n",
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            accepted.requested_with.as_deref(),
            Some(b"Thingino-WebUI".as_slice())
        );
        for rejected in [
            b"POST / HTTP/1.1\r\nX-Requested-With:\r\nContent-Length: 0\r\n\r\n".as_slice(),
            b"POST / HTTP/1.1\r\nX-Requested-With: Thingino-WebUI\r\nX-Requested-With: Thingino-WebUI\r\nContent-Length: 0\r\n\r\n".as_slice(),
        ] {
            assert_eq!(parse_request(rejected), Err(RequestError::BadRequest));
        }
    }
}
