# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class HypervisorPublisherTest(unittest.TestCase):
    def test_anonymous_verification_rejects_wrong_inventory(self):
        repo = Path(__file__).resolve().parents[1]
        workflow = (repo / '.github/workflows/publish-hypervisor.yml').read_text()
        block = workflow.split('      - name: Pull anonymously on a fresh runner and verify inventory\n', 1)[1]
        script = textwrap.dedent(block.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / 'docker'
            docker.write_text('''#!/bin/bash
set -eu
[[ "$DOCKER_CONFIG" == "$RUNNER_TEMP/anonymous-docker" ]]
grep -qx '{"auths":{}}' "$DOCKER_CONFIG/config.json"
if [[ "$1" == image ]]; then
  if [[ "$*" == *org.opencontainers.image.revision* ]]; then echo "$GITHUB_SHA";
  else echo "$IMAGE_REFERENCE"; fi
elif [[ "$1" == run && "$*" == *rpm* ]]; then
  if [[ "$WRONG_INVENTORY" == 1 ]]; then echo 'wrong package';
  else awk '!/^#/ && NF {print "pkg\\t" $0}' ci/hypervisor/packages.lock; fi
fi
''')
            docker.chmod(0o755)
            env = dict(os.environ, PATH=directory + ':' + os.environ['PATH'],
                       RUNNER_TEMP=directory, GITHUB_SHA='b' * 40,
                       IMAGE_REFERENCE='ghcr.io/archledger/plasma-fusion-kwin-hypervisor@sha256:' + 'a' * 64)
            for wrong in ['0', '1']:
                result = subprocess.run(['bash', '-c', script], cwd=repo,
                                        env=dict(env, WRONG_INVENTORY=wrong),
                                        capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode == 0, wrong == '0', result.stderr)

    def test_tag_and_revision_gate_before_checks(self):
        repo = Path(__file__).resolve().parents[1]
        workflow = (repo / '.github/workflows/publish-hypervisor.yml').read_text()
        block = workflow.split('      - name: Validate publishing tag and inputs\n', 1)[1]
        script = textwrap.dedent(block.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'checks-invoked'
            python = root / 'python3'
            python.write_text('#!/bin/sh\nprintf invoked > "$PUBLISHER_TEST_MARKER"\n')
            python.chmod(0o755)
            env = dict(os.environ, PATH=directory + ':' + os.environ['PATH'],
                       RUNNER_TEMP=directory, PUBLISHER_TEST_MARKER=str(marker))
            for tag, sha, allowed in [
                ('ci-hypervisor-20261030.1', head, True),
                ('ci-hypervisor-20261030.1', '0' * 40, False),
                ('main', head, False),
                ('ci-builder-20261030.1', head, False),
                ('ci-hypervisor-20261030.0', head, False),
                ('ci-hypervisor-20261030.1;false', head, False),
            ]:
                with self.subTest(tag=tag, sha=sha):
                    marker.unlink(missing_ok=True)
                    result = subprocess.run(['bash', '-c', script], cwd=repo,
                                            env=dict(env, TAG=tag, GITHUB_SHA=sha),
                                            capture_output=True, text=True, timeout=20, check=False)
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)
                    self.assertEqual(marker.exists(), allowed)


if __name__ == '__main__':
    unittest.main()
