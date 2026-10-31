# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from ci.native.results import CASES, SUITES, verify_tests


class NativeResultTest(unittest.TestCase):
    def setUp(self):
        root = ET.Element('testsuite', tests='3', failures='0', skipped='0', disabled='0')
        for name in SUITES:
            ET.SubElement(root, 'testcase', name=name, status='run')
        self.junit = ET.tostring(root, encoding='unicode')
        self.registry = {'tests': [
            {'name': name,
             'command': ['/usr/bin/dbus-run-session', '/tmp/build/bin/' + name.removeprefix('kwin-')],
             'properties': [{'name': 'WORKING_DIRECTORY', 'value': '/tmp/build/autotests/integration'}]}
            for name in SUITES]}
        sections = []
        for suite, cases in CASES.items():
            sections.append('********* Start testing of ' + suite + ' *********')
            sections.extend('PASS   : ' + suite + '::' + case for case in cases)
            if 'OffscreenQuickView' in suite:
                sections.append('QDEBUG : ' + suite + '::testComposition() qt.rhi.general: '
                                'OpenGL VENDOR: Mesa RENDERER: llvmpipe (LLVM 22.1.8, 256 bits) '
                                'VERSION: 4.6 (Core Profile) Mesa 26.2.5')
            sections.append(f'Totals: {len(cases)} passed, 0 failed, 0 skipped, 0 blacklisted')
            sections.append('********* Finished testing of ' + suite + ' *********')
        self.log = '\n'.join(sections)

    def verify(self, junit=None, log=None, registry=None):
        return verify_tests(junit or self.junit, log or self.log,
                            json.dumps(registry or self.registry))

    def test_exact_suite_and_case_inventory_passes(self):
        result = self.verify()
        self.assertEqual(result['qt_test_passed'], 43)
        self.assertEqual(result['suites'], 3)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['skipped'], 0)

    def test_independent_pinned_case_fixture_passes(self):
        log = (Path(__file__).parent / 'fixtures/native-v675-passed.log').read_text()
        self.assertEqual(self.verify(log=log)['qt_test_passed'], 43)

    def test_contradictory_nonpass_records_are_rejected(self):
        for outcome in ['FAIL!', 'SKIP', 'XFAIL', 'XPASS', 'BFAIL', 'BPASS', 'BXFAIL', 'BXPASS']:
            changed = self.log + '\n' + outcome + ' : KWin::OffscreenQuickViewTest::testComposition()\n'
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                self.verify(log=changed)

    def test_missing_repeated_or_disabled_suite_fails(self):
        for tests in [self.registry['tests'][:2], self.registry['tests'] * 2,
                      self.registry['tests'] + [{'name': 'other'}]]:
            with self.subTest(tests=tests), self.assertRaises(ValueError):
                self.verify(registry={'tests': tests})
        registry = copy.deepcopy(self.registry)
        registry['tests'][0]['properties'] = [{'name': 'DISABLED', 'value': True}]
        with self.assertRaises(ValueError):
            self.verify(registry=registry)

    def test_junit_fail_skip_and_not_run_are_rejected(self):
        for old, new in [('failures="0"', 'failures="1"'), ('skipped="0"', 'skipped="1"'),
                         ('status="run"', 'status="notrun"')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                self.verify(junit=self.junit.replace(old, new, 1))
        root = ET.fromstring(self.junit)
        ET.SubElement(root[0], 'skipped')
        with self.assertRaises(ValueError):
            self.verify(junit=ET.tostring(root, encoding='unicode'))

    def test_test_command_and_working_directory_are_preserved(self):
        for key, value in [('command', ['/tmp/build/bin/testInternalWindow']), ('properties', [])]:
            registry = copy.deepcopy(self.registry)
            registry['tests'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(registry=registry)

    def test_direct_cases_and_data_rows_cannot_be_dropped_or_duplicated(self):
        line = 'PASS   : KWin::OffscreenQuickViewTest::testComposition()'
        for changed in [self.log.replace(line + '\n', ''), self.log.replace(line, line + '\n' + line),
                        self.log.replace('testOpacity(fractional)', 'testOpacity(other)'),
                        self.log.replace('0 skipped', '1 skipped'),
                        self.log.replace('0 blacklisted', '1 blacklisted')]:
            with self.subTest(changed=changed[-80:]), self.assertRaises(ValueError):
                self.verify(log=changed)

    def test_renderer_must_come_from_the_offscreen_suite(self):
        changed = self.log.replace('RENDERER: llvmpipe', 'RENDERER: other')
        changed += '\nRENDERER: llvmpipe (unrelated log)\n'
        with self.assertRaises(ValueError):
            self.verify(log=changed)

    def test_repeated_qt_suite_and_entity_xml_are_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(log=self.log + '\n' + self.log)
        with self.assertRaises(ValueError):
            self.verify(junit='<!DOCTYPE testsuite []>' + self.junit)

    def test_malformed_registry_and_xml_fail_closed(self):
        for registry in ['[]', '{"tests": [null, null, null]}', '{"tests": [1, 2, 3]}',
                         '{"tests": [], "tests": []}']:
            with self.subTest(registry=registry), self.assertRaises(ValueError):
                verify_tests(self.junit, self.log, registry)
        with self.assertRaises(ValueError):
            verify_tests('<broken', self.log, json.dumps(self.registry))


if __name__ == '__main__':
    unittest.main()
