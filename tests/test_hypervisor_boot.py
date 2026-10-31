# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import json
import tempfile
import unittest
from pathlib import Path

from ci.hypervisor.boot_guest import (
    parse_guest_result,
    qemu_command,
    read_bounded,
    seed_userdata,
    validate_confinement,
    validate_image_info,
)


class HypervisorBootTest(unittest.TestCase):
    def test_fixed_kvm_command(self):
        command = qemu_command(Path('/work'))
        self.assertEqual(command[command.index('-accel') + 1], 'kvm')
        self.assertEqual(command[command.index('-machine') + 1], 'pc-q35-10.2')
        self.assertIn('-nodefaults', command)
        self.assertEqual(command[command.index('-nic') + 1], 'none')
        self.assertEqual(command[command.index('-monitor') + 1], 'none')
        self.assertNotIn('tcg', command)
        self.assertFalse(any('vfio' in part or 'virtfs' in part or 'virgl' in part for part in command))

    def test_base_image_has_no_backing_or_external_format(self):
        validate_image_info({'format': 'qcow2', 'virtual-size': 5368709120})
        for data in [{'format': 'raw', 'virtual-size': 5368709120},
                     {'format': 'qcow2', 'virtual-size': 1},
                     {'format': 'qcow2', 'virtual-size': 5368709120, 'backing-filename': 'other'}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_image_info(data)

    def test_only_matching_guest_receipt_is_accepted(self):
        result = {'nonce': 'abc', 'success': True, 'kernel': '6.19.10-300.fc44.x86_64',
                  'uid': 0, 'vgem_available': True,
                  'vgem_sha256': 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8'}
        text = 'boot noise\nPF_BOOT_BEGIN abc\n' + json.dumps(result) + '\nPF_BOOT_END abc\n'
        self.assertEqual(parse_guest_result(text, 'abc'), result)
        for bad in [text.replace('abc', 'wrong'), text + text, text.replace('true', 'false')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_guest_result(bad, 'abc')

    def test_receipt_framing_and_types_are_strict(self):
        receipt = {'nonce': 'abc', 'success': True, 'kernel': '6.19.10-300.fc44.x86_64',
                   'uid': 0, 'vgem_available': True,
                   'vgem_sha256': 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8'}
        good = 'PF_BOOT_BEGIN abc\n' + json.dumps(receipt) + '\nPF_BOOT_END abc\n'
        invalid = [
            'PF_BOOT_END abc\nPF_BOOT_BEGIN abc\n' + json.dumps(receipt),
            good.replace('PF_BOOT_END abc\n', ''),
            good.replace('"uid": 0', '"uid": false'),
            good.replace('"success": true', '"success": 1'),
            good.replace('PF_BOOT_BEGIN', 'noise PF_BOOT_BEGIN'),
            good.replace(json.dumps(receipt), '[]'),
        ]
        for serial in invalid:
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                parse_guest_result(serial, 'abc')

    def test_numeric_nonce_remains_a_yaml_string(self):
        nonce = '11111111111141118111111111111111'
        userdata = seed_userdata(nonce)
        self.assertIn('    content: "' + nonce + '"\n', userdata)
        self.assertIn('runcmd:\n  - [python3, /usr/local/sbin/pf-boot-probe.py]', userdata)

    def test_only_enforcing_expected_confinement_is_accepted(self):
        status = 'NoNewPrivs:\t1\nCapEff:\t0000000000000000\nSeccomp:\t2\n'
        validate_confinement(status, 'docker-default (enforce)')
        for changed, profile in [
            (status, 'docker-default (complain)'),
            (status, 'docker-default-other (enforce)'),
            (status.replace('NoNewPrivs:\t1', 'NoNewPrivs:\t0'), 'docker-default (enforce)'),
            (status.replace('0000000000000000', '0000000000000001'), 'docker-default (enforce)'),
            (status.replace('Seccomp:\t2', 'Seccomp:\t0'), 'docker-default (enforce)'),
        ]:
            with self.subTest(profile=profile, status=changed), self.assertRaises(ValueError):
                validate_confinement(changed, profile)

    def test_logs_reject_symlinks_nonregular_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'log'
            log.write_text('12345')
            self.assertEqual(read_bounded(log, 5), '12345')
            with self.assertRaises(ValueError):
                read_bounded(log, 4)
            link = root / 'link'
            link.symlink_to(log)
            with self.assertRaises(OSError):
                read_bounded(link, 5)
            with self.assertRaises(ValueError):
                read_bounded(root, 5)


if __name__ == '__main__':
    unittest.main()
