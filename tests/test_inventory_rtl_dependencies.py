import importlib.util
from pathlib import Path
import unittest
import hashlib
import os
import tempfile

spec = importlib.util.spec_from_file_location('deps', Path(__file__).resolve().parents[1] / 'scripts/inventory_rtl_dependencies.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def block(*tokens):
    return 'deps_object := \\\n' + ''.join('  ' + token + ' \\\n' for token in tokens) + '\n'


class DependencyParserTests(unittest.TestCase):
    def test_literals_and_optional_config(self):
        self.assertEqual(module.parse_dependencies(block('include/linux/types.h', '$(wildcard include/config/example.h)')),
                         [('include/linux/types.h', False), ('include/config/example.h', True)])

    def test_make_execution_rejected(self):
        for token in ('$(shell touch /result)', '$(eval example)', '$(wildcard include/config/*.h)'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                module.parse_dependencies(block(token))

    def test_shell_execution_rejected(self):
        for token in ('`id`', 'header.h;id', 'header.h|id'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                module.parse_dependencies(block(token))

    def test_optional_traversal_rejected(self):
        with self.assertRaises(ValueError):
            module.parse_dependencies(block('$(wildcard include/config/../../outside.h)'))

    def test_missing_and_duplicate_blocks_rejected(self):
        for text in ('', block('include/a.h') + block('include/b.h')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                module.parse_dependencies(text)

    def test_compiler_lexical_parent_retained_for_confined_resolution(self):
        path = '/workspace/toolchain/bin/../include/stdarg.h'
        self.assertEqual(module.parse_dependencies(block(path)), [(path, False)])

    def test_malformed_continuation_rejected(self):
        with self.assertRaises(ValueError):
            module.parse_dependencies(block('include/a.h').replace('include/a.h \\', 'include/a.h'))


class DependencyFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / 'workspace'
        self.revision = 'a' * 40
        self.driver = self.workspace / 'thingino-output/build' / ('wifi-rtl8188fu-' + self.revision)
        self.kernel = self.workspace / 'thingino-output/build/linux-3.10.14'
        self.driver.mkdir(parents=True)
        (self.kernel / 'include').mkdir(parents=True)
        (self.kernel / 'include/example.h').write_text('fixture header\n')

    def units(self, *tokens):
        raw = block(*tokens).encode()
        (self.driver / '.unit.o.cmd').write_bytes(raw)
        return {'source_revision': self.revision, 'translation_unit_count': 1,
                'translation_units': [{'source': 'unit.c', 'compile_record': '.unit.o.cmd',
                                      'compile_record_identity': {'sha256': hashlib.sha256(raw).hexdigest()}}]}

    def test_files_optional_absence_and_source_mapping(self):
        result = module.collect(self.workspace, self.units('include/example.h', '$(wildcard include/config/absent.h)'))
        self.assertEqual(result['dependency_count'], 1)
        self.assertEqual(len(result['absent_optional_config_paths']), 1)
        self.assertEqual(result['source_sets'][result['dependencies'][0]['source_set']], ['unit.c'])

    def test_missing_required_file_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing required'):
            module.collect(self.workspace, self.units('include/missing.h'))

    def test_workspace_escape_rejected(self):
        with self.assertRaisesRegex(ValueError, 'escapes workspace'):
            module.collect(self.workspace, self.units('../../../../outside.h'))

    def test_symlink_escape_rejected(self):
        (self.root / 'outside.h').write_text('outside\n')
        (self.kernel / 'include/escape.h').symlink_to(self.root / 'outside.h')
        with self.assertRaisesRegex(ValueError, 'escapes workspace'):
            module.collect(self.workspace, self.units('include/escape.h'))

    def test_internal_symlink_is_explicitly_recorded(self):
        (self.kernel / 'include/alias.h').symlink_to('example.h')
        result = module.collect(self.workspace, self.units('include/alias.h'))
        self.assertTrue(result['dependencies'][0]['path'].endswith('/example.h'))
        self.assertTrue(result['dependencies'][0]['recorded_paths'][0].endswith('/alias.h'))

    def test_changed_compile_record_rejected(self):
        units = self.units('include/example.h')
        (self.driver / '.unit.o.cmd').write_text(block('include/other.h'))
        with self.assertRaisesRegex(ValueError, 'differs'):
            module.collect(self.workspace, units)

    def test_invalid_source_identity_rejected(self):
        units = self.units('include/example.h')
        units['source_revision'] = '../escape'
        with self.assertRaisesRegex(ValueError, 'invalid source revision'):
            module.collect(self.workspace, units)


if __name__ == '__main__':
    unittest.main()
