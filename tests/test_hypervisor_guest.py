# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from ci.hypervisor import guest_probe
from ci.hypervisor.boot_guest import parse_guest_result


class GuestPayloadTest(unittest.TestCase):
    def run_payload(self, interfaces, digest):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nonce = '1' * 32
            (root / 'nonce').write_text(nonce)
            (root / 'module').write_bytes(b'test module')
            paths = {'/etc/pf-boot-nonce': root / 'nonce', '/dev/ttyS0': root / 'serial',
                     '/test-module': root / 'module'}
            with patch.object(guest_probe, 'Path', side_effect=lambda path: paths[path]), \
                    patch.object(guest_probe.os, 'geteuid', return_value=0), \
                    patch.object(guest_probe.os, 'uname', return_value=types.SimpleNamespace(
                        release='6.19.10-300.fc44.x86_64')), \
                    patch.object(guest_probe.os, 'listdir', return_value=interfaces), \
                    patch.object(guest_probe.subprocess, 'run') as run, \
                    patch.object(guest_probe.subprocess, 'check_output', return_value='/test-module\n') as info, \
                    patch.object(guest_probe.hashlib, 'sha256', return_value=Mock(hexdigest=lambda: digest)):
                guest_probe.main()
            self.assertEqual(run.call_args_list,
                             [call(['systemctl', 'stop', 'serial-getty@ttyS0.service'], check=True),
                              call(['systemctl', 'poweroff'], check=False)])
            return (root / 'serial').read_text(), nonce, info

    def test_payload_checks_module_without_loading_it(self):
        serial, nonce, info = self.run_payload(
            ['lo'], 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8')
        self.assertTrue(parse_guest_result(serial, nonce)['success'])
        info.assert_called_once_with(['modinfo', '-F', 'filename', 'vgem'], text=True)

    def test_unexpected_guest_interface_reports_failure(self):
        serial, nonce, info = self.run_payload(['lo', 'eth0'], '0' * 64)
        with self.assertRaises(ValueError):
            parse_guest_result(serial, nonce)
        info.assert_not_called()

    def test_changed_module_reports_failure(self):
        serial, nonce, _ = self.run_payload(['lo'], '0' * 64)
        with self.assertRaises(ValueError):
            parse_guest_result(serial, nonce)

    def test_receipt_starts_on_a_new_line_after_a_console_prompt(self):
        stream = io.StringIO('fedora login: ')
        stream.seek(0, io.SEEK_END)
        nonce = '1' * 32
        receipt = {'nonce': nonce, 'uid': 0, 'success': True,
                   'kernel': '6.19.10-300.fc44.x86_64', 'vgem_available': True,
                   'vgem_sha256': 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8'}
        guest_probe.write_receipt(stream, nonce, receipt)
        self.assertTrue(parse_guest_result(stream.getvalue(), nonce)['success'])


if __name__ == '__main__':
    unittest.main()
