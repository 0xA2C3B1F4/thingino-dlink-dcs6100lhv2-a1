use super::*;

pub(crate) fn request_authorized(request: &Request, state: &SharedState) -> bool {
    let browser_session = matches!(
        (request.method.as_str(), request.target.as_str()),
        ("POST", "/api/v1/auth/password")
            | ("GET" | "POST", "/api/v1/config/access")
            | ("GET" | "POST" | "DELETE", "/api/v1/webui/api-key")
    );
    if browser_session {
        let recent_authentication = matches!(
            (request.method.as_str(), request.target.as_str()),
            ("POST", "/api/v1/auth/password")
                | ("POST", "/api/v1/config/access")
                | ("POST" | "DELETE", "/api/v1/webui/api-key")
        );
        return request.proxy_marker.as_deref() == Some(b"1")
            && state.web_auth.as_ref().is_some_and(|auth| {
                if recent_authentication {
                    auth.authorize_recent_session(request.cookie.as_deref())
                } else {
                    auth.authorize_session(request.cookie.as_deref())
                }
            });
    }
    if authorized(request.authorization.as_deref(), &state.token) {
        return true;
    }
    if request.proxy_marker.as_deref() != Some(b"1") {
        return false;
    }
    state.web_auth.as_ref().is_some_and(|auth| {
        auth.authorize(
            request.cookie.as_deref(),
            request.api_key.as_deref(),
            request.remote_address.as_deref(),
            request.method == "GET",
        )
    })
}

fn mutating_request_has_csrf_guard(request: &Request) -> bool {
    if !matches!(request.method.as_str(), "POST" | "PUT" | "DELETE") {
        return true;
    }
    if !request.body.is_empty() && !mutation_media_type_matches(request) {
        return false;
    }
    has_json_content_type(request)
        || request.api_key.is_some()
        || request.authorization.is_some()
        || request.requested_with.as_deref() == Some(b"Thingino-WebUI")
}

fn mutation_media_type_matches(request: &Request) -> bool {
    if request.target.starts_with("/api/v1/files/text?") {
        return has_content_type(request, "text/plain");
    }
    if request.target == "/api/v1/network/probe" {
        return has_content_type(request, "application/x-www-form-urlencoded");
    }
    has_json_content_type(request)
}

fn has_content_type(request: &Request, expected: &str) -> bool {
    request.content_type.as_deref().is_some_and(|value| {
        std::str::from_utf8(value).ok().is_some_and(|value| {
            let value = value.trim().to_ascii_lowercase();
            value == expected || value.starts_with(&format!("{expected};"))
        })
    })
}

