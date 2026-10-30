# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class ChangeRangeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.root / 'patchset-evidence').mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid',
                        RUNNER_TEMP=str(self.root))
        self.git('init', '-q')
        self.base = self.commit('clean\n')
        workflow = Path(__file__).resolve().parents[1] / '.github/workflows/fast-validation.yml'
        text = workflow.read_text()
        block = text.split('      - name: Check the event\'s complete change range\n', 1)[1]
        block = block.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0]
        self.script = textwrap.dedent(block)

    def git(self, *arguments, input=None):
        return subprocess.check_output(['git', *arguments], cwd=self.source,
                                       env=self.env, text=True, input=input).strip()

    def commit(self, content, filename='file.txt'):
        path = self.source / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.git('add', '--', filename)
        self.git('commit', '-qm', 'Fixture')
        return self.git('rev-parse', 'HEAD')

    def run_step(self, base, head):
        return subprocess.run(['bash', '-c', self.script], cwd=self.source,
                              env=dict(self.env, BASE_COMMIT=base, HEAD_COMMIT=head),
                              capture_output=True, text=True, timeout=10, check=False)

    def test_clean_range_passes(self):
        head = self.commit('another clean line\n')
        self.assertEqual(self.run_step(self.base, head).returncode, 0)

    def test_committed_whitespace_fails_even_with_clean_worktree(self):
        head = self.commit('trailing space \n')
        self.assertEqual(self.git('status', '--porcelain'), '')
        result = self.run_step(self.base, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('trailing whitespace', result.stdout)

    def test_new_branch_checks_entire_head_tree(self):
        head = self.commit('trailing space \n')
        self.assertNotEqual(self.run_step('0' * 40, head).returncode, 0)
        self.assertEqual(self.run_step('0' * 40, self.base).returncode, 0)

    def test_invalid_or_missing_revision_fails(self):
        for value in ['--help', '', 'f' * 40]:
            with self.subTest(value=value):
                self.assertNotEqual(self.run_step(value, self.base).returncode, 0)

    def test_diff_format_context_is_checked_at_application_layer(self):
        head = self.commit(' \n', 'patches/context.patch')
        self.assertEqual(self.run_step(self.base, head).returncode, 0)

    def test_rewritten_branch_fetches_missing_exact_endpoints(self):
        remote = self.root / 'origin.git'
        self.git('clone', '-q', '--bare', str(self.source), str(remote))
        self.git('remote', 'add', 'origin', str(remote))

        def detached_commit(content, parent):
            prefix = ['--git-dir', str(remote)]
            blob = self.git(*prefix, 'hash-object', '-w', '--stdin', input=content)
            tree = self.git(*prefix, 'mktree', input=f'100644 blob {blob}\tfile.txt\n')
            return self.git(*prefix, 'commit-tree', tree, '-p', parent, input='Unreferenced\n')

        before = detached_commit('old branch tip\n', self.base)
        after = detached_commit('new branch tip \n', self.base)
        for commit in [before, after]:
            result = subprocess.run(['git', 'cat-file', '-e', commit], cwd=self.source,
                                    env=self.env, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
        result = self.run_step(before, after)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('trailing whitespace', result.stdout)
        for commit in [before, after]:
            self.assertEqual(self.git('cat-file', '-t', commit), 'commit')


if __name__ == '__main__':
    unittest.main()
