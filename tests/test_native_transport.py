# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import base64
import json
import os
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from ci.native import transport


class NativeTransportTest(unittest.TestCase):
    def setUp(self):
        self.nonce = 'a' * 32
        self.files = {'build.log': 'build completed\n', 'exit-code.txt': '0\n'}
        self.metadata = {'status': '0', 'mode': 'native'}

    def test_round_trip_preserves_only_named_text(self):
        text = transport.encode(self.nonce, self.files, self.metadata)
        self.assertEqual(transport.decode(text, self.nonce)['files'], self.files)

    def test_missing_duplicate_or_wrong_nonce_frame_is_rejected(self):
        text = transport.encode(self.nonce, self.files, self.metadata)
        for bad in [text + text, text.replace(self.nonce, 'b' * 32),
                    text.replace('PF_NATIVE_END', 'wrong'), 'prefix' + text.lstrip()]:
            with self.subTest(bad=bad[:40]), self.assertRaises(ValueError):
                transport.decode(bad, self.nonce)

    def test_unsafe_names_nontext_and_oversized_files_are_rejected(self):
        for files in [{'../key': 'x'}, {'guest.qcow2': 'x'}, {'build.log': 7}]:
            with self.subTest(files=files), self.assertRaises(ValueError):
                transport.encode(self.nonce, files, self.metadata)
        with patch.object(transport, 'MAX_FILE_BYTES', 4), self.assertRaises(ValueError):
            transport.encode(self.nonce, self.files, self.metadata)

    def test_compression_bomb_and_trailing_data_are_rejected(self):
        raw = json.dumps({'version': 1, 'nonce': self.nonce, 'files': {'build.log': 'x' * 1000},
                          'metadata': self.metadata}).encode()
        for compressed in [zlib.compress(raw), zlib.compress(raw) + b'extra']:
            text = ('\nPF_NATIVE_BEGIN ' + self.nonce + '\n' +
                    base64.b64encode(compressed).decode() + '\nPF_NATIVE_END ' + self.nonce + '\n')
            with patch.object(transport, 'MAX_TOTAL_BYTES', 100), self.assertRaises(ValueError):
                transport.decode(text, self.nonce)

        value = {'version': 1, 'nonce': self.nonce, 'files': self.files, 'metadata': self.metadata}
        trailing = zlib.compress(json.dumps(value).encode()) + b'extra'
        text = ('\nPF_NATIVE_BEGIN ' + self.nonce + '\n' + base64.b64encode(trailing).decode() +
                '\nPF_NATIVE_END ' + self.nonce + '\n')
        with self.assertRaises(ValueError):
            transport.decode(text, self.nonce)

    def test_gather_rejects_links_fifos_and_oversized_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'build.log'
            file.write_text('ok')
            self.assertEqual(transport.gather(root), {'build.log': 'ok'})
            file.unlink()
            file.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                transport.gather(root)
            file.unlink()
            os.mkfifo(file)
            with self.assertRaises(ValueError):
                transport.gather(root)
            file.unlink()
            file.write_text('too long')
            with patch.object(transport, 'MAX_FILE_BYTES', 2), self.assertRaises(ValueError):
                transport.gather(root)


if __name__ == '__main__':
    unittest.main()