pub(crate) fn handle_public_auth_route(
    stream: &mut TcpStream,
    request: &Request,
    state: &SharedState,
    deadline: Instant,
) -> bool {
    if request.proxy_marker.as_deref() != Some(b"1") {
        return false;
    }
    let Some(auth) = state.web_auth.as_ref() else {
        return false;
    };
    match (request.method.as_str(), request.target.as_str()) {
        ("POST", "/api/v1/auth/login") => {
            if !has_json_content_type(request) {
                let _ = send_error(
                    stream,
                    deadline,
                    415,
                    "unsupported_media_type",
                    "Content-Type must be application/json",
                );
                return true;
            }
            if !auth.admit_login(request.remote_address.as_deref()) {
                let _ = send_error(
                    stream,
                    deadline,
                    429,
                    "login_rate_limited",
                    "too many login attempts",
                );
                return true;
            }
            match auth.login(&request.body) {
                Ok(result) => {
                    let body = format!(
                        "{{\"success\":true,\"is_default_password\":{}}}\n",
                        if result.is_default_password {
                            "true"
                        } else {
                            "false"
                        }
                    );
                    let cookie = format!(
                        "thingino_session={}; Path=/; Max-Age=86400; HttpOnly; SameSite=Strict",
                        result.session_id
                    );
                    let _ = send_response_headers(
                        stream,
                        deadline,
                        200,
                        "OK",
                        "application/json",
                        &body.into_bytes(),
                        &[("Set-Cookie", cookie.as_str())],
                    );
                }
                Err(AuthError::InvalidCredentials) => {
                    let _ = send_error(
                        stream,
                        deadline,
                        401,
                        "invalid_credentials",
                        "invalid credentials",
                    );
                }
                Err(AuthError::InvalidRequest) => {
                    let _ = send_error(
                        stream,
                        deadline,
                        400,
                        "invalid_login",
                        "invalid login request",
                    );
                }
                Err(AuthError::Unavailable) => {
                    let _ = send_error(
                        stream,
                        deadline,
                        503,
                        "auth_unavailable",
                        "authentication is unavailable",
                    );
                }
            }
            true
        }
        ("POST", "/api/v1/auth/logout") if request.body.is_empty() => {
            auth.logout(request.cookie.as_deref());
            let _ = send_response_headers(
                stream,
                deadline,
                204,
                "No Content",
                "application/json",
                b"",
                &[(
                    "Set-Cookie",
                    "thingino_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                )],
            );
            true
        }
        ("GET", "/api/v1/auth/logout") if request.body.is_empty() => {
            auth.logout(request.cookie.as_deref());
            let _ = send_response_headers(
                stream,
                deadline,
                302,
                "Found",
                "application/json",
                b"",
                &[
                    (
                        "Set-Cookie",
                        "thingino_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                    ),
                    ("Location", "/login.html"),
                ],
            );
            true
        }
        ("GET", "/api/v1/auth/session") if request.body.is_empty() => {
            let status =
                auth.session_status(request.cookie.as_deref(), request.remote_address.as_deref());
            let username = status
                .username
                .map(|value| format!("\"{value}\""))
                .unwrap_or_else(|| "null".to_owned());
            let body = format!(
                "{{\"authenticated\":{},\"username\":{},\"is_default_password\":{},\"client_ip\":\"{}\",\"control_api\":{{\"name\":\"Thingino Control\",\"version\":1}}}}\n",
                if status.authenticated {
                    "true"
                } else {
                    "false"
                },
                username,
                if status.is_default_password {
                    "true"
                } else {
                    "false"
                },
                status.client_ip,
            );
            let _ = send_response(
                stream,
                deadline,
                200,
                "OK",
                "application/json",
                body.as_bytes(),
            );
            true
        }
        _ => false,
    }
}

pub(crate) fn handle_api_key_route(
    stream: &mut TcpStream,
    request: &Request,
    state: &SharedState,
    deadline: Instant,
) -> bool {
    let Some(auth) = state.web_auth.as_ref() else {
        return false;
    };
    if request.method == "POST"
        && request.target == "/api/v1/config/access"
        && !request.body.is_empty()
    {
        let document = match json::parse(&request.body) {
            Ok(document) => document,
            Err(_) => return false,
        };
        let password = document
            .get_path("password")
            .and_then(json::Value::as_str)
            .or_else(|| {
                document
                    .get_path("rtsp.password")
                    .and_then(json::Value::as_str)
            })
            .filter(|value| !value.is_empty());
        if let Some(password) = password {
            if !has_json_content_type(request) {
                let _ = send_error(
                    stream,
                    deadline,
                    415,
                    "unsupported_media_type",
                    "Content-Type must be application/json",
                );
                return true;
            }
            let Ok(_credential_guard) = auth.lock_credential_update() else {
                let _ = send_error(
                    stream,
                    deadline,
                    503,
                    "credential_update_failed",
                    "management credential storage is unavailable",
                );
                return true;
            };
            let mut password_document = BTreeMap::new();
            password_document.insert(
                "password".to_owned(),
                json::Value::String(password.to_owned()),
            );
            let password_body = json::Value::Object(password_document).to_json();
            let (_, original_shadow) = match auth.change_password(password_body.as_bytes()) {
                Ok(change) => change,
                Err(AuthError::InvalidRequest | AuthError::InvalidCredentials) => {
                    let _ = send_error(
                        stream,
                        deadline,
                        400,
                        "invalid_request",
                        "password must contain 10 to 128 bytes",
                    );
                    return true;
                }
                Err(AuthError::Unavailable) => {
                    let _ = send_error(
                        stream,
                        deadline,
                        503,
                        "credential_update_failed",
                        "management credential storage is unavailable",
                    );
                    return true;
                }
            };
            let result =
                state
                    .backend
                    .api_request("POST", "/api/v1/config/access", &request.body, deadline);
            match result {
                Some(Ok(response)) => {
                    send_backend_result(stream, deadline, Ok(response), None);
                }
                Some(Err(error)) => {
                    let _ = auth.restore_password(&original_shadow);
                    send_backend_result(stream, deadline, Err(error), None);
                }
                None => {
                    let _ = auth.restore_password(&original_shadow);
                    let _ = send_error(
                        stream,
                        deadline,
                        503,
                        "credential_update_failed",
                        "WebUI, ONVIF, and RTSP credentials were not updated together",
                    );
                }
            }
            return true;
        }
    }
    if request.method == "POST" && request.target == "/api/v1/auth/password" {
        if request.body.is_empty() || !has_json_content_type(request) {
            let (status, code, message) = if request.body.is_empty() {
                (400, "invalid_request", "request body is required")
            } else {
                (
                    415,
                    "unsupported_media_type",
                    "Content-Type must be application/json",
                )
            };
            let _ = send_error(stream, deadline, status, code, message);
            return true;
        }
        let Ok(_credential_guard) = auth.lock_credential_update() else {
            let _ = send_error(
                stream,
                deadline,
                503,
                "credential_update_failed",
                "management credential storage is unavailable",
            );
            return true;
        };
        let (password, original_shadow) = match auth.change_password(&request.body) {
            Ok(change) => change,
            Err(AuthError::InvalidRequest | AuthError::InvalidCredentials) => {
                let _ = send_error(
                    stream,
                    deadline,
                    400,
                    "invalid_request",
                    "password must contain 10 to 128 bytes",
                );
                return true;
            }
            Err(AuthError::Unavailable) => {
                let _ = send_error(
                    stream,
                    deadline,
                    503,
                    "credential_update_failed",
                    "management credential storage is unavailable",
                );
                return true;
            }
        };
        let mut access = BTreeMap::new();
        access.insert(
            "username".to_owned(),
            json::Value::String("root".to_owned()),
        );
        access.insert("password".to_owned(), json::Value::String(password));
        let access_body = json::Value::Object(access).to_json();
        let updated = matches!(
            state.backend.api_request(
                "POST",
                "/api/v1/config/access",
                access_body.as_bytes(),
                deadline,
            ),
            Some(Ok(_))
        );
        if !updated {
            let _ = auth.restore_password(&original_shadow);
            let _ = send_error(
                stream,
                deadline,
                503,
                "credential_update_failed",
                "WebUI, ONVIF, and RTSP credentials were not updated together",
            );
            return true;
        }
        auth.invalidate_sessions();
        let _ = send_response(
            stream,
            deadline,
            200,
            "OK",
            "application/json",
            b"{\"status\":\"ok\",\"management_password_changed\":true}\n",
        );
        return true;
    }
    let _credential_guard = if matches!(request.method.as_str(), "POST" | "DELETE")
        && request.target == "/api/v1/webui/api-key"
    {
        match auth.lock_credential_update() {
            Ok(guard) => Some(guard),
            Err(_) => {
                let _ = send_error(
                    stream,
                    deadline,
                    503,
                    "auth_unavailable",
                    "API-key storage is unavailable",
                );
                return true;
            }
        }
    } else {
        None
    };
    let result = match (request.method.as_str(), request.target.as_str()) {
        ("GET", "/api/v1/webui/api-key") if request.body.is_empty() => auth
            .api_key_exists()
            .map(|exists| (200, format!("{{\"exists\":{exists}}}\n"))),
        ("POST", "/api/v1/webui/api-key") if request.body.is_empty() => {
            auth.generate_api_key().map(|key| {
                (
                    200,
                    format!("{{\"api_key\":\"{key}\",\"generated\":true}}\n"),
                )
            })
        }
        ("DELETE", "/api/v1/webui/api-key") if request.body.is_empty() => {
            auth.delete_api_key().map(|()| (204, String::new()))
        }
        _ => return false,
    };
    match result {
        Ok((status, body)) => {
            let _ = send_response(
                stream,
                deadline,
                status,
                if status == 204 { "No Content" } else { "OK" },
                "application/json",
                body.as_bytes(),
            );
        }
        Err(AuthError::InvalidRequest | AuthError::InvalidCredentials) => {
            let _ = send_error(
                stream,
                deadline,
                400,
                "invalid_request",
                "request body or parameters are invalid",
            );
        }
        Err(AuthError::Unavailable) => {
            let _ = send_error(
                stream,
                deadline,
                503,
                "api_key_unavailable",
                "API key storage is unavailable",
            );
        }
    }
    true
}

fn send_whip_error(stream: &mut TcpStream, deadline: Instant, error: BackendError) {
    let (status, code, message) = match error {
        BackendError::Timeout => (504, "backend_timeout", "WebRTC signaling timed out"),
        BackendError::Connection | BackendError::Unavailable => (
            503,
            "service_unavailable",
            "WebRTC signaling is unavailable",
        ),
        BackendError::Protocol | BackendError::Upstream(_) => (
            502,
            "bad_gateway",
            "WebRTC signaling returned an invalid response",
        ),
    };
    let _ = send_error(stream, deadline, status, code, message);
}

fn handle_whip_route(
    stream: &mut TcpStream,
    request: &Request,
    state: &SharedState,
    deadline: Instant,
) -> bool {
    const CREATE_STREAM0: &str = "/api/v1/media/webrtc/whip?stream=0";
    const CREATE_STREAM1: &str = "/api/v1/media/webrtc/whip?stream=1";
    const CREATE_PREFIX: &str = "/api/v1/media/webrtc/whip?stream=";
    const DELETE_PREFIX: &str = "/api/v1/media/webrtc/whip/";

    let create_stream = match request.target.as_str() {
        CREATE_STREAM0 => Some(0),
        CREATE_STREAM1 => Some(1),
        _ => None,
    };
    if request.method == "POST"
        && request.target.starts_with(CREATE_PREFIX)
        && create_stream.is_none()
    {
        let _ = send_error(stream, deadline, 404, "not_found", "unknown WHIP stream");
        return true;
    }
    if request.method == "POST"
        && let Some(stream_id) = create_stream
    {
        if request.body.is_empty() {
            let _ = send_error(
                stream,
                deadline,
                400,
                "invalid_request",
                "SDP offer is required",
            );
            return true;
        }
        if !has_sdp_content_type(request) {
            let _ = send_error(
                stream,
                deadline,
                415,
                "unsupported_media_type",
                "Content-Type must be application/sdp",
            );
            return true;
        }
        let Some(proxy) = state.whip.as_ref() else {
            send_whip_error(stream, deadline, BackendError::Unavailable);
            return true;
        };
        let backend_deadline = deadline.checked_sub(RESPONSE_RESERVE).unwrap_or(deadline);
        let Some(_permit) = state.gate.acquire(backend_deadline) else {
            send_whip_error(stream, deadline, BackendError::Timeout);
            return true;
        };
        match proxy.create(stream_id, &request.body, backend_deadline) {
            Ok(session) => {
                let location = format!("{DELETE_PREFIX}{}", session.session_id);
                let _ = send_response_headers(
                    stream,
                    deadline,
                    201,
                    "Created",
                    "application/sdp",
                    &session.answer,
                    &[("Location", location.as_str())],
                );
            }
            Err(error) => send_whip_error(stream, deadline, error),
        }
        return true;
    }

    if request.method == "DELETE" && request.target.starts_with(DELETE_PREFIX) {
        if !request.body.is_empty() {
            let _ = send_error(
                stream,
                deadline,
                400,
                "invalid_request",
                "WHIP DELETE body must be empty",
            );
            return true;
        }
        let session_id = &request.target[DELETE_PREFIX.len()..];
        if !whip::valid_session_id(session_id) {
            let _ = send_error(
                stream,
                deadline,
                400,
                "invalid_request",
                "invalid WHIP session identifier",
            );
            return true;
        }
        let Some(proxy) = state.whip.as_ref() else {
            send_whip_error(stream, deadline, BackendError::Unavailable);
            return true;
        };
        let backend_deadline = deadline.checked_sub(RESPONSE_RESERVE).unwrap_or(deadline);
        let Some(_permit) = state.gate.acquire(backend_deadline) else {
            send_whip_error(stream, deadline, BackendError::Timeout);
            return true;
        };
        match proxy.delete(session_id, backend_deadline) {
            Ok(()) => {
                let _ = send_response(stream, deadline, 204, "No Content", "application/sdp", b"");
            }
            Err(error) => send_whip_error(stream, deadline, error),
        }
        return true;
    }

    false
}

pub(crate) fn handle_client(mut stream: TcpStream, state: &SharedState, deadline: Instant) {
    if remaining(deadline).is_none() {
        drain_pending_input(&mut stream);
        let _ = send_error(
            &mut stream,
            deadline,
            504,
            "request_timeout",
            "request timed out",
        );
        return;
    }
    let request = match read_request(&mut stream, deadline) {
        Ok(request) => request,
        Err(RequestError::Timeout) => {
            let _ = send_error(
                &mut stream,
                deadline,
                400,
                "request_timeout",
                "request timed out",
            );
            return;
        }
        Err(RequestError::BadRequest) => {
            let _ = send_error(
                &mut stream,
                deadline,
                400,
                "bad_request",
                "invalid HTTP request",
            );
            return;
        }
        Err(RequestError::PayloadTooLarge) => {
            let _ = send_error(
                &mut stream,
                deadline,
                413,
                "payload_too_large",
                "request body is too large",
            );
            return;
        }
    };

    if handle_public_auth_route(&mut stream, &request, state, deadline) {
        return;
    }

    if request.proxy_marker.as_deref() == Some(b"2")
        && request.method == "GET"
        && request.body.is_empty()
    {
        let media_target = request
            .target
            .strip_prefix("/api/v1/internal/media-authorize?target=")
            .and_then(percent_decode);
        if media_target
            .as_deref()
            .is_some_and(|target| matches!(target, "/onvif/image.cgi" | "/onvif/image1.cgi"))
        {
            let _ = send_response(
                &mut stream,
                deadline,
                204,
                "No Content",
                "application/json",
                b"",
            );
            return;
        }
        let route = match request.target.as_str() {
            "/api/v1/internal/onvif-snapshot?stream=0" => Some(BackendRoute::Snapshot(0)),
            "/api/v1/internal/onvif-snapshot?stream=1" => Some(BackendRoute::Snapshot(1)),
            _ => None,
        };
        if let Some(route) = route {
            let backend_deadline = deadline.checked_sub(RESPONSE_RESERVE).unwrap_or(deadline);
            let Some(_permit) = state.gate.acquire(backend_deadline) else {
                let _ = send_error(
                    &mut stream,
                    deadline,
                    504,
                    "backend_timeout",
                    "backend timed out",
                );
                return;
            };
            send_backend_result(
                &mut stream,
                deadline,
                state.backend.request(route, backend_deadline),
                None,
            );
            return;
        }
    }

    if !request_authorized(&request, state) {
        let result = if request.proxy_marker.as_deref() == Some(b"1") && accepts_html(&request) {
            send_redirect(&mut stream, deadline, "/login.html")
        } else {
            send_error(
                &mut stream,
                deadline,
                401,
                "unauthorized",
                "authentication required",
            )
        };
        let _ = result;
        return;
    }

    if request.method == "GET" && request.body.is_empty() {
        let media_target = request
            .target
            .strip_prefix("/api/v1/internal/media-authorize?target=");
        if request.target == "/api/v1/internal/media-authorize"
            || media_target.is_some_and(|target| {
                percent_decode(target)
                    .as_deref()
                    .is_some_and(|target| state.backend.authorize_media(target))
            })
        {
            let _ = send_response(
                &mut stream,
                deadline,
                204,
                "No Content",
                "application/json",
                b"",
            );
            return;
        }
    }

    if handle_whip_route(&mut stream, &request, state, deadline) {
        return;
    }

    if !mutating_request_has_csrf_guard(&request) {
        let _ = send_error(
            &mut stream,
            deadline,
            415,
            "unsupported_media_type",
            "mutating requests require application/json or X-Requested-With",
        );
        return;
    }

    if handle_api_key_route(&mut stream, &request, state, deadline) {
        return;
    }

    let backend_deadline = deadline.checked_sub(RESPONSE_RESERVE).unwrap_or(deadline);
    let Some(api_permit) = state.gate.acquire(backend_deadline) else {
        let _ = send_error(
            &mut stream,
            deadline,
            504,
            "backend_timeout",
            "backend timed out",
        );
        return;
    };
    if let Some(result) = state.backend.api_request(
        &request.method,
        &request.target,
        &request.body,
        backend_deadline,
    ) {
        send_backend_result(&mut stream, deadline, result, None);
        return;
    }
    drop(api_permit);

    let route = match (request.method.as_str(), request.target.as_str()) {
        ("GET", "/api/v1/health") if request.body.is_empty() => BackendRoute::Health,
        ("GET", "/api/v1/runtime/media") if request.body.is_empty() => BackendRoute::RuntimeMedia,
        ("GET", "/api/v1/config") | ("GET", "/api/v1/config/") if request.body.is_empty() => {
            BackendRoute::Config
        }
        ("GET" | "POST", target) if target.starts_with("/api/v1/actions/snapshot?") => {
            if !request.body.is_empty() {
                let _ = send_error(
                    &mut stream,
                    deadline,
                    400,
                    "invalid_snapshot_request",
                    "snapshot body must be empty",
                );
                return;
            }
            match target.strip_prefix("/api/v1/actions/snapshot?stream_id=") {
                Some("0") => BackendRoute::Snapshot(0),
                Some("1") => BackendRoute::Snapshot(1),
                _ => {
                    let _ = send_error(
                        &mut stream,
                        deadline,
                        400,
                        "invalid_stream_id",
                        "stream_id must be 0 or 1",
                    );
                    return;
                }
            }
        }
        ("POST", "/api/v1/actions/daynight") => match parse_daynight_mode(&request.body) {
            Some(mode) => BackendRoute::DayNight(mode),
            None => {
                let _ = send_error(
                    &mut stream,
                    deadline,
                    400,
                    "invalid_daynight_mode",
                    "mode must be auto, day, or night",
                );
                return;
            }
        },
        _ => {
            let _ = send_error(&mut stream, deadline, 404, "not_found", "route not found");
            return;
        }
    };

    let backend_deadline = deadline.checked_sub(RESPONSE_RESERVE).unwrap_or(deadline);
    let Some(_permit) = state.gate.acquire(backend_deadline) else {
        let _ = send_error(
            &mut stream,
            deadline,
            504,
            "backend_timeout",
            "backend timed out",
        );
        return;
    };
    let snapshot = match route {
        BackendRoute::Snapshot(stream_id) => Some(stream_id),
        _ => None,
    };
    send_backend_result(
        &mut stream,
        deadline,
        state.backend.request(route, backend_deadline),
        snapshot,
    );
}
