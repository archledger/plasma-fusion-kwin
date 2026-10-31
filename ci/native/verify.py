# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independently verify the guest's native evidence on the runner."""
import json
import re

from ci.native.guest import KERNEL, VGEM, verify_oci
from ci.native.results import SUITES, require, verify_tests
from ci.native.runtime_assets import FRAMES
from tools.native_inputs import BUILDER_CONFIG, BUILDER_DIGEST


def verify_renderer(text, node):
    require(re.search(r'^uid=1000 node=' + re.escape(node) + r' driver=vgem$', text, re.MULTILINE),
            'renderer did not identify the guest vgem node')
    renderer = re.findall(r'^GL_RENDERER=(llvmpipe[^\n]*)$', text, re.MULTILINE)
    require(len(renderer) == 1 and text.count('PREFLIGHT_PASS:') == 1 and 'PREFLIGHT_FAIL:' not in text,
            'renderer did not complete successfully')
    pixels = re.findall(r'^pixel\[(\d+),2\]=(\d+),(\d+),(\d+),(\d+)$', text, re.MULTILINE)
    require(len(pixels) == 4, 'missing renderer pixel checks')
    for values, expected in zip(pixels, [(2, 255, 0, 0), (12, 51, 102, 153)] * 2, strict=True):
        x, red, green, blue, alpha = map(int, values)
        require(x == expected[0] and alpha == 255 and
                all(abs(actual - wanted) <= 1 for actual, wanted in zip((red, green, blue), expected[1:], strict=True)),
                'renderer pixel differs')
    return renderer[0]


def verify_envelope(value, mode, inputs, expected_packages):
    require(mode in {'render', 'native'}, 'invalid native mode')
    metadata, files = value['metadata'], value['files']
    require(metadata.get('status') == '0' and metadata.get('mode') == mode and
            metadata.get('container_removed') == 'true' and 'error' not in metadata,
            'guest workload or cleanup did not succeed')
    require(metadata.get('kernel') == KERNEL and metadata.get('vgem_sha256') == VGEM and
            metadata.get('builder_config') == BUILDER_CONFIG and metadata.get('builder_manifest') == BUILDER_DIGEST,
            'guest or builder identity differs')
    node = metadata.get('drm_node', '')
    require(re.fullmatch(r'/dev/dri/card[0-9]+', node), 'invalid guest DRM node')
    common = {'exit-code.txt', 'last-phase.txt', 'collector-status.txt', 'guest-stage.log',
              'render-context.txt', 'render-build.log', 'source.json', 'packages.txt', 'builder-oci.json'}
    required = common | ({'ctest.xml', 'LastTest.log', 'registered-tests.json', 'native-results.json',
                          'runtime-assets.json', 'executables.sha256', 'configure.log', 'build.log',
                          'CMakeCache.txt'} if mode == 'native' else set())
    require(not required - files.keys(), 'native evidence is incomplete')
    identity = tuple(int(metadata[key]) for key in ['drm_major', 'drm_minor']) + (0, int(metadata['drm_gid']), 0o660)
    require(identity[0] == 226 and 0 <= identity[1] < 128, 'unexpected DRM device numbers')
    verify_oci(json.loads(files['builder-oci.json']), node, identity, mode)
    require(files['exit-code.txt'].strip() == '0' and files['collector-status.txt'].strip() == 'ok' and
            files['last-phase.txt'].strip() == ('complete' if mode == 'native' else 'render-complete'),
            'inner workload did not complete')
    require(json.loads(files['source.json']) == inputs['source'], 'built source identity differs')
    packages = []
    for line in files['packages.txt'].splitlines():
        parts = line.split('\t')
        require(len(parts) == 2, 'invalid package inventory record')
        if parts[0] != 'gpg-pubkey':
            packages.append(parts[1])
    require(sorted(packages) == sorted(expected_packages), 'built dependency inventory differs')
    result = {'mode': mode, 'renderer': verify_renderer(files['render-context.txt'], node)}
    if mode == 'native':
        measured = verify_tests(files['ctest.xml'], files['LastTest.log'], files['registered-tests.json'])
        require(json.loads(files['native-results.json']) == measured, 'native summary differs from measured output')
        assets = json.loads(files['runtime-assets.json'])
        expected = [{'source': '/tmp/source/src/qml/frames/plasma/' + name,
                     'installed': '/tmp/private-prefix/share/kwin-wayland/frames/plasma/' + name,
                     'sha256': digest} for name, digest in FRAMES.items()]
        require(assets == expected, 'runtime QML identity or layout differs')
        hashes = files['executables.sha256'].splitlines()
        require(len(hashes) == 3 and all(re.fullmatch(r'[0-9a-f]{64}  /tmp/build/bin/test[A-Za-z]+', line)
                                       for line in hashes), 'invalid executable identity records')
        require({line.split('  ')[1] for line in hashes} ==
                {'/tmp/build/bin/' + name.removeprefix('kwin-') for name in SUITES},
                'test executable inventory differs')
        result['tests'] = measured
    return result
