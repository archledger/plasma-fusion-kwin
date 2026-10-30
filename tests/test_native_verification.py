# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import json
import unittest

from ci.native.guest import KERNEL, VGEM
from ci.native.verify import verify_envelope, verify_renderer
from tools.native_inputs import BUILDER_CONFIG, BUILDER_DIGEST


class NativeVerificationTest(unittest.TestCase):
    def setUp(self):
        self.renderer = ('uid=1000 node=/dev/dri/card0 driver=vgem\n'
                         'GL_RENDERER=llvmpipe (LLVM 22.1.8)\n'
                         'pixel[2,2]=255,0,0,255\npixel[12,2]=51,102,153,255\n')
        self.renderer += ('pixel[2,2]=255,0,0,255\npixel[12,2]=51,102,153,255\n'
                          'PREFLIGHT_PASS: completed\n')
        self.inputs = {'source': {'tree': 'a' * 40, 'sha256': 'b' * 64, 'bytes': 10}}
        oci = {'process': {'user': {'uid': 1000, 'gid': 1000, 'additionalGids': [39]},
                           'noNewPrivileges': True, 'capabilities': {},
                           'args': ['/usr/bin/python3', '-m', 'ci.native.workload', 'render']},
               'linux': {'seccomp': {'defaultAction': 'SCMP_ACT_ERRNO'},
                         'namespaces': [{'type': name} for name in ['pid', 'network', 'ipc', 'uts', 'mount', 'cgroup']],
                         'devices': [{'path': '/dev/dri/card0', 'type': 'c', 'major': 226, 'minor': 0}],
                         'resources': {'devices': [{'allow': True, 'type': 'c', 'major': 226,
                                                   'minor': 0, 'access': 'rw'}]}}}
        self.value = {'metadata': {'mode': 'render', 'status': '0', 'kernel': KERNEL, 'vgem_sha256': VGEM,
                                   'builder_config': BUILDER_CONFIG, 'builder_manifest': BUILDER_DIGEST,
                                   'container_removed': 'true', 'drm_node': '/dev/dri/card0',
                                   'drm_major': '226', 'drm_minor': '0', 'drm_gid': '39'},
                      'files': {'exit-code.txt': '0', 'last-phase.txt': 'render-complete',
                                'collector-status.txt': 'ok', 'guest-stage.log': 'ok', 'render-build.log': 'ok',
                                'render-context.txt': self.renderer, 'source.json': json.dumps(self.inputs['source']),
                                'packages.txt': 'example\texample-0:1-1.x86_64\n', 'builder-oci.json': json.dumps(oci)}}

    def test_measured_renderer_and_identity_are_required(self):
        result = verify_envelope(self.value, 'render', self.inputs, ['example-0:1-1.x86_64'])
        self.assertIn('llvmpipe', result['renderer'])
        for key, value in [('status', '1'), ('container_removed', 'false'), ('kernel', 'wrong'),
                           ('builder_config', 'wrong'), ('vgem_sha256', 'wrong')]:
            bad = copy.deepcopy(self.value)
            bad['metadata'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_envelope(bad, 'render', self.inputs, ['example-0:1-1.x86_64'])

    def test_missing_or_wrong_pixels_fail(self):
        for text in [self.renderer.replace('255,0,0,255', '0,0,0,255'),
                     self.renderer.replace('pixel[2,2]=255,0,0,255\n', '', 1),
                     self.renderer.replace('llvmpipe', 'hardware'), self.renderer + 'PREFLIGHT_FAIL: error\n']:
            with self.subTest(text=text[-40:]), self.assertRaises(ValueError):
                verify_renderer(text, '/dev/dri/card0')


if __name__ == '__main__':
    unittest.main()
