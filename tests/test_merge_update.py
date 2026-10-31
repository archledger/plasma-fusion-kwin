# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise exact-base update semantics against disposable real Git remotes."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.native import merge_update


class MergeUpdateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='merge-update-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.remote = self.root / 'remote.git'
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        self.git('init', '-q', '--initial-branch=main')
        self.tree = self.git('mktree', input='')
        self.root_commit = self.commit('Root')
        self.base = self.commit('Base', self.root_commit)
        self.head = self.commit('PR head', self.base)
        self.merge = self.commit('Qualified merge', self.base, self.head)
        self.git('update-ref', 'refs/heads/main', self.base)
        self.git('init', '-q', '--bare', str(self.remote))
        self.git('push', '-q', str(self.remote), 'refs/heads/main:refs/heads/main')
        self.target = merge_update.MergeCommit(self.base, self.head, self.merge, self.tree)

    def git(self, *args, input=None):
        return subprocess.check_output(['git', *args], cwd=self.source, env=self.env,
                                       text=True, input=input, stderr=subprocess.PIPE).strip()

    def commit(self, message, *parents):
        args = ['commit-tree', self.tree]
        for parent in parents:
            args.extend(['-p', parent])
        return self.git(*args, input=message + '\n')

    def remote_tip(self):
        return self.git('--git-dir', str(self.remote), 'rev-parse', 'refs/heads/main')

    def update(self, *, apply=False):
        with patch.dict(os.environ, self.env, clear=True):
            return merge_update.push_exact_merge(self.source, str(self.remote), self.target,
                                                 apply=apply)

    def test_default_is_dry_run_and_cannot_change_main(self):
        result = self.update()
        self.assertFalse(result.applied)
        self.assertEqual(self.remote_tip(), self.base)

    def test_only_the_exact_validated_merge_is_pushed(self):
        result = self.update(apply=True)
        self.assertTrue(result.applied)
        self.assertEqual(result.commit, self.merge)
        self.assertEqual(self.remote_tip(), self.merge)

    def test_changed_base_before_observation_is_rejected(self):
        newer = self.commit('Concurrent advance', self.base)
        self.git('push', '-q', str(self.remote), f'{newer}:refs/heads/main')
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update(apply=True)
        self.assertEqual(self.remote_tip(), newer)

    def test_lease_rejects_advance_between_observation_and_push(self):
        self.assert_late_change_rejected(self.commit('Concurrent advance', self.base))

    def test_lease_rejects_rewind_even_when_push_would_be_fast_forward(self):
        self.assert_late_change_rejected(self.root_commit)

    def assert_late_change_rejected(self, new_tip):
        # Transfer the fixture object without changing the target ref first.
        self.git('push', '-q', str(self.remote), f'{new_tip}:refs/heads/concurrent')
        real_git = merge_update.git

        def change_before_push(source, *args, **kwargs):
            if args[0] == 'push':
                self.git('--git-dir', str(self.remote), 'update-ref', 'refs/heads/main', new_tip)
            return real_git(source, *args, **kwargs)

        with patch.object(merge_update, 'git', side_effect=change_before_push), \
                self.assertRaises(merge_update.MergeUpdateError):
            self.update(apply=True)
        self.assertEqual(self.remote_tip(), new_tip)

    def test_wrong_tree_head_and_non_merge_fail_before_remote_update(self):
        # A valid but different tree is enough to test the declared-tree check.
        blob = self.git('hash-object', '-w', '--stdin', input='different\n')
        other_tree = self.git('mktree', input=f'100644 blob {blob}\tfile.txt\n')
        for target in [merge_update.MergeCommit(self.base, self.head, self.merge, other_tree),
                       merge_update.MergeCommit(self.base, self.root_commit, self.merge, self.tree),
                       merge_update.MergeCommit(self.base, self.head, self.head, self.tree)]:
            with self.subTest(target=target):
                self.target = target
                with self.assertRaises(merge_update.MergeUpdateError):
                    self.update(apply=True)
                self.assertEqual(self.remote_tip(), self.base)

    def test_local_follow_tags_setting_cannot_expand_update_scope(self):
        self.git('config', 'push.followTags', 'true')
        self.git('tag', '-a', 'must-stay-local', self.merge, '-m', 'Fixture')
        self.update(apply=True)
        self.assertEqual(self.git('--git-dir', str(self.remote), 'tag', '--list'), '')

    def test_existing_pre_push_hook_is_not_bypassed(self):
        hook = self.source / '.git/hooks/pre-push'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update(apply=True)
        self.assertEqual(self.remote_tip(), self.base)

    def test_replace_refs_cannot_disguise_a_non_merge(self):
        other = self.commit('Not a merge', self.base)
        self.git('replace', other, self.merge)
        self.target = merge_update.MergeCommit(self.base, self.head, other, self.tree)
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update(apply=True)
        self.assertEqual(self.remote_tip(), self.base)

    def test_graft_cannot_disguise_a_disconnected_commit_as_qualified_merge(self):
        other = self.commit('Disconnected commit')
        (self.source / '.git/info/grafts').write_text(f'{other} {self.base} {self.head}\n')
        self.assertEqual(self.git('show', '--no-patch', '--format=%P', other),
                         f'{self.base} {self.head}')
        raw = self.git('cat-file', 'commit', other).split('\n\n', 1)[0]
        self.assertFalse(any(line.startswith('parent ') for line in raw.splitlines()))
        self.target = merge_update.MergeCommit(self.base, self.head, other, self.tree)
        # A dry run suffices to reproduce unsafe acceptance without rewriting a ref.
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update()
        self.assertEqual(self.remote_tip(), self.base)

    def test_message_parent_lines_cannot_disguise_a_non_merge(self):
        other = self.commit(f'Not a merge\n\nparent {self.base}\nparent {self.head}')
        self.target = merge_update.MergeCommit(self.base, self.head, other, self.tree)
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update()
        self.assertEqual(self.remote_tip(), self.base)

    def test_third_raw_parent_is_rejected(self):
        other = self.commit('Three parents', self.base, self.head, self.root_commit)
        self.target = merge_update.MergeCommit(self.base, self.head, other, self.tree)
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update()
        self.assertEqual(self.remote_tip(), self.base)

    def test_oversized_raw_commit_is_rejected(self):
        other = self.commit('x' * merge_update.MAX_COMMIT_BYTES, self.base, self.head)
        self.target = merge_update.MergeCommit(self.base, self.head, other, self.tree)
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update()
        self.assertEqual(self.remote_tip(), self.base)

    def test_ambient_git_directory_cannot_redirect_the_source(self):
        self.env['GIT_DIR'] = str(self.remote)
        result = self.update(apply=True)
        self.assertTrue(result.applied)
        self.assertEqual(self.remote_tip(), self.merge)

    def test_missing_remote_ref_is_not_created(self):
        self.git('--git-dir', str(self.remote), 'update-ref', '-d', 'refs/heads/main')
        with self.assertRaises(merge_update.MergeUpdateError):
            self.update(apply=True)
        self.assertEqual(self.git('--git-dir', str(self.remote), 'for-each-ref',
                                  '--format=%(refname)', 'refs/heads/main'), '')

    def test_invalid_object_ids_are_rejected(self):
        for value in ['', '--all', 'HEAD', 'a' * 39, 'a' * 40 + '\n', '0' * 40]:
            with self.subTest(value=value):
                self.target = merge_update.MergeCommit(self.base, self.head, value, self.tree)
                with self.assertRaises(merge_update.MergeUpdateError):
                    self.update(apply=True)
                self.assertEqual(self.remote_tip(), self.base)


if __name__ == '__main__':
    unittest.main()
