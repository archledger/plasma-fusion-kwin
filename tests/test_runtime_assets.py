# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.native import runtime_assets


class RuntimeAssetsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'source'
        self.frames = self.source / 'src/qml/frames/plasma'
        self.frames.mkdir(parents=True)
        self.prefix = self.root / 'private-prefix'
        self.expected = {}
        for name in runtime_assets.FRAMES:
            data = ('fixture: ' + name).encode()
            (self.frames / name).write_bytes(data)
            self.expected[name] = hashlib.sha256(data).hexdigest()

    def stage(self):
        with patch.object(runtime_assets, 'FRAMES', self.expected):
            return runtime_assets.stage(self.source, self.prefix)

    def test_only_verified_qml_is_copied_to_production_layout(self):
        result = self.stage()
        self.assertEqual(len(result), 3)
        for entry in result:
            destination = Path(entry['installed'])
            self.assertTrue(destination.is_relative_to(self.prefix / 'share/kwin-wayland/frames/plasma'))
            self.assertEqual(destination.read_bytes(), Path(entry['source']).read_bytes())
        self.assertFalse((self.prefix / 'share/kwin/frames').exists())

    def test_changed_source_refuses_before_creating_destination(self):
        (self.frames / 'frame_none.qml').write_text('changed')
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.prefix.exists())

    def test_source_and_destination_symlinks_are_rejected(self):
        name = 'frame_none.qml'
        original = self.frames / name
        outside = self.root / 'outside'
        original.rename(outside)
        original.symlink_to(outside)
        with self.assertRaises((OSError, ValueError)):
            self.stage()
        original.unlink()
        outside.rename(original)
        target = self.root / 'other'
        target.mkdir()
        self.prefix.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(list(target.iterdir()), [])

    def test_existing_staging_is_not_overwritten(self):
        self.stage()
        with self.assertRaises((OSError, ValueError)):
            self.stage()


if __name__ == '__main__':
    unittest.main()
