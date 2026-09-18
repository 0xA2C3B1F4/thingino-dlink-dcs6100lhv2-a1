"""Read Kbuild evidence; this is not a license or full source-delivery audit."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex

REVISION = "6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06"
PACKAGE = "wifi-rtl8188fu-" + REVISION
RECORDED_ROOT = PurePosixPath("/workspace/thingino-output/build") / PACKAGE


def regular(path):
    path = Path(path)
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise ValueError(f"symlink path component: {path}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {path.name}")
    return path.read_bytes()


def identity(path):
    data = regular(path)
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def relative(value):
    result = PurePosixPath(value).relative_to(RECORDED_ROOT)
    if ".." in result.parts or not result.parts:
        raise ValueError("unsafe recorded source path")
    return result.as_posix()


def inventory(workspace):
    driver = workspace / "thingino-output/build" / PACKAGE
    link_record = driver / ".8188fu.o.cmd"
    command = regular(link_record).decode().split(":=", 1)[1]
    arguments = shlex.split(command)
    output_index = arguments.index("-o") + 1
    objects = [relative(value) for i, value in enumerate(arguments)
               if value.endswith(".o") and i != output_index]
    if not objects or len(objects) != len(set(objects)):
        raise ValueError("empty or repeated link inputs")
    sources = []
    for obj in objects:
        path = driver / obj
        record = path.with_name("." + path.name + ".cmd")
        contents = regular(record).decode()
        source_lines = re.findall(r"^source_[^\n]* := ([^\n]+)$", contents, re.M)
        if len(source_lines) != 1:
            raise ValueError("expected one Kbuild source binding")
        source = relative(source_lines[0])
        raw = regular(driver / source)
        header_lines = raw.decode("utf-8", errors="replace").splitlines()[:80]
        markers = {marker: [i + 1 for i, line in enumerate(header_lines) if marker in line]
                   for marker in ("GNU General Public License", "BSD", "README", "Copyright")}
        sources.append({"source": source, **identity(driver / source),
                        "object": obj, "object_identity": identity(path),
                        "compile_record": record.relative_to(driver).as_posix(),
                        "compile_record_identity": identity(record),
                        "header_marker_lines": {k: v for k, v in markers.items() if v}})
    target = workspace / "thingino-output/target"
    library = target / "lib"
    if library.is_symlink():
        if library.readlink().as_posix() != "usr/lib":
            raise ValueError("unexpected target library symlink")
        library = target / "usr/lib"
    installed = list((library / "modules").rglob("8188fu.ko"))
    if len(installed) != 1:
        raise ValueError("expected one installed module")
    return {
        "schema_version": 1,
        "scope": "linked-driver-translation-units-not-full-corresponding-source",
        "license_review_status": "not-assessed",
        "firmware_release_gate_closed": False,
        "source_revision": REVISION,
        "source_repository": "https://github.com/gtxaspec/rtl8188ftv-wifi.git",
        "build_module": identity(driver / "8188fu.ko"),
        "installed_module": identity(installed[0]),
        "link_record": identity(link_record),
        "kernel_config": identity(workspace / "thingino-output/build/linux-3.10.14/.config"),
        "generated_module_source": identity(driver / "8188fu.mod.c"),
        "translation_unit_count": len(sources),
        "translation_units": sorted(sources, key=lambda x: x["source"]),
        "not_covered": ["transitive headers", "kernel corresponding source", "compiler inputs",
                        "complete per-file notice review", "redistribution permission",
                        "corresponding-source delivery archive"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.workspace), indent=2, sort_keys=True))
