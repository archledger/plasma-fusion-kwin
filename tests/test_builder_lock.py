# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class BuilderLockTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / 'packages.lock'
        self.script = Path(__file__).resolve().parents[1] / 'ci/builder/install-locked.sh'
        self.env = dict(os.environ)
        self.env.pop('FUSION_CI_IMAGE_BUILD', None)
        self.lock.write_text('alpha-0:1.0-1.fc44.noarch\nbeta-1:2.0-3.fc44.x86_64\n')

    def run_check(self, mode='--check'):
        return subprocess.run(['bash', str(self.script), mode, str(self.lock)],
                              env=self.env, capture_output=True, text=True,
                              check=False, timeout=10)

    def test_valid_lock_is_checkable_without_installation(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2 package pins validated', result.stdout)

    def test_comments_and_empty_lines_are_allowed(self):
        self.lock.write_text('# License header\n\nalpha-0:1.0-1.fc44.noarch\n')
        self.assertEqual(self.run_check().returncode, 0)

    def test_empty_lock_is_rejected(self):
        self.lock.write_text('# Nothing pinned\n')
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_options_urls_paths_and_invalid_architectures_are_rejected(self):
        for value in ['--nogpgcheck', 'https://example.invalid/a.rpm', '/tmp/a.rpm',
                      'name with spaces', 'alpha-0:1.0-1.fc44.aarch64', 'alpha-latest']:
            with self.subTest(value=value):
                self.lock.write_text(value + '\n')
                self.assertNotEqual(self.run_check().returncode, 0)

    def test_unsorted_or_duplicate_packages_are_rejected(self):
        for text in ['beta-0:1-1.noarch\nalpha-0:1-1.noarch\n',
                     'alpha-0:1-1.noarch\nalpha-0:1-1.noarch\n',
                     'alpha-0:1-1.noarch\nalpha-0:2-1.noarch\n']:
            with self.subTest(text=text):
                self.lock.write_text(text)
                self.assertNotEqual(self.run_check().returncode, 0)

    def test_install_refuses_without_explicit_image_build_guard(self):
        result = self.run_check('--install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('image-build guard', result.stderr)

    def test_guarded_install_still_refuses_non_root(self):
        commands = self.root / 'non-root-bin'
        commands.mkdir()
        id_command = commands / 'id'
        id_command.write_text('#!/bin/sh\necho 1000\n')
        id_command.chmod(0o755)
        marker = self.root / 'dnf-was-called'
        dnf = commands / 'dnf'
        dnf.write_text('#!/bin/sh\ntouch "$DNF_MARKER"\nexit 99\n')
        dnf.chmod(0o755)
        self.env.update(FUSION_CI_IMAGE_BUILD='1', DNF_MARKER=str(marker),
                        PATH=str(commands) + ':' + self.env['PATH'])
        result = self.run_check('--install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('image-build guard', result.stderr)
        self.assertFalse(marker.exists())

    def test_unknown_mode_does_not_install(self):
        result = self.run_check('--anything')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('usage', result.stderr.lower())

    def test_symlink_and_oversized_lock_are_rejected(self):
        target = self.root / 'real.lock'
        self.lock.rename(target)
        self.lock.symlink_to(target)
        self.assertNotEqual(self.run_check().returncode, 0)
        self.lock.unlink()
        self.lock.write_bytes(b'#' * 262145)
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_inventory_drift_stops_before_writing_image_metadata(self):
        self.lock.write_text('alpha-0:1.0-1.fc44.noarch\n')
        commands = self.root / 'bin'
        commands.mkdir()
        scripts = {
            'id': 'echo 0',
            'dnf': 'exit 0',
            'rpm': "printf 'alpha\\talpha-0:1.0-1.fc44.noarch\\nextra\\textra-0:1-1.noarch\\n'",
            'mkdir': 'echo unexpected-metadata-write >&2; exit 99',
        }
        for name, body in scripts.items():
            path = commands / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        self.env.update(FUSION_CI_IMAGE_BUILD='1', PATH=str(commands) + ':' + self.env['PATH'])
        result = self.run_check('--install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Actual package inventory differs', result.stderr)
        self.assertNotIn('unexpected-metadata-write', result.stderr)

    def test_signature_policy_overrides_each_repository(self):
        commands = self.root / 'signature-bin'
        commands.mkdir()
        args = self.root / 'dnf-args'
        for name, body in {
            'id': 'echo 0',
            'dnf': 'printf "%s\\n" "$@" > "$CAPTURE_DNF_ARGS"; exit 73',
        }.items():
            path = commands / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        self.env.update(FUSION_CI_IMAGE_BUILD='1', CAPTURE_DNF_ARGS=str(args),
                        PATH=str(commands) + ':' + self.env['PATH'])
        self.assertNotEqual(self.run_check('--install').returncode, 0)
        flags = args.read_text().splitlines()
        self.assertIn('--setopt=pkg_gpgcheck=1', flags)
        self.assertIn('--setopt=*.pkg_gpgcheck=1', flags)

    def test_prefixed_gpg_name_is_not_excluded_from_inventory(self):
        self.lock.write_text('alpha-0:1.0-1.fc44.noarch\n')
        commands = self.root / 'prefix-bin'
        commands.mkdir()
        scripts = {
            'id': 'echo 0',
            'dnf': 'exit 0',
            'rpm': (
                'case "$*" in\n'
                '  *NAME*NAME*) printf "alpha\\talpha-0:1.0-1.fc44.noarch\\n'
                'gpg-pubkey-helper\\tgpg-pubkey-helper-0:1-1.noarch\\n" ;;\n'
                '  *) printf "alpha-0:1.0-1.fc44.noarch\\n'
                'gpg-pubkey-helper-0:1-1.noarch\\n" ;;\n'
                'esac'
            ),
            'mkdir': 'echo unexpected-metadata-write >&2; exit 99',
        }
        for name, body in scripts.items():
            path = commands / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        self.env.update(FUSION_CI_IMAGE_BUILD='1', PATH=str(commands) + ':' + self.env['PATH'])
        result = self.run_check('--install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Actual package inventory differs', result.stderr)
        self.assertNotIn('unexpected-metadata-write', result.stderr)


if __name__ == '__main__':
    unittest.main()
