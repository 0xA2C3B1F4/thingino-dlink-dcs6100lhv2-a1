//! Media path validation and bounded directory listings.

use super::*;

pub(in crate::camera) fn validated_absolute_path(value: &str) -> Result<PathBuf, BackendError> {
    let path = PathBuf::from(value);
    if value.len() > 512 || !path.is_absolute() {
        return Err(BackendError::Protocol);
    }
    if path.components().any(|component| {
        !matches!(
            component,
            std::path::Component::RootDir | std::path::Component::Normal(_)
        )
    }) {
        return Err(BackendError::Protocol);
    }
    Ok(path)
}

pub(in crate::camera) fn path_is_within_roots(path: &Path, roots: &[PathBuf]) -> bool {
    roots.iter().any(|root| {
        fs::canonicalize(root)
            .map(|root| path.starts_with(root))
            .unwrap_or_else(|_| path.starts_with(root))
    })
}

pub(in crate::camera) fn canonical_media_path(
    value: &str,
    roots: &[PathBuf],
) -> Result<PathBuf, BackendError> {
    let requested = validated_absolute_path(value)?;
    let metadata = requested
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if metadata.file_type().is_symlink() {
        return Err(BackendError::Protocol);
    }
    let canonical = fs::canonicalize(&requested).map_err(|_| BackendError::Unavailable)?;
    if !path_is_within_roots(&canonical, roots) {
        return Err(BackendError::Protocol);
    }
    Ok(canonical)
}

pub(in crate::camera) fn file_directory(
    value: &str,
    roots: &[PathBuf],
) -> Result<BackendResponse, BackendError> {
    if value == "/" {
        let mut entries = Vec::new();
        for root in roots {
            let Ok(path) = fs::canonicalize(root) else {
                continue;
            };
            let Ok(metadata) = path.symlink_metadata() else {
                continue;
            };
            if !metadata.is_dir() || metadata.file_type().is_symlink() {
                continue;
            }
            let name = path
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_else(|| path.to_string_lossy().into_owned());
            entries.push(object([
                ("name", Value::String(name)),
                ("path", Value::String(path.to_string_lossy().into_owned())),
                ("size", Value::String("-".to_owned())),
                (
                    "perm",
                    Value::String(format!("{:04o}", metadata.permissions().mode() & 0o7777)),
                ),
                ("time", Value::String(metadata.mtime().to_string())),
                ("is_dir", Value::Bool(true)),
                ("is_link", Value::Bool(false)),
                ("link_target", Value::String(String::new())),
                ("deletable", Value::Bool(false)),
            ]));
        }
        return json_response(object([
            ("directory", Value::String("/".to_owned())),
            ("parent", Value::String("/".to_owned())),
            (
                "breadcrumbs",
                Value::Array(vec![object([
                    ("label", Value::String("Home".to_owned())),
                    ("path", Value::String("/".to_owned())),
                ])]),
            ),
            ("entries", Value::Array(entries)),
        ]));
    }
    let directory = canonical_media_path(value, roots)?;
    if !directory.is_dir() {
        return Err(BackendError::Protocol);
    }
    let mut entries = Vec::new();
    for result in fs::read_dir(&directory).map_err(|_| BackendError::Unavailable)? {
        let entry = result.map_err(|_| BackendError::Unavailable)?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.contains(['\0', '\r', '\n']) {
            continue;
        }
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path).map_err(|_| BackendError::Unavailable)?;
        let file_type = metadata.file_type();
        let link_target = if file_type.is_symlink() {
            fs::read_link(&path)
                .ok()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default()
        } else {
            String::new()
        };
        entries.push((
            file_type.is_dir(),
            name.clone(),
            object([
                ("name", Value::String(name)),
                ("path", Value::String(path.to_string_lossy().into_owned())),
                (
                    "size",
                    Value::String(if file_type.is_dir() {
                        "-".to_owned()
                    } else {
                        metadata.len().to_string()
                    }),
                ),
                (
                    "perm",
                    Value::String(format!("{:04o}", metadata.permissions().mode() & 0o7777)),
                ),
                ("time", Value::String(metadata.mtime().to_string())),
                ("is_dir", Value::Bool(file_type.is_dir())),
                ("is_link", Value::Bool(file_type.is_symlink())),
                ("link_target", Value::String(link_target)),
                ("deletable", Value::Bool(path_is_within_roots(&path, roots))),
            ]),
        ));
        if entries.len() >= 512 {
            break;
        }
    }
    entries.sort_by(|left, right| right.0.cmp(&left.0).then(left.1.cmp(&right.1)));
    let parent = directory
        .parent()
        .unwrap_or(&directory)
        .to_string_lossy()
        .into_owned();
    let mut breadcrumbs = vec![object([
        ("label", Value::String("Home".to_owned())),
        ("path", Value::String("/".to_owned())),
    ])];
    let mut accumulated = PathBuf::from("/");
    for component in directory.components() {
        if let std::path::Component::Normal(part) = component {
            accumulated.push(part);
            breadcrumbs.push(object([
                ("label", Value::String(part.to_string_lossy().into_owned())),
                (
                    "path",
                    Value::String(accumulated.to_string_lossy().into_owned()),
                ),
            ]));
        }
    }
    json_response(object([
        (
            "directory",
            Value::String(directory.to_string_lossy().into_owned()),
        ),
        ("parent", Value::String(parent)),
        ("breadcrumbs", Value::Array(breadcrumbs)),
        (
            "entries",
            Value::Array(entries.into_iter().map(|(_, _, value)| value).collect()),
        ),
    ]))
}

pub(in crate::camera) fn safe_storage_component(value: &str) -> bool {
    !value.is_empty()
        && safe_path_fragment(value)
        && !value.contains(['\0', '\r', '\n'])
        && !Path::new(value).is_absolute()
}

pub(in crate::camera) fn safe_mount_path(value: &str) -> bool {
    validated_absolute_path(value)
        .map(|path| path.starts_with("/mnt") || path.starts_with("/media"))
        .unwrap_or(false)
}

pub(in crate::camera) fn directory_listing(
    path: &Path,
    depth: usize,
    limit: usize,
) -> Result<String, BackendError> {
    fn visit(
        root: &Path,
        path: &Path,
        depth: usize,
        output: &mut String,
        limit: usize,
    ) -> Result<(), BackendError> {
        if depth == 0 || output.len() >= limit {
            return Ok(());
        }
        let mut entries = fs::read_dir(path)
            .map_err(|_| BackendError::Unavailable)?
            .filter_map(Result::ok)
            .collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let metadata = entry
                .path()
                .symlink_metadata()
                .map_err(|_| BackendError::Unavailable)?;
            let relative = entry
                .path()
                .strip_prefix(root)
                .unwrap_or(&entry.path())
                .to_owned();
            let line = format!(
                "{}\t{}\t{}\n",
                if metadata.is_dir() {
                    "d"
                } else if metadata.file_type().is_symlink() {
                    "l"
                } else {
                    "f"
                },
                metadata.len(),
                relative.display()
            );
            if output.len().saturating_add(line.len()) > limit {
                output.push_str("... listing truncated ...\n");
                return Ok(());
            }
            output.push_str(&line);
            if metadata.is_dir() {
                visit(root, &entry.path(), depth - 1, output, limit)?;
            }
        }
        Ok(())
    }

    let mut output = String::new();
    visit(path, path, depth, &mut output, limit)?;
    Ok(output)
}
