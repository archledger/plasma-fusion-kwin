# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unprivileged renderer/native workload, executed only inside the guest builder."""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path

from ci.native.results import SUITES, require, verify_registration, verify_tests
from ci.native.runtime_assets import stage


def verify_environment():
    require(os.geteuid() == 1000, 'workload must run as UID1000')
    status = Path('/proc/self/status').read_text()
    require(re.search(r'^NoNewPrivs:\s+1$', status, re.MULTILINE) and
            re.search(r'^CapEff:\s+0+$', status, re.MULTILINE) and
            re.search(r'^Seccomp:\s+2$', status, re.MULTILINE), 'workload confinement differs')
    node = os.environ['EXPECTED_DRM_NODE']
    require(re.fullmatch(r'/dev/dri/card[0-9]+', node) and
            os.listdir('/dev/dri') == [Path(node).name] and stat.S_ISCHR(os.stat(node).st_mode),
            'unexpected builder DRM device')
    require(not Path('/dev/kvm').exists() and not Path('/run/podman/podman.sock').exists() and
            not Path('/var/run/docker.sock').exists(), 'unexpected host or engine access')
    return node


def main(mode, root=Path('/tmp')):
    results = root / 'results'
    results.mkdir()
    deadline = time.monotonic() + 3000
    phase, status = 'identity', 1

    def run(command, log, timeout):
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'native workload deadline exhausted')
        with (results / log).open('w') as output:
            process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                     timeout=min(timeout, remaining), check=False)
        require(process.returncode == 0, 'command failed: ' + command[0])

    try:
        node = verify_environment()
        inputs = json.loads((root / 'inputs.json').read_text())
        with (root / 'patched-source.tar').open('rb') as source:
            require(hashlib.file_digest(source, 'sha256').hexdigest() == inputs['source']['sha256'],
                    'source archive digest differs')
        (results / 'source.json').write_text(json.dumps(inputs['source'], indent=2))
        (results / 'os-release.txt').write_text(Path('/etc/os-release').read_text())
        run(['rpm', '-qa', '--qf', '%{NAME}\t%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n'], 'packages.txt', 30)
        for tool, log in [('cmake', 'cmake-version.txt'), ('ninja', 'ninja-version.txt'),
                          ('python3', 'python-version.txt'), ('c++', 'cxx-version.txt')]:
            run([tool, '--version'], log, 15)
        for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS']:
            os.environ.pop(key, None)
        os.environ.update(LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', QSG_INFO='1',
                          QT_LOGGING_RULES='qt.scenegraph.general=true;qt.rhi.general=true')
        phase = 'renderer'
        flags = shlex.split(subprocess.check_output(
            ['pkg-config', '--cflags', '--libs', 'gbm', 'egl', 'glesv2', 'libdrm'], text=True, timeout=15))
        run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(root / 'render_probe.c'),
             '-o', str(root / 'render-probe'), *flags], 'render-build.log', 60)
        run([str(root / 'render-probe'), node], 'render-context.txt', 120)
        if mode == 'render':
            status, phase = 0, 'render-complete'
            return 0

        phase = 'configure'
        source = root / 'source'
        source.mkdir()
        with tarfile.open(root / 'patched-source.tar') as archive:
            archive.extractall(source, filter='data')
        build = root / 'build'
        run(['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DBUILD_TESTING=ON',
             '-DCMAKE_BUILD_TYPE=Debug', '-DCMAKE_AUTOGEN_PARALLEL=2',
             '-DCMAKE_INSTALL_PREFIX=' + str(root / 'private-prefix')], 'configure.log', 300)
        pattern = '^kwin-(testOffscreenQuickView|testInternalWindow|testTabBox)$'
        run(['ctest', '--test-dir', str(build), '--show-only=json-v1', '-R', pattern],
            'registered-tests.json', 30)
        verify_registration((results / 'registered-tests.json').read_text())
        phase = 'build'
        run(['cmake', '--build', str(build), '--target',
             *(name.removeprefix('kwin-') for name in SUITES), '--parallel', '2'], 'build.log', 2800)
        binaries = {str(build / 'bin' / name.removeprefix('kwin-')): '' for name in SUITES}
        for path in binaries:
            with Path(path).open('rb') as binary:
                binaries[path] = hashlib.file_digest(binary, 'sha256').hexdigest()
        (results / 'executables.sha256').write_text(''.join(f'{digest}  {path}\n' for path, digest in binaries.items()))
        phase = 'runtime-data'
        (results / 'runtime-assets.json').write_text(json.dumps(stage(source, root / 'private-prefix'), indent=2))
        os.environ['XDG_DATA_DIRS'] = str(root / 'private-prefix/share') + ':' + os.environ.get(
            'XDG_DATA_DIRS', '/usr/local/share:/usr/share')
        runtime = root / 'native-runtime'
        runtime.mkdir(mode=0o700)
        os.environ['XDG_RUNTIME_DIR'] = str(runtime)
        require(stat.S_IMODE(Path('/tmp/.X11-unix').stat().st_mode) == 0o1777,
                'unexpected X11 socket directory mode')
        phase = 'test'
        run(['ctest', '--test-dir', str(build), '-R', pattern, '--output-on-failure', '--no-tests=error',
             '--parallel', '1', '--timeout', '180', '--output-junit', str(results / 'ctest.xml')], 'ctest.log', 600)
        shutil.copyfile(build / 'Testing/Temporary/LastTest.log', results / 'LastTest.log')
        for path, digest in binaries.items():
            with Path(path).open('rb') as binary:
                require(hashlib.file_digest(binary, 'sha256').hexdigest() == digest, 'test executable changed')
        measured = verify_tests((results / 'ctest.xml').read_text(), (results / 'LastTest.log').read_text(),
                                (results / 'registered-tests.json').read_text())
        (results / 'native-results.json').write_text(json.dumps(measured, indent=2))
        status, phase = 0, 'complete'
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        (results / 'workload-error.txt').write_text(str(error)[:8000])
        return 1
    finally:
        (results / 'exit-code.txt').write_text(str(status) + '\n')
        (results / 'last-phase.txt').write_text(phase + '\n')
        for source, name in [(root / 'build/Testing/Temporary/LastTest.log', 'LastTest.log'),
                             (root / 'build/CMakeCache.txt', 'CMakeCache.txt')]:
            if source.is_file():
                shutil.copyfile(source, results / name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['render', 'native'])
    args = parser.parse_args()
    raise SystemExit(main(args.mode))
