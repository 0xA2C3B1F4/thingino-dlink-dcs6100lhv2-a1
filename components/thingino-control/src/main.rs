use std::env;
use std::fs::{self, File};
use std::io::{self, Read};
use std::net::{SocketAddr, TcpListener};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use thingino_control::{
    Backend, CameraPaths, HttpBackend, PrudyntBackend, WebAuth, WebAuthPaths, WhipProxy,
    control_token_from_bytes, serve_with_web_auth_and_whip,
};

#[path = "../storage-worker/mod.rs"]
mod storage_worker;

struct Options {
    listen: SocketAddr,
    backend: Option<SocketAddr>,
    camera: bool,
    token_file: PathBuf,
    whip_backend: Option<SocketAddr>,
}

fn parse_options() -> Result<Options, String> {
    let mut listen = None;
    let mut backend = None;
    let mut camera = false;
    let mut token_file = None;
    let mut whip_backend = None;
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--listen" => {
                let value = arguments.next().ok_or("--listen requires an address")?;
                listen = Some(value.parse().map_err(|_| "invalid --listen address")?);
            }
            "--backend" => {
                let value = arguments.next().ok_or("--backend requires an address")?;
                backend = Some(value.parse().map_err(|_| "invalid --backend address")?);
            }
            "--camera" => camera = true,
            "--token-file" => {
                token_file = Some(PathBuf::from(
                    arguments.next().ok_or("--token-file requires a path")?,
                ));
            }
            "--whip-backend" => {
                let value = arguments
                    .next()
                    .ok_or("--whip-backend requires an address")?;
                let address: SocketAddr = value
                    .parse()
                    .map_err(|_| "invalid --whip-backend address")?;
                if !address.ip().is_loopback() {
                    return Err("--whip-backend must use a loopback address".to_owned());
                }
                whip_backend = Some(address);
            }
            "--help" | "-h" => {
                return Err(
                    "usage: thingino-controld --listen ADDR (--camera | --backend ADDR) --token-file PATH [--whip-backend LOOPBACK_ADDR]".to_owned(),
                );
            }
            _ => return Err(format!("unknown argument: {argument}")),
        }
    }
    if camera == backend.is_some() {
        return Err("exactly one of --camera or --backend is required".to_owned());
    }
    let listen: SocketAddr = listen.ok_or("--listen is required")?;
    if camera && !listen.ip().is_loopback() {
        return Err("--camera listener must use a loopback address".to_owned());
    }
    Ok(Options {
        listen,
        backend,
        camera,
        token_file: token_file.ok_or("--token-file is required")?,
        whip_backend,
    })
}

fn load_token(path: &PathBuf) -> io::Result<Vec<u8>> {
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "token input must not be a symbolic link",
        ));
    }
    let file = File::open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 65536 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "token input must contain 1 to 65536 bytes",
        ));
    }
    let mut token = Vec::with_capacity(metadata.len() as usize);
    file.take(65537).read_to_end(&mut token)?;
    if token.len() > 65536 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "token input must contain 1 to 65536 bytes",
        ));
    }
    if token.first() == Some(&b'{') {
        return control_token_from_bytes(&token).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "Thingino config lacks a valid Control token",
            )
        });
    }
    while token
        .last()
        .is_some_and(|byte| matches!(byte, b'\n' | b'\r'))
    {
        token.pop();
    }
    if token.is_empty() || token.len() > 4096 || token.iter().any(|byte| byte.is_ascii_whitespace())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "raw token must contain 1 to 4096 non-whitespace bytes",
        ));
    }
    Ok(token)
}

fn run() -> Result<(), String> {
    if env::args().nth(1).as_deref() == Some("--storage-worker") {
        return storage_worker::run_default();
    }
    let options = parse_options()?;
    let token = load_token(&options.token_file).map_err(|error| error.to_string())?;
    if options.camera && (token.len() != 64 || !token.iter().all(|byte| byte.is_ascii_hexdigit())) {
        return Err("camera token must contain exactly 64 hexadecimal characters".to_owned());
    }
    let listener = TcpListener::bind(options.listen).map_err(|error| error.to_string())?;
    let address = listener.local_addr().map_err(|error| error.to_string())?;
    println!("listening on {address}");
    let backend: Arc<dyn Backend> = if options.camera {
        let backend = Arc::new(PrudyntBackend::new(CameraPaths::default()));
        backend.start_maintenance();
        backend.start_ha();
        backend
    } else {
        Arc::new(HttpBackend::new(
            options.backend.expect("validated fixture backend"),
        ))
    };
    let web_auth = options
        .camera
        .then(|| WebAuth::new(WebAuthPaths::default()));
    let whip = options.whip_backend.map(WhipProxy::new);
    serve_with_web_auth_and_whip(
        listener,
        backend,
        token,
        web_auth,
        whip,
        Arc::new(AtomicBool::new(false)),
    )
    .map_err(|error| error.to_string())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("thingino-controld: {message}");
            ExitCode::FAILURE
        }
    }
}
