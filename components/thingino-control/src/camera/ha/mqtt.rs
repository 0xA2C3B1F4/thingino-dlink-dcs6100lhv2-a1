use std::collections::VecDeque;
use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::ptr;
use std::rc::Rc;
use std::sync::Mutex;

pub(super) const MAX_TOPIC_BYTES: usize = 512;
pub(super) const MAX_PAYLOAD_BYTES: usize = 2 * 1024 * 1024;
pub(super) const MAX_COMMAND_PAYLOAD_BYTES: usize = 16;
pub(super) const CALLBACK_QUEUE_CAPACITY: usize = 64;

const RTLD_NOW: c_int = 2;
const MOSQ_ERR_SUCCESS: c_int = 0;
static LIBRARY_USERS: Mutex<usize> = Mutex::new(0);

#[cfg(target_os = "linux")]
#[link(name = "dl")]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}

#[repr(C)]
struct MosquittoMessage {
    mid: c_int,
    topic: *mut c_char,
    payload: *mut c_void,
    payload_len: c_int,
    qos: c_int,
    retain: bool,
}

type Mosquitto = c_void;
type ConnectCallback = unsafe extern "C" fn(*mut Mosquitto, *mut c_void, c_int);
type DisconnectCallback = unsafe extern "C" fn(*mut Mosquitto, *mut c_void, c_int);
type MessageCallback = unsafe extern "C" fn(*mut Mosquitto, *mut c_void, *const MosquittoMessage);

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct Message {
    pub(super) topic: String,
    pub(super) payload: Vec<u8>,
    pub(super) qos: i32,
    pub(super) retained: bool,
}

struct CallbackState {
    connect_result: Option<i32>,
    disconnect_result: Option<i32>,
    messages: VecDeque<Message>,
    dropped_messages: u64,
    max_payload_bytes: usize,
}

impl Default for CallbackState {
    fn default() -> Self {
        Self {
            connect_result: None,
            disconnect_result: None,
            messages: VecDeque::new(),
            dropped_messages: 0,
            max_payload_bytes: MAX_COMMAND_PAYLOAD_BYTES,
        }
    }
}

struct LibraryHandle {
    handle: *mut c_void,
    cleanup: unsafe extern "C" fn() -> c_int,
}

impl Drop for LibraryHandle {
    fn drop(&mut self) {
        // SAFETY: cleanup and dlclose came from this successfully loaded library.
        unsafe {
            if let Ok(mut users) = LIBRARY_USERS.lock() {
                *users = users.saturating_sub(1);
                if *users == 0 {
                    (self.cleanup)();
                }
            }
            dlclose(self.handle);
        }
    }
}

#[derive(Clone)]
pub(super) struct Api {
    _library: Rc<LibraryHandle>,
    new: unsafe extern "C" fn(*const c_char, bool, *mut c_void) -> *mut Mosquitto,
    destroy: unsafe extern "C" fn(*mut Mosquitto),
    connect_callback_set: unsafe extern "C" fn(*mut Mosquitto, Option<ConnectCallback>),
    disconnect_callback_set: unsafe extern "C" fn(*mut Mosquitto, Option<DisconnectCallback>),
    message_callback_set: unsafe extern "C" fn(*mut Mosquitto, Option<MessageCallback>),
    username_pw_set: unsafe extern "C" fn(*mut Mosquitto, *const c_char, *const c_char) -> c_int,
    tls_set: unsafe extern "C" fn(
        *mut Mosquitto,
        *const c_char,
        *const c_char,
        *const c_char,
        *const c_char,
        Option<unsafe extern "C" fn(*mut c_char, c_int, c_int, *mut c_void) -> c_int>,
    ) -> c_int,
    tls_insecure_set: unsafe extern "C" fn(*mut Mosquitto, bool) -> c_int,
    will_set: unsafe extern "C" fn(
        *mut Mosquitto,
        *const c_char,
        c_int,
        *const c_void,
        c_int,
        bool,
    ) -> c_int,
    connect: unsafe extern "C" fn(*mut Mosquitto, *const c_char, c_int, c_int) -> c_int,
    disconnect: unsafe extern "C" fn(*mut Mosquitto) -> c_int,
    loop_once: unsafe extern "C" fn(*mut Mosquitto, c_int, c_int) -> c_int,
    publish: unsafe extern "C" fn(
        *mut Mosquitto,
        *mut c_int,
        *const c_char,
        c_int,
        *const c_void,
        c_int,
        bool,
    ) -> c_int,
    want_write: unsafe extern "C" fn(*mut Mosquitto) -> bool,
    subscribe: unsafe extern "C" fn(*mut Mosquitto, *mut c_int, *const c_char, c_int) -> c_int,
}

