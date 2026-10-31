# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ci.native import producer


class PreparationTest(unittest.TestCase):
    def test_prepare_job_writes_the_bound_manifest_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = SimpleNamespace(commit='a' * 40)
            ctx = SimpleNamespace(
                work=root,
                root=root,
                current=lambda: current,
                initial=current,
                association=MagicMock(),
                qid='b' * 64,
                execution={'nonce': 'c' * 32},
                admission={'source': {'tree': 'd' * 40}},
                identity={'fixture': 'identity'},
            )
            manifest = SimpleNamespace(
                commit='e' * 40, release='v6.7.5', expected_tree='d' * 40
            )
            with (
                patch.object(producer, 'candidate_data', return_value=(root, manifest)),
                patch.object(producer, 'isolated_source', return_value=b'source'),
                patch.object(producer, 'prepare_image', return_value={'files': []}),
                patch.object(
                    producer, 'pack_inputs', return_value=({}, [], 'f' * 64)
                ) as pack,
            ):
                producer.prepare_job(ctx)
            self.assertEqual(
                json.loads((root / 'prepared/prepared.json').read_text()),
                {
                    'identity': ctx.identity,
                    'qualification_id': ctx.qid,
                    'input_manifest_sha256': 'f' * 64,
                },
            )
            self.assertEqual(
                pack.call_args.kwargs['invocation'],
                {'nonce': 'c' * 32, 'qualification_id': 'b' * 64},
            )

    def test_preparation_cleans_up_on_success_failed_command_and_bad_source(self):
        for failure in [None, 'start', 'source']:
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                work = Path(directory)
                candidate = work / 'candidate'
                candidate.mkdir()
                (candidate / 'patchset.json').write_text('{}')
                source = b'prepared bytes'
                expected = {
                    'tree': 'a' * 40,
                    'sha256': hashlib.sha256(source).hexdigest(),
                    'bytes': len(source),
                }
                ctx = SimpleNamespace(
                    work=work,
                    root=Path(__file__).resolve().parents[1],
                    admission={'source': expected},
                )
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode='w') as archive:
                    for name, data in [
                        ('source.json', json.dumps(expected).encode()),
                        ('source.tar', b'wrong' if failure == 'source' else source),
                    ]:
                        entry = tarfile.TarInfo(name)
                        entry.size = len(data)
                        archive.addfile(entry, io.BytesIO(data))
                state = {'removed': []}

                def engine(
                    command, *, state=state, failure=failure, buffer=buffer, **kwargs
                ):
                    args = command[1:]
                    self.assertNotIn('GH_TOKEN', kwargs['env'])
                    self.assertNotIn('NATIVE_GATE_PRIVATE_KEY', kwargs['env'])
                    value = b''
                    if args[:2] == ['image', 'inspect']:
                        value = ('sha256:' + producer.BUILDER_CONFIG).encode()
                    elif args[:2] == ['volume', 'create']:
                        state['volume'] = args[-1]
                    elif args[0] == 'create':
                        state['name'] = next(
                            arg[7:] for arg in args if arg.startswith('--name=')
                        )
                        state['token'] = state['name'].removeprefix('pf-prepare-')
                    elif args[0] == 'inspect':
                        value = json.dumps(
                            [
                                {
                                    'Id': 'b' * 64,
                                    'Name': '/' + state['name'],
                                    'Image': 'sha256:' + producer.BUILDER_CONFIG,
                                    'Config': {
                                        'Image': producer.BUILDER_REFERENCE,
                                        'User': '1000:1000',
                                        'Labels': {producer.LABEL: state['token']},
                                    },
                                    'AppArmorProfile': 'docker-default',
                                    'Mounts': [
                                        {
                                            'Type': 'volume',
                                            'Name': state['volume'],
                                            'Destination': '/work',
                                            'RW': True,
                                        }
                                    ],
                                    'HostConfig': {
                                        'ReadonlyRootfs': True,
                                        'Privileged': False,
                                        'NetworkMode': 'none',
                                        'Binds': None,
                                        'Devices': [],
                                        'DeviceRequests': None,
                                        'CapDrop': ['ALL'],
                                        'CapAdd': None,
                                        'Memory': 2 * 1024**3,
                                        'MemorySwap': 2 * 1024**3,
                                        'PidsLimit': 256,
                                        'NanoCpus': 2 * 10**9,
                                        'CgroupnsMode': 'private',
                                        'PidMode': '',
                                        'IpcMode': 'private',
                                        'UTSMode': '',
                                        'RestartPolicy': {'Name': 'no'},
                                        'SecurityOpt': ['no-new-privileges'],
                                    },
                                }
                            ]
                        ).encode()
                    elif args[0] == 'start' and failure == 'start':
                        raise subprocess.CalledProcessError(1, command)
                    elif args[0] == 'cp' and args[-1] == '-':
                        value = buffer.getvalue()
                    elif args[0] == 'rm':
                        state['removed'].append('container')
                    elif args[:2] == ['volume', 'inspect']:
                        value = json.dumps(
                            [
                                {
                                    'Name': state['volume'],
                                    'Labels': {producer.LABEL: state['token']},
                                    'Driver': 'local',
                                    'Scope': 'local',
                                    'Options': None,
                                }
                            ]
                        ).encode()
                    elif args[:2] == ['volume', 'rm']:
                        state['removed'].append('volume')
                    return subprocess.CompletedProcess(command, 0, value, b'')

                with (
                    patch.object(producer, 'fetch_source'),
                    patch.object(producer.subprocess, 'run', side_effect=engine),
                ):
                    if failure is None:
                        self.assertEqual(
                            producer.isolated_source(ctx, candidate, None), source
                        )
                    else:
                        with self.assertRaises(
                            (ValueError, subprocess.SubprocessError)
                        ):
                            producer.isolated_source(ctx, candidate, None)
                self.assertEqual(state['removed'], ['container', 'volume'])


if __name__ == '__main__':
    unittest.main()
