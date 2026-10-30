# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class PublisherGuardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = Path(__file__).resolve().parents[1]
        self.marker = self.root / 'tests-invoked'
        tools = self.root / 'bin'
        tools.mkdir()
        python = tools / 'python3'
        python.write_text('#!/bin/sh\nprintf invoked > "$PUBLISHER_TEST_MARKER"\n')
        python.chmod(0o755)
        workflow = (self.repo / '.github/workflows/publish-builder.yml').read_text()
        block = workflow.split('      - name: Validate the explicit publishing tag and lock\n', 1)[1]
        self.script = textwrap.dedent(block.split('        run: |\n', 1)[1]
                                      .split('\n      - name:', 1)[0])
        self.head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo,
                                            text=True).strip()
        self.env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                        PUBLISHER_TEST_MARKER=str(self.marker), RUNNER_TEMP=str(self.root))

    def run_guard(self, tag, sha=None):
        return subprocess.run(['bash', '-c', self.script], cwd=self.repo,
                              env=dict(self.env, TAG=tag, GITHUB_SHA=sha or self.head),
                              capture_output=True, text=True, timeout=20, check=False)

    def test_valid_publishing_tag_reaches_checks(self):
        result = self.run_guard('ci-builder-20261030.1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.is_file())

    def test_malformed_tag_stops_before_further_commands(self):
        for tag in ['main', 'v1.0', 'ci-builder-20261030.0', 'ci-builder-20261030.1;false']:
            with self.subTest(tag=tag):
                self.assertNotEqual(self.run_guard(tag).returncode, 0)
                self.assertFalse(self.marker.exists())

    def test_mismatched_revision_stops_before_further_commands(self):
        self.assertNotEqual(self.run_guard('ci-builder-20261030.1', '0' * 40).returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