impl Api {
    pub(super) fn load() -> Result<Self, &'static str> {
        let mut handle = ptr::null_mut();
        for candidate in library_candidates() {
            let candidate = CString::new(*candidate).map_err(|_| "invalid library name")?;
            // SAFETY: candidate is a terminated library name and RTLD_NOW is valid.
            handle = unsafe { dlopen(candidate.as_ptr(), RTLD_NOW) };
            if !handle.is_null() {
                break;
            }
        }
        if handle.is_null() {
            return Err("libmosquitto unavailable");
        }

        macro_rules! symbol {
            ($name:literal, $kind:ty) => {{
                let name = CString::new($name).expect("static symbol has no NUL");
                // SAFETY: handle is live; the symbol is checked before converting
                // the C function pointer to its documented libmosquitto signature.
                let raw = unsafe { dlsym(handle, name.as_ptr()) };
                if raw.is_null() {
                    // SAFETY: handle came from dlopen and has not been closed.
                    unsafe { dlclose(handle) };
                    return Err("libmosquitto symbol unavailable");
                }
                // SAFETY: libmosquitto's public ABI fixes this symbol signature.
                unsafe { std::mem::transmute::<*mut c_void, $kind>(raw) }
            }};
        }

        let init = symbol!("mosquitto_lib_init", unsafe extern "C" fn() -> c_int);
        let cleanup = symbol!("mosquitto_lib_cleanup", unsafe extern "C" fn() -> c_int);
        let new = symbol!(
            "mosquitto_new",
            unsafe extern "C" fn(*const c_char, bool, *mut c_void) -> *mut Mosquitto
        );
        let destroy = symbol!("mosquitto_destroy", unsafe extern "C" fn(*mut Mosquitto));
        let connect_callback_set = symbol!(
            "mosquitto_connect_callback_set",
            unsafe extern "C" fn(*mut Mosquitto, Option<ConnectCallback>)
        );
        let disconnect_callback_set = symbol!(
            "mosquitto_disconnect_callback_set",
            unsafe extern "C" fn(*mut Mosquitto, Option<DisconnectCallback>)
        );
        let message_callback_set = symbol!(
            "mosquitto_message_callback_set",
            unsafe extern "C" fn(*mut Mosquitto, Option<MessageCallback>)
        );
        let username_pw_set = symbol!(
            "mosquitto_username_pw_set",
            unsafe extern "C" fn(*mut Mosquitto, *const c_char, *const c_char) -> c_int
        );
        let tls_set = symbol!(
            "mosquitto_tls_set",
            unsafe extern "C" fn(
                *mut Mosquitto,
                *const c_char,
                *const c_char,
                *const c_char,
                *const c_char,
                Option<unsafe extern "C" fn(*mut c_char, c_int, c_int, *mut c_void) -> c_int>,
            ) -> c_int
        );
        let tls_insecure_set = symbol!(
            "mosquitto_tls_insecure_set",
            unsafe extern "C" fn(*mut Mosquitto, bool) -> c_int
        );
        let will_set = symbol!(
            "mosquitto_will_set",
            unsafe extern "C" fn(
                *mut Mosquitto,
                *const c_char,
                c_int,
                *const c_void,
                c_int,
                bool,
            ) -> c_int
        );
        let connect = symbol!(
            "mosquitto_connect",
            unsafe extern "C" fn(*mut Mosquitto, *const c_char, c_int, c_int) -> c_int
        );
        let disconnect = symbol!(
            "mosquitto_disconnect",
            unsafe extern "C" fn(*mut Mosquitto) -> c_int
        );
        let loop_once = symbol!(
            "mosquitto_loop",
            unsafe extern "C" fn(*mut Mosquitto, c_int, c_int) -> c_int
        );
        let publish = symbol!(
            "mosquitto_publish",
            unsafe extern "C" fn(
                *mut Mosquitto,
                *mut c_int,
                *const c_char,
                c_int,
                *const c_void,
                c_int,
                bool,
            ) -> c_int
        );
        let subscribe = symbol!(
            "mosquitto_subscribe",
            unsafe extern "C" fn(*mut Mosquitto, *mut c_int, *const c_char, c_int) -> c_int
        );
        let want_write = symbol!(
            "mosquitto_want_write",
            unsafe extern "C" fn(*mut Mosquitto) -> bool
        );
        let mut users = LIBRARY_USERS
            .lock()
            .map_err(|_| "libmosquitto lifecycle lock unavailable")?;
        if *users == 0 {
            // SAFETY: init is serialized and is the matching library initializer.
            if unsafe { init() } != MOSQ_ERR_SUCCESS {
                // SAFETY: handle is live and init did not create caller-owned state.
                unsafe { dlclose(handle) };
                return Err("libmosquitto initialization failed");
            }
        }
        *users = users.saturating_add(1);
        drop(users);
        let library = Rc::new(LibraryHandle { handle, cleanup });
        Ok(Self {
            _library: library,
            new,
            destroy,
            connect_callback_set,
            disconnect_callback_set,
            message_callback_set,
            username_pw_set,
            tls_set,
            tls_insecure_set,
            will_set,
            connect,
            disconnect,
            loop_once,
            publish,
            subscribe,
            want_write,
        })
    }
}

