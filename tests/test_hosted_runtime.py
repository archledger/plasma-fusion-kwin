# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import subprocess
import unittest
from unittest.mock import patch

from tools import probe_hosted_runtime
from tools.probe_hosted_runtime import blockers


class HostedRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            'architecture': 'x86_64', 'kvm': {'api_version': 12},
            'tools': {name: '/usr/bin/' + name for name in
                      ['qemu-system-x86_64', 'qemu-img', 'virsh', 'virt-xml-validate', 'xorriso', 'podman']},
            'python_libvirt': True, 'q35_10_2': True,
            'memory_total_bytes': 16 * 1024**3, 'free_disk_bytes': 20 * 1024**3,
        }

    def test_complete_prerequisites(self):
        self.assertEqual(blockers(self.snapshot), [])

    def test_missing_kvm_is_not_ready(self):
        self.snapshot['kvm'] = {'error': 'permission denied'}
        self.assertIn('KVM API 12 is not accessible', blockers(self.snapshot))

    def test_missing_tools_never_pass(self):
        self.snapshot['tools']['qemu-system-x86_64'] = None
        self.assertIn('missing tool: qemu-system-x86_64', blockers(self.snapshot))

    def test_missing_xml_validator_rejected(self):
        self.snapshot['tools']['virt-xml-validate'] = None
        self.assertIn('missing tool: virt-xml-validate', blockers(self.snapshot))

    def test_libvirt_import_failure_is_not_usable(self):
        failure = subprocess.CompletedProcess([], 1, '', 'No module named libvirtmod')
        with patch.object(probe_hosted_runtime.subprocess, 'run', return_value=failure):
            result = probe_hosted_runtime.probe_libvirt()
        self.assertFalse(result['usable'])
        self.assertIn('libvirtmod', result['error'])

    def test_qemu_exec_error_is_structured(self):
        with patch.object(probe_hosted_runtime.subprocess, 'run', side_effect=PermissionError('refused')):
            result = probe_hosted_runtime.probe_qemu('/unusable/qemu')
        self.assertFalse(result['q35_10_2'])
        self.assertIn('refused', result['error'])

    def test_no_silent_machine_or_tcg_fallback(self):
        self.snapshot['q35_10_2'] = False
        self.assertIn('verified pc-q35-10.2 machine is unavailable', blockers(self.snapshot))

    def test_runtime_bindings_and_resources_are_required(self):
        for key, value in [('python_libvirt', False), ('memory_total_bytes', 1024**3),
                           ('free_disk_bytes', 1024**3), ('architecture', 'aarch64')]:
            with self.subTest(key=key):
                data = copy.deepcopy(self.snapshot)
                data[key] = value
                self.assertTrue(blockers(data))


if __name__ == '__main__':
    unittest.main()
