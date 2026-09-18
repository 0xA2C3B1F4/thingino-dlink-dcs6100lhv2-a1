"""Read literal Kbuild dependencies without evaluating Make or shell code."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def parse_dependencies(text):
    blocks = re.findall(r'^deps_[^\n]+ := \\\n((?:[^\n]+\n)+)\n', text, re.M)
    if len(blocks) != 1:
        raise ValueError('expected one nonempty dependency block')
    result = []
    for line in blocks[0].splitlines():
        if not line.rstrip().endswith('\\'):
            raise ValueError('unterminated dependency continuation')
        token = line.strip()[:-1].strip()
        wildcard = re.fullmatch(r'\$\(wildcard (include/config/[a-zA-Z0-9_./-]+\.h)\)', token)
        optional = wildcard is not None
        value = wildcard.group(1) if wildcard else token
        if not re.fullmatch(r'[a-zA-Z0-9_./+-]+', value):
            raise ValueError('unsupported dependency expression')
        path = PurePosixPath(value)
        if optional and '..' in path.parts:
            raise ValueError('dependency traversal')
        if optional and path.is_absolute():
            raise ValueError('absolute optional dependency')
        result.append((path.as_posix(), optional))
    return result


def collect(workspace, units):
    workspace = workspace.resolve(strict=True)
    if not re.fullmatch(r'[0-9a-f]{40}', units.get('source_revision', '')):
        raise ValueError('invalid source revision')
    sources = units.get('translation_units')
    if not isinstance(sources, list) or not sources or len(sources) != units.get('translation_unit_count'):
        raise ValueError('invalid translation unit count')
    names = [item['source'] for item in sources]
    if len(names) != len(set(names)):
        raise ValueError('duplicate source units')
    driver = workspace / 'thingino-output/build' / ('wifi-rtl8188fu-' + units['source_revision'])
    kernel = workspace / 'thingino-output/build/linux-3.10.14'
    files, absent = {}, set()
    for unit in units['translation_units']:
        record = (driver / unit['compile_record']).resolve(strict=True)
        if not record.is_relative_to(driver):
            raise ValueError('compile record escapes driver')
        raw = record.read_bytes()
        if hashlib.sha256(raw).hexdigest() != unit['compile_record_identity']['sha256']:
            raise ValueError('compile record differs from verified unit inventory')
        for value, optional in parse_dependencies(raw.decode()):
            recorded = PurePosixPath(value)
            if recorded.is_absolute():
                relative = recorded.relative_to('/workspace')
                path = workspace / relative
            else:
                path = kernel / value
            lexical = path.relative_to(workspace).as_posix()
            resolved = path.resolve(strict=False)
            if not resolved.is_relative_to(workspace):
                raise ValueError('dependency escapes workspace')
            if not path.exists():
                if optional and not path.is_symlink():
                    absent.add(lexical)
                    continue
                raise ValueError('missing required dependency: ' + value)
            if not resolved.is_file():
                raise ValueError('dependency is not a regular file')
            name = resolved.relative_to(workspace).as_posix()
            if name not in files:
                data = resolved.read_bytes()
                files[name] = {'path': name, 'sha256': hashlib.sha256(data).hexdigest(),
                               'size': len(data), 'recorded_paths': set(), 'used_by': set()}
            files[name]['recorded_paths'].add(lexical)
            files[name]['used_by'].add(unit['source'])
    source_sets = sorted(set(tuple(sorted(item['used_by'])) for item in files.values()))
    source_indices = {names: i for i, names in enumerate(source_sets)}
    entries = []
    for name in sorted(files):
        item = files[name]
        item['recorded_paths'] = sorted(item['recorded_paths'])
        item['source_set'] = source_indices[tuple(sorted(item.pop('used_by')))]
        entries.append(item)
    return {'schema_version': 1, 'scope': 'recorded-kbuild-dependencies-only',
            'source_revision': units['source_revision'], 'translation_unit_count': len(units['translation_units']),
            'dependency_count': len(entries), 'dependencies': entries,
            'source_sets': [list(names) for names in source_sets],
            'absent_optional_config_paths': sorted(absent),
            'firmware_release_gate_closed': False,
            'not_covered': ['complete per-file licensing', 'source delivery archive',
                            'unrecorded generator and compiler inputs']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--units', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.workspace, json.loads(args.units.read_text())), indent=2, sort_keys=True))