#[cfg(target_os = "linux")]
fn library_candidates() -> &'static [&'static str] {
    &["libmosquitto.so.1", "libmosquitto.so"]
}

#[cfg(target_os = "macos")]
fn library_candidates() -> &'static [&'static str] {
    &[
        "libmosquitto.1.dylib",
        "libmosquitto.dylib",
        "/opt/homebrew/lib/libmosquitto.1.dylib",
        "/usr/local/lib/libmosquitto.1.dylib",
    ]
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn library_candidates() -> &'static [&'static str] {
    &[]
}

pub(super) struct Client {
    api: Api,
    client: *mut Mosquitto,
    callbacks: Box<CallbackState>,
    #[cfg(test)]
    pub(super) fail_next_image_write: bool,
}

impl Client {
    pub(super) fn new(api: Api, client_id: &str) -> Result<Self, &'static str> {
        let id = bounded_cstring(client_id, 128).ok_or("invalid MQTT client ID")?;
        let mut callbacks = Box::<CallbackState>::default();
        // SAFETY: callback storage is boxed and remains at a stable address for
        // the full mosquitto client lifetime.
        let client = unsafe {
            (api.new)(
                id.as_ptr(),
                true,
                (&mut *callbacks as *mut CallbackState).cast(),
            )
        };
        if client.is_null() {
            return Err("MQTT client allocation failed");
        }
        // SAFETY: client is live and callbacks use the matching userdata type.
        unsafe {
            (api.connect_callback_set)(client, Some(on_connect));
            (api.disconnect_callback_set)(client, Some(on_disconnect));
            (api.message_callback_set)(client, Some(on_message));
        }
        Ok(Self {
            api,
            client,
            callbacks,
            #[cfg(test)]
            fail_next_image_write: false,
        })
    }

    pub(super) fn set_credentials(
        &mut self,
        username: &str,
        password: Option<&str>,
    ) -> Result<(), &'static str> {
        if username.is_empty() {
            return Ok(());
        }
        let username = bounded_cstring(username, 128).ok_or("invalid MQTT username")?;
        let password = password
            .map(|value| bounded_cstring(value, 128).ok_or("invalid MQTT password"))
            .transpose()?;
        // SAFETY: client and C strings remain valid for the duration of the call;
        // libmosquitto copies the credential values.
        let result = unsafe {
            (self.api.username_pw_set)(
                self.client,
                username.as_ptr(),
                password
                    .as_ref()
                    .map_or(ptr::null(), |value| value.as_ptr()),
            )
        };
        success(result, "MQTT credentials rejected")
    }

    pub(super) fn set_tls(
        &mut self,
        ca_file: Option<&str>,
        ca_path: Option<&str>,
        insecure: bool,
    ) -> Result<(), &'static str> {
        let ca_file = ca_file
            .map(|value| bounded_cstring(value, 512).ok_or("invalid MQTT CA file"))
            .transpose()?;
        let ca_path = ca_path
            .map(|value| bounded_cstring(value, 512).ok_or("invalid MQTT CA path"))
            .transpose()?;
        // SAFETY: libmosquitto copies the supplied paths during configuration.
        let result = unsafe {
            (self.api.tls_set)(
                self.client,
                ca_file.as_ref().map_or(ptr::null(), |value| value.as_ptr()),
                ca_path.as_ref().map_or(ptr::null(), |value| value.as_ptr()),
                ptr::null(),
                ptr::null(),
                None,
            )
        };
        success(result, "MQTT TLS configuration failed")?;
        // SAFETY: client remains live and the flag is copied.
        success(
            unsafe { (self.api.tls_insecure_set)(self.client, insecure) },
            "MQTT TLS verification configuration failed",
        )
    }

    pub(super) fn set_will(&mut self, topic: &str, payload: &[u8]) -> Result<(), &'static str> {
        let topic = topic_cstring(topic)?;
        let length = c_int::try_from(payload.len()).map_err(|_| "MQTT will payload too large")?;
        // SAFETY: all slices are valid for this call and libmosquitto copies them.
        success(
            unsafe {
                (self.api.will_set)(
                    self.client,
                    topic.as_ptr(),
                    length,
                    payload.as_ptr().cast(),
                    1,
                    true,
                )
            },
            "MQTT will configuration failed",
        )
    }

    pub(super) fn connect(
        &mut self,
        host: &str,
        port: u16,
        keepalive: u16,
    ) -> Result<(), &'static str> {
        let host = bounded_cstring(host, 255).ok_or("invalid MQTT host")?;
        self.callbacks.connect_result = None;
        self.callbacks.disconnect_result = None;
        // SAFETY: client is live and host is valid for the call.
        success(
            unsafe {
                (self.api.connect)(
                    self.client,
                    host.as_ptr(),
                    c_int::from(port),
                    c_int::from(keepalive),
                )
            },
            "MQTT connection failed",
        )
    }

    pub(super) fn loop_once(&mut self, timeout_ms: i32) -> Result<(), &'static str> {
        // SAFETY: all callbacks run synchronously in this thread because the
        // separate libmosquitto loop thread API is never called.
        success(
            unsafe { (self.api.loop_once)(self.client, timeout_ms.clamp(0, 1_000), 8) },
            "MQTT loop failed",
        )
    }

    pub(super) fn take_connect_result(&mut self) -> Option<i32> {
        self.callbacks.connect_result.take()
    }

    pub(super) fn take_disconnect_result(&mut self) -> Option<i32> {
        self.callbacks.disconnect_result.take()
    }

    pub(super) fn take_message(&mut self) -> Option<Message> {
        self.callbacks.messages.pop_front()
    }

    pub(super) fn dropped_messages(&self) -> u64 {
        self.callbacks.dropped_messages
    }

    #[cfg(test)]
    pub(super) fn set_test_inbound_payload_limit(&mut self, limit: usize) {
        self.callbacks.max_payload_bytes = limit.min(MAX_PAYLOAD_BYTES);
    }

    pub(super) fn publish(
        &mut self,
        topic: &str,
        payload: &[u8],
        retained: bool,
    ) -> Result<(), &'static str> {
        self.publish_qos(topic, payload, retained, 1)
    }

    pub(super) fn pending_write(&self) -> bool {
        // SAFETY: the sole worker owns this live client.
        unsafe { (self.api.want_write)(self.client) }
    }

    pub(super) fn publish_image(
        &mut self,
        topic: &str,
        payload: &[u8],
    ) -> Result<(), &'static str> {
        if payload.len() > 256 * 1024 || self.pending_write() {
            return Err("MQTT image queue unavailable");
        }
        #[cfg(test)]
        if std::mem::take(&mut self.fail_next_image_write) {
            return Err("injected MQTT image write failure");
        }
        self.publish_qos(topic, payload, false, 0)?;
        let until = std::time::Instant::now() + std::time::Duration::from_millis(250);
        while self.pending_write() && std::time::Instant::now() < until {
            self.loop_once(10)?;
        }
        if self.pending_write() {
            return Err("MQTT image write timed out");
        }
        Ok(())
    }

    fn publish_qos(
        &mut self,
        topic: &str,
        payload: &[u8],
        retained: bool,
        qos: c_int,
    ) -> Result<(), &'static str> {
        let topic = topic_cstring(topic)?;
        if payload.len() > MAX_PAYLOAD_BYTES {
            return Err("MQTT payload too large");
        }
        let length = c_int::try_from(payload.len()).map_err(|_| "MQTT payload too large")?;
        // SAFETY: libmosquitto copies topic and payload before returning.
        success(
            unsafe {
                (self.api.publish)(
                    self.client,
                    ptr::null_mut(),
                    topic.as_ptr(),
                    length,
                    payload.as_ptr().cast(),
                    qos,
                    retained,
                )
            },
            "MQTT publish failed",
        )
    }

    pub(super) fn subscribe(&mut self, topic: &str) -> Result<(), &'static str> {
        let topic = topic_cstring(topic)?;
        // SAFETY: libmosquitto copies the topic before returning.
        success(
            unsafe { (self.api.subscribe)(self.client, ptr::null_mut(), topic.as_ptr(), 1) },
            "MQTT subscribe failed",
        )
    }

    pub(super) fn disconnect(&mut self) {
        // SAFETY: client remains live until Drop.
        unsafe {
            (self.api.disconnect)(self.client);
        }
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        // SAFETY: this client was returned by mosquitto_new and is destroyed once.
        unsafe {
            (self.api.destroy)(self.client);
        }
    }
}

