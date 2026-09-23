use std::io::{self, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::thread;
use std::time::{Duration, Instant};

use crate::BackendError;

pub(in crate::camera) fn connect_socket(
    path: &Path,
    deadline: Instant,
) -> Result<UnixStream, BackendError> {
    loop {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match UnixStream::connect(path) {
            Ok(stream) => {
                stream
                    .set_nonblocking(true)
                    .map_err(|_| BackendError::Connection)?;
                return Ok(stream);
            }
            Err(error) if retryable_unix_connect_error(&error) => {
                let budget = deadline
                    .checked_duration_since(Instant::now())
                    .ok_or(BackendError::Timeout)?;
                thread::sleep(budget.min(Duration::from_millis(20)));
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
}

pub(in crate::camera) fn retryable_unix_connect_error(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::NotFound
            | io::ErrorKind::ConnectionRefused
            | io::ErrorKind::ConnectionReset
            | io::ErrorKind::Interrupted
            | io::ErrorKind::WouldBlock
    )
}

pub(in crate::camera) fn wait_for_socket(deadline: Instant) -> Result<(), BackendError> {
    let budget = deadline
        .checked_duration_since(Instant::now())
        .ok_or(BackendError::Timeout)?;
    thread::sleep(budget.min(Duration::from_millis(1)));
    Ok(())
}

pub(in crate::camera) fn write_deadline(
    stream: &mut UnixStream,
    mut bytes: &[u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !bytes.is_empty() {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match stream.write(bytes) {
            Ok(0) => return Err(BackendError::Connection),
            Ok(count) => bytes = &bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => wait_for_socket(deadline)?,
            Err(error) if error.kind() == io::ErrorKind::TimedOut => {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
    Ok(())
}

#[cfg(test)]
pub(in crate::camera) fn read_line(
    stream: &mut UnixStream,
    deadline: Instant,
    limit: usize,
) -> Result<String, BackendError> {
    let mut bytes = Vec::new();
    while bytes.len() < limit {
        let mut byte = [0_u8; 1];
        read_exact_deadline(stream, &mut byte, deadline)?;
        if byte[0] == b'\n' {
            return String::from_utf8(bytes).map_err(|_| BackendError::Protocol);
        }
        if byte[0] == b'\r' || byte[0] == 0 {
            return Err(BackendError::Protocol);
        }
        bytes.push(byte[0]);
    }
    Err(BackendError::Protocol)
}

pub(in crate::camera) fn read_exact_deadline(
    stream: &mut UnixStream,
    mut buffer: &mut [u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !buffer.is_empty() {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match stream.read(buffer) {
            Ok(0) => return Err(BackendError::Protocol),
            Ok(count) => buffer = &mut buffer[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => wait_for_socket(deadline)?,
            Err(error) if error.kind() == io::ErrorKind::TimedOut => {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
    Ok(())
}

#[cfg(test)]
pub(in crate::camera) fn read_to_end_deadline(
    stream: &mut UnixStream,
    deadline: Instant,
    limit: usize,
) -> Result<Vec<u8>, BackendError> {
    let mut output = Vec::with_capacity(4096);
    let mut buffer = [0_u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(BackendError::Timeout);
        }
        match stream.read(&mut buffer) {
            Ok(0) => return Ok(output),
            Ok(count) => {
                if output.len() + count > limit {
                    return Err(BackendError::Protocol);
                }
                output.extend_from_slice(&buffer[..count]);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => wait_for_socket(deadline)?,
            Err(error) if error.kind() == io::ErrorKind::TimedOut => {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Connection),
        }
    }
}
