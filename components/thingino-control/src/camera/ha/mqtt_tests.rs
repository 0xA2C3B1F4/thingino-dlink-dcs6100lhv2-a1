use super::*;
use std::process::{Child, Command as ProcessBuilder, Stdio};
use std::time::{Duration, Instant};

fn test_port(name: &str) -> Option<u16> {
    std::env::var(name).ok()?.parse().ok()
}

fn connected(client: &mut Client, host: &str, port: u16) -> Result<(), &'static str> {
    client.connect(host, port, 5)?;
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        client.loop_once(20)?;
        if let Some(result) = client.take_connect_result() {
            return if result == 0 {
                Ok(())
            } else {
                Err("broker rejected test connection")
            };
        }
    }
    Err("test connection timed out")
}

fn start_test_broker(binary: &str, config: &str) -> Child {
    ProcessBuilder::new(binary)
        .args(["-c", config])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("restart test broker must start")
}

fn connect_after_start(client: &mut Client, port: u16) {
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        if connected(client, "127.0.0.1", port).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!("restart test broker did not accept a connection");
}

fn collect_until(
    observer: &mut Client,
    publisher: Option<&mut Client>,
    expected: usize,
) -> Vec<Message> {
    let mut publisher = publisher;
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut messages = Vec::new();
    while Instant::now() < deadline && messages.len() < expected {
        if let Some(client) = publisher.as_mut() {
            client.loop_once(5).unwrap();
        }
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            messages.push(message);
        }
    }
    messages
}

#[test]
fn topic_and_payload_bounds_fail_closed() {
    assert!(topic_cstring("cameras/camera/ircut/state").is_ok());
    for invalid in ["", "bad topic", "bad/+", "bad/#", "bad\n"] {
        assert!(topic_cstring(invalid).is_err(), "accepted {invalid:?}");
    }
    assert!(topic_cstring(&"a".repeat(MAX_TOPIC_BYTES + 1)).is_err());
}

#[test]
fn ffi_library_and_client_lifecycle_is_owned() {
    let api = Api::load().expect("host libmosquitto must be installed");
    let mut client = Client::new(api, "thingino-control-unit").unwrap();
    client.set_will("cameras/unit/status", b"offline").unwrap();
    client.set_credentials("unit", None).unwrap();
    assert!(
        client
            .publish("cameras/unit/state", &vec![0; MAX_PAYLOAD_BYTES + 1], true)
            .is_err()
    );
    drop(client);
}

