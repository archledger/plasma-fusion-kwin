# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import native_inputs


class NativeInputsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = self.root / 'image'
        self.image.mkdir()
        config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'config': {'User': 'builder'}}).encode()
        layer = b'opaque layer content'
        self.config_sha = hashlib.sha256(config).hexdigest()
        self.layer_sha = hashlib.sha256(layer).hexdigest()
        (self.image / self.config_sha).write_bytes(config)
        (self.image / self.layer_sha).write_bytes(layer)
        manifest = json.dumps({'schemaVersion': 2,
                               'config': {'digest': 'sha256:' + self.config_sha, 'size': len(config)},
                               'layers': [{'digest': 'sha256:' + self.layer_sha, 'size': len(layer)}]}).encode()
        (self.image / 'manifest.json').write_bytes(manifest)
        (self.image / 'version').write_text('Directory Transport Version: 1.1\n')
        self.manifest_sha = hashlib.sha256(manifest).hexdigest()

    def verify(self):
        with patch.object(native_inputs, 'BUILDER_DIGEST', self.manifest_sha), \
                patch.object(native_inputs, 'BUILDER_CONFIG', self.config_sha):
            return native_inputs.verify_image_directory(self.image)

    def test_manifest_config_and_blob_hashes_are_verified(self):
        self.assertEqual(self.verify()['config'], self.config_sha)

    def test_corrupt_blob_and_extra_file_are_rejected(self):
        (self.image / self.layer_sha).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.verify()
        (self.image / self.layer_sha).write_bytes(b'opaque layer content')
        (self.image / 'unexpected').write_text('extra')
        with self.assertRaises(ValueError):
            self.verify()

    def test_linked_blob_and_wrong_manifest_are_rejected(self):
        blob = self.image / self.layer_sha
        outside = self.root / 'blob'
        blob.rename(outside)
        blob.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.verify()
        blob.unlink()
        outside.rename(blob)
        (self.image / 'manifest.json').write_text('{}')
        with self.assertRaises(ValueError):
            self.verify()

    def test_archive_uses_patched_index_tree_not_head(self):
        source = self.root / 'source'
        source.mkdir()
        external_index = self.root / 'unrelated-index'
        external_index.write_bytes(b'leave this unrelated index untouched')
        with patch.dict(os.environ, {'GIT_INDEX_FILE': str(external_index)}):
            env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                   GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')

        def git(*args):
            return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', *args],
                                           cwd=source, env=env, stderr=subprocess.PIPE).decode().strip()

        git('init', '-q')
        (source / 'input.txt').write_text('original')
        git('add', 'input.txt')
        git('commit', '-qm', 'base')
        (source / 'input.txt').write_text('patched')
        git('add', 'input.txt')
        tree = git('write-tree')
        archive = self.root / 'source.tar'
        native_inputs.archive_tree(source, tree, archive)
        with tarfile.open(archive) as tar, tar.extractfile('input.txt') as file:
            self.assertEqual(file.read(), b'patched')
            self.assertTrue(all(member.mtime == 0 for member in tar.getmembers()))
        with self.assertRaises(ValueError):
            native_inputs.archive_tree(source, '0' * 40, self.root / 'wrong.tar')
        self.assertEqual(external_index.read_bytes(), b'leave this unrelated index untouched')

    def test_acquisition_failure_does_not_write_success_receipt(self):
        output = self.root / 'prepared'
        manifest = types.SimpleNamespace(expected_tree='a' * 40)
        with patch.object(native_inputs, 'load_manifest', return_value=manifest), \
                patch.object(native_inputs, 'fetch_source'), \
                patch.object(native_inputs, 'apply_patchset', return_value={'result_tree': 'a' * 40}), \
                patch.object(native_inputs, 'archive_tree', return_value={'sha256': 'b' * 64}), \
                patch.object(native_inputs.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'skopeo')), \
                self.assertRaises(subprocess.CalledProcessError):
            native_inputs.prepare(self.root, output)
        self.assertFalse((output / 'inputs.json').exists())


if __name__ == '__main__':
    unittest.main()
