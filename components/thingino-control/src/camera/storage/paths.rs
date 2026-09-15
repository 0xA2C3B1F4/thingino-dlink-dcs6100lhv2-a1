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