#[test]
fn local_brokers_cover_retained_lwt_auth_tls_and_callback_bounds() {
    let Some(plain_port) = test_port("THINGINO_HA_TEST_BROKER_PORT") else {
        return;
    };
    let auth_port = test_port("THINGINO_HA_TEST_AUTH_PORT").unwrap();
    let tls_port = test_port("THINGINO_HA_TEST_TLS_PORT").unwrap();
    let absent_port = test_port("THINGINO_HA_TEST_ABSENT_PORT").unwrap();
    let username = std::env::var("THINGINO_HA_TEST_USERNAME").unwrap();
    let password = std::env::var("THINGINO_HA_TEST_PASSWORD").unwrap();
    let ca = std::env::var("THINGINO_HA_TEST_CA").unwrap();
    let invalid_ca = std::env::var("THINGINO_HA_TEST_INVALID_CA").unwrap();
    let mosquitto = std::env::var("THINGINO_HA_TEST_MOSQUITTO").unwrap();
    let restart_config = std::env::var("THINGINO_HA_TEST_RESTART_CONFIG").unwrap();
    let api = Api::load().unwrap();
    let prefix = format!("thingino-control-test-{}", std::process::id());
    let availability = format!("{prefix}/availability");
    let discovery = format!("{prefix}/discovery");
    let state = format!("{prefix}/state");

    let mut observer = Client::new(api.clone(), &format!("{prefix}-observer")).unwrap();
    observer.set_test_inbound_payload_limit(MAX_PAYLOAD_BYTES);
    connected(&mut observer, "127.0.0.1", plain_port).unwrap();
    for topic in [&availability, &discovery, &state] {
        observer.subscribe(topic).unwrap();
    }
    observer.loop_once(20).unwrap();

    let mut publisher = Client::new(api.clone(), &format!("{prefix}-publisher")).unwrap();
    publisher.set_will(&availability, b"offline").unwrap();
    connected(&mut publisher, "127.0.0.1", plain_port).unwrap();
    publisher.publish(&availability, b"online", true).unwrap();
    publisher
        .publish(&discovery, b"{\"name\":\"Camera\"}", true)
        .unwrap();
    publisher.publish(&state, b"ON", true).unwrap();
    let live = collect_until(&mut observer, Some(&mut publisher), 3);
    assert_eq!(live.len(), 3);
    assert!(live.iter().all(|message| message.qos == 1));

    let mut retained = Client::new(api.clone(), &format!("{prefix}-retained")).unwrap();
    retained.set_test_inbound_payload_limit(MAX_PAYLOAD_BYTES);
    connected(&mut retained, "127.0.0.1", plain_port).unwrap();
    for topic in [&availability, &discovery, &state] {
        retained.subscribe(topic).unwrap();
    }
    let retained_messages = collect_until(&mut retained, Some(&mut publisher), 3);
    assert_eq!(retained_messages.len(), 3);
    assert!(retained_messages.iter().all(|message| message.retained));
    retained.set_test_inbound_payload_limit(MAX_COMMAND_PAYLOAD_BYTES);

    drop(publisher);
    let lwt = collect_until(&mut retained, None, 1);
    assert!(lwt.iter().any(|message| {
        message.topic == availability && message.payload == b"offline" && message.qos == 1
    }));

    let mut offline_retained =
        Client::new(api.clone(), &format!("{prefix}-offline-retained")).unwrap();
    connected(&mut offline_retained, "127.0.0.1", plain_port).unwrap();
    offline_retained.subscribe(&availability).unwrap();
    let offline = collect_until(&mut offline_retained, None, 1);
    assert!(offline.iter().any(|message| {
        message.topic == availability
            && message.payload == b"offline"
            && message.qos == 1
            && message.retained
    }));

    let mut flood = Client::new(api.clone(), &format!("{prefix}-publisher")).unwrap();
    connected(&mut flood, "127.0.0.1", plain_port).unwrap();
    let before_oversized = retained.dropped_messages();
    flood.publish(&state, &[b'X'; 17], false).unwrap();
    let oversized_deadline = Instant::now() + Duration::from_secs(3);
    while retained.dropped_messages() == before_oversized && Instant::now() < oversized_deadline {
        flood.loop_once(5).unwrap();
        retained.loop_once(5).unwrap();
    }
    assert!(retained.dropped_messages() > before_oversized);
    let before_flood = retained.dropped_messages();
    for index in 0..(CALLBACK_QUEUE_CAPACITY * 4) {
        flood
            .publish(&state, index.to_string().as_bytes(), false)
            .unwrap();
        flood.loop_once(0).unwrap();
        retained.loop_once(0).unwrap();
    }
    let flood_deadline = Instant::now() + Duration::from_secs(3);
    while retained.dropped_messages() == before_flood && Instant::now() < flood_deadline {
        flood.loop_once(5).unwrap();
        retained.loop_once(5).unwrap();
    }
    assert!(retained.dropped_messages() > before_flood);

    let mut authenticated = Client::new(api.clone(), &format!("{prefix}-auth-ok")).unwrap();
    authenticated
        .set_credentials(&username, Some(&password))
        .unwrap();
    connected(&mut authenticated, "127.0.0.1", auth_port).unwrap();
    let mut rejected = Client::new(api.clone(), &format!("{prefix}-auth-bad")).unwrap();
    rejected
        .set_credentials(&username, Some(&format!("{password}-wrong")))
        .unwrap();
    assert!(connected(&mut rejected, "127.0.0.1", auth_port).is_err());

    let mut tls = Client::new(api.clone(), &format!("{prefix}-tls-ok")).unwrap();
    tls.set_tls(Some(&ca), None, false).unwrap();
    connected(&mut tls, "localhost", tls_port).unwrap();
    let mut invalid_tls = Client::new(api.clone(), &format!("{prefix}-tls-bad")).unwrap();
    invalid_tls.set_tls(Some(&invalid_ca), None, false).unwrap();
    assert!(connected(&mut invalid_tls, "localhost", tls_port).is_err());

    let restart_api = api.clone();
    let mut absent = Client::new(api, &format!("{prefix}-absent")).unwrap();
    assert!(connected(&mut absent, "127.0.0.1", absent_port).is_err());

    let mut first_broker = start_test_broker(&mosquitto, &restart_config);
    let mut before_restart =
        Client::new(restart_api.clone(), &format!("{prefix}-restart-client")).unwrap();
    connect_after_start(&mut before_restart, absent_port);
    first_broker.kill().unwrap();
    first_broker.wait().unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while before_restart.loop_once(20).is_ok() && Instant::now() < deadline {}
    assert!(before_restart.loop_once(0).is_err());
    drop(before_restart);

    let mut second_broker = start_test_broker(&mosquitto, &restart_config);
    let mut after_restart = Client::new(restart_api, &format!("{prefix}-restart-client")).unwrap();
    connect_after_start(&mut after_restart, absent_port);
    second_broker.kill().unwrap();
    second_broker.wait().unwrap();
}