fn bounded_cstring(value: &str, maximum: usize) -> Option<CString> {
    (value.len() <= maximum
        && !value
            .bytes()
            .any(|byte| byte == 0 || byte.is_ascii_control()))
    .then(|| CString::new(value).ok())
    .flatten()
}

fn topic_cstring(topic: &str) -> Result<CString, &'static str> {
    if topic.is_empty()
        || topic.len() > MAX_TOPIC_BYTES
        || topic.bytes().any(|byte| {
            byte == 0
                || byte.is_ascii_control()
                || byte.is_ascii_whitespace()
                || byte == b'+'
                || byte == b'#'
        })
    {
        return Err("invalid MQTT topic");
    }
    CString::new(topic).map_err(|_| "invalid MQTT topic")
}

fn success(result: c_int, message: &'static str) -> Result<(), &'static str> {
    if result == MOSQ_ERR_SUCCESS {
        Ok(())
    } else {
        Err(message)
    }
}

unsafe extern "C" fn on_connect(_client: *mut Mosquitto, userdata: *mut c_void, result: c_int) {
    if userdata.is_null() {
        return;
    }
    // SAFETY: Client owns a boxed CallbackState and passes its stable address.
    unsafe { &mut *userdata.cast::<CallbackState>() }.connect_result = Some(result);
}

unsafe extern "C" fn on_disconnect(_client: *mut Mosquitto, userdata: *mut c_void, result: c_int) {
    if userdata.is_null() {
        return;
    }
    // SAFETY: Client owns a boxed CallbackState and passes its stable address.
    unsafe { &mut *userdata.cast::<CallbackState>() }.disconnect_result = Some(result);
}

unsafe extern "C" fn on_message(
    _client: *mut Mosquitto,
    userdata: *mut c_void,
    message: *const MosquittoMessage,
) {
    if userdata.is_null() || message.is_null() {
        return;
    }
    // SAFETY: both pointers originate from libmosquitto for this callback.
    let state = unsafe { &mut *userdata.cast::<CallbackState>() };
    // SAFETY: message is non-null for the callback duration.
    let message = unsafe { &*message };
    if message.topic.is_null()
        || message.payload_len < 0
        || usize::try_from(message.payload_len)
            .map_or(true, |length| length > state.max_payload_bytes)
        || state.messages.len() >= CALLBACK_QUEUE_CAPACITY
    {
        state.dropped_messages = state.dropped_messages.saturating_add(1);
        return;
    }
    // SAFETY: libmosquitto supplies a terminated topic string.
    let topic = unsafe { CStr::from_ptr(message.topic) }.to_bytes();
    if topic.is_empty() || topic.len() > MAX_TOPIC_BYTES {
        state.dropped_messages = state.dropped_messages.saturating_add(1);
        return;
    }
    let Ok(topic) = std::str::from_utf8(topic) else {
        state.dropped_messages = state.dropped_messages.saturating_add(1);
        return;
    };
    let length = usize::try_from(message.payload_len).unwrap_or_default();
    if length > 0 && message.payload.is_null() {
        state.dropped_messages = state.dropped_messages.saturating_add(1);
        return;
    }
    let payload = if length == 0 {
        Vec::new()
    } else {
        // SAFETY: libmosquitto guarantees payload_len bytes during the callback.
        unsafe { std::slice::from_raw_parts(message.payload.cast::<u8>(), length) }.to_vec()
    };
    state.messages.push_back(Message {
        topic: topic.to_owned(),
        payload,
        qos: message.qos,
        retained: message.retain,
    });
}

#[cfg(test)]
#[path = "mqtt_tests.rs"]
mod tests;
