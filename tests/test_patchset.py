# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import patchset


class PatchsetTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.source = self.root / 'source'
        (self.project / 'patches').mkdir(parents=True)
        self.source.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        self.git('init', '-q')
        (self.source / 'value.txt').write_text('before\n')
        self.git('add', 'value.txt')
        self.git('commit', '-qm', 'Fixture base')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.metadata = {
            'schema_version': 1,
            'upstream': {'repository': patchset.UPSTREAM, 'release': 'v6.7.5',
                         'commit': self.commit},
            'expected_tree': self.tree_for('after'),
            'patches': [self.add_patch('one', 'before', 'after')],
        }

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.source, env=self.env, text=True)

    def tree_for(self, value):
        path = self.source / 'value.txt'
        original = path.read_text()
        path.write_text(value + '\n')
        self.git('add', 'value.txt')
        tree = self.git('write-tree').strip()
        path.write_text(original)
        self.git('add', 'value.txt')
        return tree

    def add_patch(self, name, before, after):
        content = (f'diff --git a/value.txt b/value.txt\n--- a/value.txt\n+++ b/value.txt\n'
                   f'@@ -1 +1 @@\n-{before}\n+{after}\n').encode()
        filename = f'patches/{name}.patch'
        (self.project / filename).write_bytes(content)
        return {
            'id': name, 'file': filename, 'sha256': hashlib.sha256(content).hexdigest(),
            'paths': ['value.txt'],
            'provenance': {
                'base_repository': patchset.UPSTREAM, 'qualification_commit': 'a' * 40,
                'qualification_commit_published': False,
                'public_submission_commit': 'https://invent.kde.org/archledger/kwin/-/commit/' + 'b' * 40,
                'upstream_merge_request': 'https://invent.kde.org/plasma/kwin/-/merge_requests/10275',
            },
        }

    def load(self):
        (self.project / 'patchset.json').write_text(json.dumps(self.metadata))
        return patchset.load_manifest(self.project)

    def test_valid_manifest_and_patch(self):
        result = self.load()
        self.assertEqual(result.commit, self.commit)
        self.assertEqual(result.patches[0].paths, ('value.txt',))

    def test_schema_version_is_exact_integer(self):
        for value in [True, 0, 2, '1']:
            with self.subTest(value=value):
                self.metadata['schema_version'] = value
                with self.assertRaises(patchset.PatchsetError):
                    self.load()

    def test_wrong_repository_or_revision(self):
        for key, value in [('repository', 'https://example.invalid/kwin'), ('commit', '--help')]:
            with self.subTest(key=key):
                original = self.metadata['upstream'][key]
                self.metadata['upstream'][key] = value
                with self.assertRaises(patchset.PatchsetError):
                    self.load()
                self.metadata['upstream'][key] = original

    def test_missing_unknown_and_duplicate_keys(self):
        for edit in ['missing', 'unknown']:
            with self.subTest(edit=edit):
                original = copy.deepcopy(self.metadata)
                if edit == 'missing':
                    del self.metadata['expected_tree']
                else:
                    self.metadata['expected_tre'] = self.metadata['expected_tree']
                with self.assertRaises(patchset.PatchsetError):
                    self.load()
                self.metadata = original
        (self.project / 'patchset.json').write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(patchset.PatchsetError):
            patchset.load_manifest(self.project)

    def test_missing_or_changed_patch(self):
        file = self.project / self.metadata['patches'][0]['file']
        file.write_text('changed')
        with self.assertRaises(patchset.PatchsetError):
            self.load()
        file.unlink()
        with self.assertRaises(patchset.PatchsetError):
            self.load()

    def test_unsafe_patch_paths(self):
        for value in ['../one.patch', '/tmp/one.patch', 'patches/../one.patch',
                      'patches\\one.patch', 'patches/./one.patch', 'patches/one\n.patch']:
            with self.subTest(value=value):
                self.metadata['patches'][0]['file'] = value
                with self.assertRaises(patchset.PatchsetError):
                    self.load()

    def test_patch_symlink_is_rejected(self):
        path = self.project / self.metadata['patches'][0]['file']
        content = path.read_bytes()
        path.unlink()
        outside = self.root / 'outside.patch'
        outside.write_bytes(content)
        path.symlink_to(outside)
        with self.assertRaises(patchset.PatchsetError):
            self.load()

    def test_symlink_parent_is_rejected(self):
        outside = self.root / 'outside'
        (self.project / 'patches').rename(outside)
        (self.project / 'patches').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(patchset.PatchsetError):
            self.load()

    def test_duplicate_patch_identity_or_path(self):
        original = copy.deepcopy(self.metadata['patches'][0])
        for field in ['id', 'file']:
            with self.subTest(field=field):
                second = self.add_patch('two', 'after', 'last')
                second[field] = original[field]
                self.metadata['patches'] = [original, second]
                with self.assertRaises(patchset.PatchsetError):
                    self.load()

    def test_unsafe_or_duplicate_target_paths(self):
        for paths in [['.'], ['../outside'], ['.git/config'], ['/tmp/x'],
                      ['value.txt', 'value.txt']]:
            with self.subTest(paths=paths):
                self.metadata['patches'][0]['paths'] = paths
                with self.assertRaises(patchset.PatchsetError):
                    self.load()

    def test_invalid_provenance_types(self):
        self.metadata['patches'][0]['provenance']['qualification_commit_published'] = 'false'
        with self.assertRaises(patchset.PatchsetError):
            self.load()

    def test_duplicate_patch_content_is_rejected(self):
        self.metadata['patches'].append(self.add_patch('two', 'before', 'after'))
        with self.assertRaisesRegex(patchset.PatchsetError, 'duplicate'):
            self.load()

    def test_empty_patch_list_or_invalid_digest_is_rejected(self):
        original = copy.deepcopy(self.metadata)
        self.metadata['patches'] = []
        with self.assertRaises(patchset.PatchsetError):
            self.load()
        self.metadata = original
        for value in ['a' * 63, 'A' * 64, 123, None]:
            with self.subTest(value=value):
                self.metadata['patches'][0]['sha256'] = value
                with self.assertRaises(patchset.PatchsetError):
                    self.load()

    def test_apply_checks_whole_result_tree(self):
        result = patchset.apply_patchset(self.source, self.load())
        self.assertEqual((self.source / 'value.txt').read_text(), 'after\n')
        self.assertEqual(result['result_tree'], self.metadata['expected_tree'])
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.commit)

    def test_wrong_base_is_rejected_before_application(self):
        self.metadata['upstream']['commit'] = 'f' * 40
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, self.load())
        self.assertEqual((self.source / 'value.txt').read_text(), 'before\n')

    def test_dirty_tracked_or_untracked_checkout_is_rejected(self):
        manifest = self.load()
        (self.source / 'value.txt').write_text('dirty\n')
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, manifest)
        (self.source / 'value.txt').write_text('before\n')
        (self.source / 'extra').write_text('untracked')
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, manifest)

    def test_ignored_files_also_make_checkout_dirty(self):
        (self.source / '.git/info/exclude').write_text('ignored\n')
        (self.source / 'ignored').write_text('unexpected build input')
        with self.assertRaisesRegex(patchset.PatchsetError, 'not clean'):
            patchset.apply_patchset(self.source, self.load())

    def test_wrong_scope_is_rejected_before_application(self):
        self.metadata['patches'][0]['paths'] = ['another.txt']
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, self.load())
        self.assertEqual((self.source / 'value.txt').read_text(), 'before\n')

    def test_wrong_result_tree_is_failure(self):
        self.metadata['expected_tree'] = 'f' * 40
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, self.load())

    def test_added_source_whitespace_is_rejected(self):
        self.metadata['patches'] = [self.add_patch('whitespace', 'before', 'after ')]
        self.metadata['expected_tree'] = self.tree_for('after ')
        with self.assertRaisesRegex(patchset.PatchsetError, 'whitespace'):
            patchset.apply_patchset(self.source, self.load())

    def test_cannot_reapply_series(self):
        manifest = self.load()
        patchset.apply_patchset(self.source, manifest)
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, manifest)

    def test_series_applies_in_declared_order(self):
        self.metadata['patches'].append(self.add_patch('two', 'after', 'last'))
        self.metadata['expected_tree'] = self.tree_for('last')
        patchset.apply_patchset(self.source, self.load())
        self.assertEqual((self.source / 'value.txt').read_text(), 'last\n')

    def test_reversed_series_fails(self):
        self.metadata['patches'].insert(0, self.add_patch('two', 'after', 'last'))
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(self.source, self.load())
        self.assertEqual((self.source / 'value.txt').read_text(), 'before\n')

    def test_ambient_git_directory_does_not_redirect_application(self):
        with patch.dict(os.environ, {'GIT_DIR': '/nonexistent', 'GIT_WORK_TREE': '/nonexistent'}):
            result = patchset.apply_patchset(self.source, self.load())
        self.assertEqual(result['result_tree'], self.metadata['expected_tree'])

    def test_patch_cannot_introduce_symlinks(self):
        content = (b'diff --git a/link b/link\nnew file mode 120000\n'
                   b'--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+/tmp/outside\n'
                   b'\\ No newline at end of file\n')
        entry = self.metadata['patches'][0]
        (self.project / entry['file']).write_bytes(content)
        entry.update(sha256=hashlib.sha256(content).hexdigest(), paths=['link'])
        with self.assertRaisesRegex(patchset.PatchsetError, 'unsupported result file mode'):
            patchset.apply_patchset(self.source, self.load())

    def test_later_conflict_does_not_return_a_valid_checkout(self):
        self.metadata['patches'].append(self.add_patch('two', 'wrong', 'last'))
        self.load()
        created = []

        def local_fetch(destination, manifest):
            created.append(destination)
            subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(self.source), str(destination)],
                           env=self.env, check=True)

        output, error = io.StringIO(), io.StringIO()
        with (patch.object(patchset, 'fetch_source', side_effect=local_fetch),
              redirect_stdout(output), redirect_stderr(error)):
            status = patchset.main(['check', '--root', str(self.project)])
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('patch', error.getvalue().lower())
        self.assertTrue(created)
        self.assertTrue(all(not path.exists() for path in created))
        self.assertEqual((self.source / 'value.txt').read_text(), 'before\n')

    def test_checkout_subdirectory_is_rejected(self):
        nested = self.source / 'nested'
        nested.mkdir()
        with self.assertRaises(patchset.PatchsetError):
            patchset.apply_patchset(nested, self.load())


if __name__ == '__main__':
    unittest.main()
