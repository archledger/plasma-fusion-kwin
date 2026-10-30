# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.native import workload


class NativeWorkloadTest(unittest.TestCase):
    def test_renderer_failure_never_reaches_kwin_configuration(self):
        self.run_renderer_case(1, 'native')

    def test_renderer_only_does_not_start_kwin_build(self):
        self.run_renderer_case(0, 'render')

    def run_renderer_case(self, renderer_status, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = b'checked input'
            (root / 'patched-source.tar').write_bytes(source)
            (root / 'inputs.json').write_text(json.dumps({'source': {'sha256': hashlib.sha256(source).hexdigest()}}))
            commands = []

            def run(command, **kwargs):
                commands.append(command)
                kwargs['stdout'].write('test output\n')
                code = renderer_status if command[0] == str(root / 'render-probe') else 0
                return subprocess.CompletedProcess(command, code)

            with patch.object(workload, 'verify_environment', return_value='/dev/dri/card0'), \
                    patch.object(workload.subprocess, 'run', side_effect=run), \
                    patch.object(workload.subprocess, 'check_output', return_value=''), \
                    patch.dict(workload.os.environ, clear=False):
                status = workload.main(mode, root)
            self.assertEqual(status, renderer_status)
            self.assertFalse(any(command[:2] == ['cmake', '-S'] or '--build' in command for command in commands))
            expected_phase = 'renderer' if renderer_status else 'render-complete'
            self.assertEqual((root / 'results/last-phase.txt').read_text().strip(), expected_phase)

    def test_root_is_rejected_before_device_access(self):
        with patch.object(workload.os, 'geteuid', return_value=0), self.assertRaises(ValueError):
            workload.verify_environment()


if __name__ == '__main__':
    unittest.main()
