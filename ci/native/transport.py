# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded text-only evidence transport across the guest serial boundary."""
import base64
import binascii
import json
import os
import re
import stat
import textwrap
import zlib
from pathlib import Path

from ci.native.results import object_pairs, require

MAX_FILE_BYTES = 5 * 1024**2
MAX_TOTAL_BYTES = 16 * 1024**2
MAX_ENCODED_BYTES = 24 * 1024**2
NAMES = frozenset({
    'exit-code.txt', 'last-phase.txt', 'os-release.txt', 'packages.txt',
    'cmake-version.txt', 'ninja-version.txt', 'cxx-version.txt', 'python-version.txt',
    'character-devices.txt', 'registered-tests.json', 'CMakeCache.txt', 'LastTest.log',
    'render-context.txt', 'configure.log', 'build.log', 'ctest.log', 'ctest.xml',
    'native-results.json', 'guest-stage.log', 'collector-status.txt', 'workload-error.txt', 'render-build.log',
    'runtime-assets.json', 'executables.sha256', 'builder-image.json', 'source.json',
})
METADATA = frozenset({'status', 'mode', 'kernel', 'vgem_sha256', 'drm_node',
                      'builder_config', 'builder_manifest', 'container_removed', 'error'})


def validate(value, nonce):
    require(isinstance(nonce, str) and re.fullmatch(r'[0-9a-f]{32}', nonce), 'invalid native nonce')
    require(isinstance(value, dict) and set(value) == {'version', 'nonce', 'metadata', 'files'},
            'invalid native envelope')
    require(type(value['version']) is int and value['version'] == 1 and value['nonce'] == nonce,
            'native envelope identity differs')
    files, metadata = value['files'], value['metadata']
    require(isinstance(files, dict) and not set(files) - NAMES, 'unexpected native artifact name')
    require(all(isinstance(text, str) and len(text.encode()) <= MAX_FILE_BYTES for text in files.values()),
            'native artifact is not bounded text')
    require(isinstance(metadata, dict) and not set(metadata) - METADATA and
            all(isinstance(text, str) and len(text.encode()) <= 8192 for text in metadata.values()),
            'invalid native metadata')


def encode(nonce, files, metadata):
    value = {'version': 1, 'nonce': nonce, 'metadata': metadata, 'files': files}
    validate(value, nonce)
    raw = json.dumps(value, separators=(',', ':')).encode()
    require(len(raw) <= MAX_TOTAL_BYTES, 'native artifact set exceeds size budget')
    encoded = base64.b64encode(zlib.compress(raw)).decode('ascii')
    require(len(encoded) <= MAX_ENCODED_BYTES, 'encoded native artifacts exceed size budget')
    return ('\nPF_NATIVE_BEGIN ' + nonce + '\n' + '\n'.join(textwrap.wrap(encoded, 76)) +
            '\nPF_NATIVE_END ' + nonce + '\n')


def decode(serial, nonce):
    require(isinstance(serial, str) and len(serial) <= MAX_ENCODED_BYTES + 8 * 1024**2,
            'native serial evidence exceeds size budget')
    lines = serial.splitlines()
    begin, end = 'PF_NATIVE_BEGIN ' + nonce, 'PF_NATIVE_END ' + nonce
    require(lines.count(begin) == 1 and lines.count(end) == 1 and
            sum(line.startswith('PF_NATIVE_BEGIN ') for line in lines) == 1 and
            sum(line.startswith('PF_NATIVE_END ') for line in lines) == 1, 'missing or repeated native frame')
    first, last = lines.index(begin), lines.index(end)
    require(last > first + 1, 'invalid native frame order')
    chunks = lines[first + 1:last]
    require(all(re.fullmatch(r'[A-Za-z0-9+/=]+', line) for line in chunks), 'invalid native encoding')
    encoded = ''.join(chunks)
    require(len(encoded) <= MAX_ENCODED_BYTES, 'encoded native artifacts exceed size budget')
    try:
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_TOTAL_BYTES + 1)
        require(len(raw) <= MAX_TOTAL_BYTES and decoder.eof and not decoder.unused_data,
                'compressed native artifacts exceed bounds or contain trailing data')
        value = json.loads(raw, object_pairs_hook=object_pairs)
    except (binascii.Error, zlib.error, UnicodeError) as error:
        raise ValueError('corrupt native artifact encoding') from error
    validate(value, nonce)
    return value


def gather(directory):
    try:
        root_fd = os.open(Path(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise ValueError('native artifact root is not a real directory') from error
    result = {}
    try:
        for name in sorted(NAMES):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ValueError('native artifact cannot be opened safely: ' + name) from error
            with os.fdopen(fd, 'rb') as file:
                require(stat.S_ISREG(os.fstat(file.fileno()).st_mode), 'non-regular native artifact')
                data = file.read(MAX_FILE_BYTES + 1)
            require(len(data) <= MAX_FILE_BYTES, 'native artifact exceeds size budget: ' + name)
            try:
                result[name] = data.decode('utf-8')
            except UnicodeError as error:
                raise ValueError('native artifact is not UTF-8 text: ' + name) from error
        return result
    finally:
        os.close(root_fd)
