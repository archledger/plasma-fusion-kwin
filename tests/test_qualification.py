# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic proof fixtures exercise real native evidence verification, not a VM."""
import copy
import hashlib
import io
import json
import stat
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ci.hypervisor.boot_guest import GUEST_SHA256
from ci.native.controller import native_command
from ci.native.current_candidate import verify_current_association
from ci.native.guest import KERNEL, VGEM
from ci.native.qualification import (
    QualificationError,
    QualificationPolicy,
    verify_qualification,
)
from ci.native.results import SUITES, verify_tests
from ci.native.runtime_assets import FRAMES
from ci.native.signed_merge import SignedMerge
from ci.native.transport import NAMES, decode, encode
from tools.native_inputs import BUILDER_CONFIG, BUILDER_DIGEST
from tools.probe_hosted_boot import LABEL, validate_runtime
from tools.run_native_guest import BOOTSTRAP, CONFIG, IMAGE


class API:
    repository = 'owner/downstream'

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, resource, **parameters):
        self.calls.append((resource, parameters))
        return copy.deepcopy(self.responses[resource])


class QualificationTest(unittest.TestCase):
    def test_reporter_id_is_derived_from_authenticated_job_inventory(self):
        self.api.responses['actions/runs/500/attempts/1/jobs']['jobs'][3]['id'] = 1703
        self.verify()

    def setUp(self):
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40, 'A' * 40,
                                          ('fixture-package',), app_id=300)
        self.signed = SignedMerge('d' * 40, 'e' * 40, 'A' * 40, 'b' * 40, 'c' * 40)
        self.receipt = {
            'identity': {
                'schema_version': 1,
                'admission': {
                    'repository_id': 100, 'pr_number': 7, 'head_repository_id': 101,
                    'base': 'b' * 40, 'head': 'c' * 40, 'merge': 'f' * 40,
                    'signed_merge': 'd' * 40, 'tree': 'e' * 40, 'payload_sha256': '1' * 64,
                    'source': {'tree': '2' * 40, 'sha256': '3' * 64, 'bytes': 4096},
                    'approver_id': 10, 'upstream_commit': self.policy.upstream_commit,
                },
                'execution': {'workflow_id': 200, 'run_id': 500, 'run_attempt': 1,
                              'run_number': 12, 'launcher': 'a' * 40, 'nonce': '6' * 32},
            },
            'qualification_id': '',
            'evidence': {'prepare_job_id': 700, 'admission_job_id': 701,
                          'native_job_id': 702, 'admission_artifact_id': 901,
                         'check_run_id': 800, 'input_manifest_sha256': '4' * 64},
        }
        jobs = [self.job(700, 'prepare', '00:00:00', '00:00:30'),
                self.job(701, 'admit', '00:00:30', '00:01:00'),
                self.job(702, 'native', '00:01:00', '00:30:00'),
                self.job(703, 'report', '00:30:00', '00:31:00')]
        self.api = API({
            'actions/artifacts/900': {
                'id': 900, 'name': 'native-qualification-500-1', 'expired': False,
                'digest': '', 'size_in_bytes': 1, 'created_at': '2026-10-30T00:29:59Z',
                'workflow_run': {'id': 500, 'repository_id': 100, 'head_repository_id': 100,
                                 'head_sha': 'a' * 40},
            },
            'actions/artifacts/901': {
                'id': 901, 'name': 'native-admission-500-1', 'expired': False,
                'digest': '', 'created_at': '2026-10-30T00:00:50Z',
                'workflow_run': {'id': 500, 'repository_id': 100, 'head_repository_id': 100,
                                 'head_sha': 'a' * 40},
            },
            'actions/runs/500': {
                'id': 500, 'repository': {'id': 100, 'full_name': 'owner/downstream'},
                'head_repository': {'id': 100}, 'workflow_id': 200,
                'path': '.github/workflows/trusted-native.yml',
                'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
                'head_branch': 'main', 'head_sha': 'a' * 40, 'run_attempt': 1, 'run_number': 12,
                'actor': {'id': 10, 'login': 'maintainer'}, 'triggering_actor': {'id': 10},
            },
            'actions/runs/500/attempts/1/jobs': {'total_count': 4, 'jobs': jobs},
            'collaborators/maintainer/permission': {'permission': 'admin', 'user': {'id': 10}},
            'git/commits/' + 'f' * 40: {'sha': 'f' * 40, 'tree': {'sha': 'e' * 40},
                                      'parents': [{'sha': 'b' * 40}, {'sha': 'c' * 40}]},
            'check-runs/800': {'id': 800, 'name': 'Native KWin / trusted', 'app': {'id': 300},
                               'head_sha': 'd' * 40, 'status': 'completed', 'conclusion': 'success',
                               'external_id': '', 'completed_at': '2026-10-30T00:30:30Z',
                               'details_url': 'https://github.com/owner/downstream/actions/runs/500'},
        })
        self.report = self.native_report()
        self.archive = self.pack()

    def job(self, identifier, name, started, completed):
        return {'id': identifier, 'name': name, 'run_id': 500, 'run_attempt': 1,
                'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success',
                'started_at': '2026-10-30T' + started + 'Z',
                'completed_at': '2026-10-30T' + completed + 'Z'}

    def native_report(self):
        source = self.receipt['identity']['admission']['source']
        files = dict.fromkeys(NAMES, '')
        junit = ET.Element('testsuite', tests='3', failures='0', skipped='0', disabled='0')
        for name in SUITES:
            ET.SubElement(junit, 'testcase', name=name, status='run')
        files['ctest.xml'] = ET.tostring(junit, encoding='unicode')
        files['LastTest.log'] = (Path(__file__).parent / 'fixtures/native-v675-passed.log').read_text()
        files['registered-tests.json'] = json.dumps({'tests': [
            {'name': name,
             'command': ['/usr/bin/dbus-run-session', '/tmp/build/bin/' + name.removeprefix('kwin-')],
             'properties': [{'name': 'WORKING_DIRECTORY', 'value': '/tmp/build/autotests/integration'}]}
            for name in SUITES]})
        files['native-results.json'] = json.dumps(verify_tests(
            files['ctest.xml'], files['LastTest.log'], files['registered-tests.json']))
        files.update({'exit-code.txt': '0', 'collector-status.txt': 'ok',
                      'last-phase.txt': 'complete', 'source.json': json.dumps(source),
                      'packages.txt': 'fixture\tfixture-package\n',
                      'render-context.txt': 'uid=1000 node=/dev/dri/card0 driver=vgem\n'
                      'GL_RENDERER=llvmpipe (fixture)\n'
                      'pixel[2,2]=255,0,0,255\npixel[12,2]=51,102,153,255\n'
                      'pixel[2,2]=255,0,0,255\npixel[12,2]=51,102,153,255\nPREFLIGHT_PASS:\n'})
        files['runtime-assets.json'] = json.dumps([
            {'source': '/tmp/source/src/qml/frames/plasma/' + name,
             'installed': '/tmp/private-prefix/share/kwin-wayland/frames/plasma/' + name,
             'sha256': digest} for name, digest in FRAMES.items()])
        files['executables.sha256'] = '\n'.join(
            '5' * 64 + '  /tmp/build/bin/' + name.removeprefix('kwin-') for name in SUITES)
        files['builder-oci.json'] = json.dumps({
            'process': {'user': {'uid': 1000, 'gid': 1000, 'additionalGids': [100]},
                        'noNewPrivileges': True, 'capabilities': {},
                        'args': ['/usr/bin/python3', '-m', 'ci.native.workload', 'native']},
            'linux': {'seccomp': {'defaultAction': 'SCMP_ACT_ERRNO'},
                      'namespaces': [{'type': kind} for kind in ['pid', 'network', 'ipc', 'uts',
                                                                 'mount', 'cgroup']],
                      'devices': [{'path': '/dev/dri/card0', 'type': 'c', 'major': 226, 'minor': 0}],
                      'resources': {'devices': [{'allow': True, 'type': 'c', 'major': 226,
                                                 'minor': 0, 'access': 'rw'}]}}})
        metadata = {'status': '0', 'mode': 'native', 'kernel': KERNEL, 'vgem_sha256': VGEM,
                    'builder_config': BUILDER_CONFIG, 'builder_manifest': BUILDER_DIGEST,
                    'drm_node': '/dev/dri/card0', 'drm_major': '226', 'drm_minor': '0',
                    'drm_gid': '100', 'container_removed': 'true'}
        device = {'uid': 0, 'gid': 100, 'mode': 0o660, 'major': 10, 'minor': 232}
        report = {
            'qualification_id': '', 'success': True, 'mode': 'native', 'source': source,
            'image': IMAGE, 'image_id': CONFIG, 'controller_exit': 0,
            'input_manifest_sha256': '4' * 64, 'container_removed': True, 'volume_removed': True,
            'cleanup_confirmed': True, 'device_metadata_unchanged': True,
            'engine_security_options': ['name=apparmor', 'name=seccomp,profile=builtin'],
            'host_device_before': device, 'host_device_after': dict(device),
            'controller': {'success': True, 'uid': 1000, 'mode': 'native',
                           'apparmor': 'docker-default (enforce)',
                           'input_manifest_sha256': '4' * 64, 'base_sha256': GUEST_SHA256,
                           'command': native_command(Path('/work')), 'qemu_exit': 0,
                           'nonce': '6' * 32, 'wire': encode('6' * 32, files, metadata)},
        }
        token = '8' * 32
        name, volume = 'pf-native-' + token, 'pf-native-data-' + token
        report['runtime'] = {
            'Id': '9' * 64, 'Name': '/' + name, 'Image': CONFIG,
            'Config': {'User': '1000:1000', 'Image': IMAGE, 'Labels': {LABEL: token},
                       'Entrypoint': ['/usr/bin/python3'],
                       'Cmd': ['-I', '-B', '-c', BOOTSTRAP, 'native']},
            'Mounts': [{'Type': 'volume', 'Name': volume, 'Driver': 'local',
                        'Destination': '/work', 'RW': True}],
            'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True, 'NetworkMode': 'none',
                           'Binds': None, 'IpcMode': 'private', 'CgroupnsMode': 'private',
                           'PidMode': '', 'UTSMode': '',
                           'Devices': [{'PathOnHost': '/dev/kvm', 'PathInContainer': '/dev/kvm',
                                        'CgroupPermissions': 'rw'}], 'GroupAdd': ['100'],
                           'CapAdd': [], 'CapDrop': ['ALL'],
                           'SecurityOpt': ['apparmor=docker-default', 'no-new-privileges'],
                           'AutoRemove': True, 'RestartPolicy': {'Name': 'no'},
                           'Memory': 8 * 1024**3, 'MemorySwap': 8 * 1024**3,
                           'PidsLimit': 256, 'NanoCpus': 2 * 10**9},
            'AppArmorProfile': 'docker-default',
        }
        report['volume'] = {'Name': volume, 'Labels': {LABEL: token}, 'Driver': 'local',
                            'Scope': 'local', 'Options': None}
        report['container'] = validate_runtime(report['runtime'], name, token, volume,
                                                IMAGE, CONFIG, 100)
        return report

    def pack(self, *, refresh_id=True):
        canonical = json.dumps(self.receipt['identity'], sort_keys=True, separators=(',', ':')).encode()
        qid = hashlib.sha256(canonical).hexdigest()
        if refresh_id:
            self.receipt['qualification_id'] = qid
            self.report['qualification_id'] = qid
            self.api.responses['check-runs/800']['external_id'] = qid
        admission = io.BytesIO()
        with zipfile.ZipFile(admission, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('admission.json', json.dumps({
                'identity': self.receipt['identity'],
                'qualification_id': self.receipt['qualification_id'],
                'input_manifest_sha256': self.receipt['evidence']['input_manifest_sha256'],
            }))
        self.admission_archive = admission.getvalue()
        self.api.responses['actions/artifacts/901']['digest'] = (
            'sha256:' + hashlib.sha256(self.admission_archive).hexdigest())
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('qualification.json', json.dumps(self.receipt))
            archive.writestr('report.json', json.dumps(self.report))
        raw = out.getvalue()
        self.api.responses['actions/artifacts/900']['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
        self.api.responses['actions/artifacts/900']['size_in_bytes'] = len(raw)
        return raw

    def verify(self):
        return verify_qualification(self.api, self.policy, self.signed, artifact_id=900,
                                    archive=self.archive, admission_archive=self.admission_archive)

    def test_exact_admission_run_and_real_native_evidence_pass(self):
        result = self.verify()
        self.assertEqual(result.qualification_id, self.receipt['qualification_id'])
        self.assertEqual(result.run_id, 500)
        self.assertEqual((result.check_run_id, result.run_number), (800, 12))
        self.assertEqual(result.tests['qt_test_passed'], 43)
        self.assertEqual(result.signed_merge, self.signed)
        self.assertEqual(result.producer_attestation.pr_number, 7)
        self.assertEqual(result.producer_attestation.source_sha256, '3' * 64)

    def test_artifact_from_another_run_or_changed_bytes_is_rejected(self):
        for key, value in [('digest', 'sha256:' + '0' * 64), ('expired', True),
                           ('name', 'native-qualification-501-1')]:
            with self.subTest(key=key):
                before = self.api.responses['actions/artifacts/900'][key]
                self.api.responses['actions/artifacts/900'][key] = value
                with self.assertRaises(QualificationError):
                    self.verify()
                self.api.responses['actions/artifacts/900'][key] = before

    def test_source_workflow_and_partial_reruns_cannot_qualify(self):
        for key, value in [('workflow_id', 201), ('head_sha', 'b' * 40), ('head_branch', 'evil'),
                           ('event', 'pull_request'), ('run_attempt', 2), ('conclusion', 'failure')]:
            with self.subTest(key=key):
                before = self.api.responses['actions/runs/500'][key]
                self.api.responses['actions/runs/500'][key] = value
                with self.assertRaises(QualificationError):
                    self.verify()
                self.api.responses['actions/runs/500'][key] = before

    def test_skipped_or_different_attempt_native_job_is_rejected(self):
        job = self.api.responses['actions/runs/500/attempts/1/jobs']['jobs'][2]
        for key, value in [('conclusion', 'skipped'), ('run_attempt', 2), ('run_id', 501)]:
            with self.subTest(key=key):
                before = job[key]
                job[key] = value
                with self.assertRaises(QualificationError):
                    self.verify()
                job[key] = before

    def test_wrong_app_or_check_candidate_is_rejected(self):
        check = self.api.responses['check-runs/800']
        for key, value in [('app', {'id': 301}), ('head_sha', 'e' * 40),
                           ('external_id', '0' * 64), ('conclusion', 'neutral')]:
            with self.subTest(key=key):
                before = check[key]
                check[key] = value
                with self.assertRaises(QualificationError):
                    self.verify()
                check[key] = before

    def test_revoked_approver_and_missing_job_pages_are_rejected(self):
        self.api.responses['collaborators/maintainer/permission']['permission'] = 'read'
        with self.assertRaises(QualificationError):
            self.verify()
        self.api.responses['collaborators/maintainer/permission']['permission'] = 'admin'
        self.api.responses['actions/runs/500/attempts/1/jobs']['total_count'] = 5
        with self.assertRaises(QualificationError):
            self.verify()

    def test_missing_cleanup_cannot_be_hidden_by_success(self):
        self.report['cleanup_confirmed'] = False
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_rehashed_admission_still_must_match_signed_identity_and_policy(self):
        original = copy.deepcopy(self.receipt)
        for field, value in [('base', '9' * 40), ('head', '9' * 40),
                             ('signed_merge', '9' * 40), ('tree', '9' * 40),
                             ('upstream_commit', '9' * 40), ('approver_id', 11)]:
            with self.subTest(field=field):
                self.receipt = copy.deepcopy(original)
                self.receipt['identity']['admission'][field] = value
                self.archive = self.pack()
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_qualification_id_and_report_id_cannot_be_replayed(self):
        self.report['qualification_id'] = '9' * 64
        self.archive = self.pack(refresh_id=False)
        with self.assertRaises(QualificationError):
            self.verify()
        self.report['qualification_id'] = self.receipt['qualification_id']
        self.receipt['qualification_id'] = '9' * 64
        self.archive = self.pack(refresh_id=False)
        with self.assertRaises(QualificationError):
            self.verify()

    def test_synthetic_merge_must_have_the_same_tree_and_ordered_parents(self):
        value = self.api.responses['git/commits/' + 'f' * 40]
        value['parents'].reverse()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_wrong_actor_or_repository_is_rejected(self):
        self.api.responses['actions/runs/500']['triggering_actor']['id'] = 11
        with self.assertRaises(QualificationError):
            self.verify()
        self.api.responses['actions/runs/500']['triggering_actor']['id'] = 10
        self.api.repository = 'owner/other'
        with self.assertRaises(QualificationError):
            self.verify()

    def test_duplicate_jobs_or_false_numeric_ids_are_rejected(self):
        jobs = self.api.responses['actions/runs/500/attempts/1/jobs']['jobs']
        jobs[2]['id'] = jobs[1]['id']
        with self.assertRaises(QualificationError):
            self.verify()
        jobs[2]['id'] = 702
        with self.assertRaises(QualificationError):
            verify_qualification(self.api, self.policy, self.signed, artifact_id=True,
                                 archive=self.archive, admission_archive=self.admission_archive)

    def test_artifact_and_check_times_must_belong_to_their_jobs(self):
        artifact = self.api.responses['actions/artifacts/900']
        artifact['created_at'] = '2026-10-30T00:00:30Z'
        with self.assertRaises(QualificationError):
            self.verify()
        artifact['created_at'] = '2026-10-30T00:29:59Z'
        self.api.responses['check-runs/800']['completed_at'] = '2026-10-30T00:29:59Z'
        with self.assertRaises(QualificationError):
            self.verify()

    def test_last_run_read_rejects_a_rerun_started_during_verification(self):
        original_get = self.api.get
        reads = []

        def get(resource, **parameters):
            result = original_get(resource, **parameters)
            if resource == 'actions/runs/500':
                reads.append(resource)
                if len(reads) == 2:
                    result['run_attempt'] = 2
            return result

        self.api.get = get
        with self.assertRaises(QualificationError):
            self.verify()
        self.assertEqual(len(reads), 2)

    def test_native_logs_are_rechecked_instead_of_trusting_success(self):
        controller = self.report['controller']
        value = decode(controller['wire'], controller['nonce'])
        value['files']['LastTest.log'] += '\nSKIP : KWin::OffscreenQuickViewTest::testComposition()\n'
        controller['wire'] = encode(controller['nonce'], value['files'], value['metadata'])
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_missing_outer_evidence_is_rejected(self):
        original = copy.deepcopy(self.report)
        for field in ['runtime', 'container', 'volume', 'engine_security_options']:
            with self.subTest(field=field):
                self.report = copy.deepcopy(original)
                del self.report[field]
                self.archive = self.pack()
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_unsafe_outer_confinement_is_rejected(self):
        original = copy.deepcopy(self.report)
        for field, value in [('Privileged', True), ('ReadonlyRootfs', False),
                             ('NetworkMode', 'host'), ('Binds', ['/host:/work']),
                             ('IpcMode', 'host'), ('PidMode', 'host'), ('UTSMode', 'host'),
                             ('CgroupnsMode', 'host'), ('CapAdd', ['SYS_ADMIN']),
                             ('CapDrop', []), ('SecurityOpt', ['seccomp=unconfined']),
                             ('Devices', []), ('DeviceRequests', [{'Count': -1}]),
                             ('DeviceCgroupRules', ['a *:* rwm']), ('GroupAdd', ['0']),
                             ('AutoRemove', False), ('RestartPolicy', {'Name': 'always'}),
                             ('Memory', 0), ('MemorySwap', 0), ('PidsLimit', 0), ('NanoCpus', 0)]:
            with self.subTest(field=field):
                self.report = copy.deepcopy(original)
                self.report['runtime']['HostConfig'][field] = value
                self.report['container']['host_config'][field] = value
                self.archive = self.pack()
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_outer_identity_mounts_and_command_are_rechecked(self):
        original = copy.deepcopy(self.report)
        for section, field, value in [
                ('Config', 'User', '0:0'), ('Config', 'Image', 'other-image'),
                ('Config', 'Entrypoint', ['/bin/sh']), ('Config', 'Cmd', ['other']),
                (None, 'Image', 'sha256:' + '0' * 64),
                (None, 'AppArmorProfile', 'unconfined'),
                (None, 'Mounts', [{'Type': 'bind', 'Source': '/host', 'Destination': '/work'}])]:
            with self.subTest(section=section, field=field):
                self.report = copy.deepcopy(original)
                target = self.report['runtime'] if section is None else self.report['runtime'][section]
                target[field] = value
                self.archive = self.pack()
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_outer_volume_cannot_hide_a_host_bind(self):
        self.report['volume']['Options'] = {'type': 'none', 'device': '/host', 'o': 'bind'}
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_source_and_input_manifest_cannot_differ_from_admission(self):
        self.report['input_manifest_sha256'] = '9' * 64
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_worker_cannot_replace_the_previously_published_manifest_binding(self):
        original_archive = self.admission_archive
        original_digest = self.api.responses['actions/artifacts/901']['digest']
        self.receipt['evidence']['input_manifest_sha256'] = '9' * 64
        self.report['input_manifest_sha256'] = '9' * 64
        self.report['controller']['input_manifest_sha256'] = '9' * 64
        self.archive = self.pack()
        self.admission_archive = original_archive
        self.api.responses['actions/artifacts/901']['digest'] = original_digest
        with self.assertRaisesRegex(QualificationError, 'independently published admission'):
            self.verify()

    def test_admission_cannot_be_created_after_worker_start(self):
        self.api.responses['actions/artifacts/901']['created_at'] = '2026-10-30T00:10:00Z'
        with self.assertRaises(QualificationError):
            self.verify()

    def test_native_nonce_must_match_the_preissued_identity(self):
        self.receipt['identity']['execution']['nonce'] = '7' * 32
        self.archive = self.pack()
        with self.assertRaisesRegex(QualificationError, 'pre-issued nonce'):
            self.verify()

    def test_admission_artifact_digest_and_run_are_independent_requirements(self):
        self.api.responses['actions/artifacts/901']['digest'] = 'sha256:' + '9' * 64
        with self.assertRaises(QualificationError):
            self.verify()
        self.archive = self.pack()
        self.api.responses['actions/artifacts/901']['workflow_run']['id'] = 501
        with self.assertRaises(QualificationError):
            self.verify()

    def test_admission_comparison_does_not_coerce_true_to_one(self):
        identity = copy.deepcopy(self.receipt['identity'])
        identity['execution']['run_attempt'] = True
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('admission.json', json.dumps({
                'identity': identity, 'qualification_id': self.receipt['qualification_id'],
                'input_manifest_sha256': '4' * 64}))
        self.admission_archive = stream.getvalue()
        self.api.responses['actions/artifacts/901']['digest'] = (
            'sha256:' + hashlib.sha256(self.admission_archive).hexdigest())
        with self.assertRaises(QualificationError):
            self.verify()

    def test_native_source_comparison_preserves_numeric_types(self):
        self.report['source'] = dict(self.report['source'], bytes=4096.0)
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_wrong_schema_and_missing_fields_are_refused(self):
        self.receipt['identity']['schema_version'] = True
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()
        self.receipt['identity']['schema_version'] = 1
        del self.receipt['identity']['execution']['run_id']
        self.archive = self.pack()
        with self.assertRaises(QualificationError):
            self.verify()

    def test_execution_identity_is_not_taken_from_an_artifact_without_validation(self):
        original = copy.deepcopy(self.receipt)
        for field, value in [('run_attempt', 2), ('run_attempt', True), ('run_number', '12'),
                             ('workflow_id', 201), ('launcher', '9' * 40)]:
            with self.subTest(field=field, value=value):
                self.receipt = copy.deepcopy(original)
                self.receipt['identity']['execution'][field] = value
                self.archive = self.pack()
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_signer_policy_is_independent_of_artifact_fields(self):
        self.signed = SignedMerge(self.signed.commit, self.signed.tree, 'B' * 40,
                                  self.signed.base, self.signed.head)
        with self.assertRaises(QualificationError):
            self.verify()

    def test_producer_attested_fork_needs_current_api_authentication(self):
        self.receipt['identity']['admission']['head_repository_id'] = 102
        self.archive = self.pack()
        historical = self.verify()
        self.assertEqual(historical.producer_attestation.head_repository_id, 102)
        self.api.responses['pulls/7'] = {
            'number': 7, 'state': 'open', 'draft': False, 'merged': False, 'mergeable': True,
            'labels': [{'name': 'native-approved'}], 'merge_commit_sha': 'f' * 40,
            'base': {'ref': 'main', 'sha': 'b' * 40,
                     'repo': {'id': 100, 'full_name': 'owner/downstream'}},
            'head': {'sha': 'c' * 40, 'repo': {'id': 101}},
        }
        for resource, ref, sha in [('git/ref/heads/main', 'refs/heads/main', 'b' * 40),
                                   ('git/ref/pull/7/head', 'refs/pull/7/head', 'c' * 40),
                                   ('git/ref/pull/7/merge', 'refs/pull/7/merge', 'f' * 40)]:
            self.api.responses[resource] = {'ref': ref, 'object': {'type': 'commit', 'sha': sha}}
        with self.assertRaises(QualificationError):
            verify_current_association(self.api, self.policy, historical)

    def test_corrupt_archive_with_matching_digest_is_still_rejected(self):
        self.archive = b'not a zip'
        self.api.responses['actions/artifacts/900']['digest'] = (
            'sha256:' + hashlib.sha256(self.archive).hexdigest())
        with self.assertRaises(QualificationError):
            self.verify()

    def test_invalid_policy_ids_fail_before_any_requests(self):
        with self.assertRaises(QualificationError):
            QualificationPolicy('owner/downstream', True, 200, 'a' * 40, 'A' * 40,
                                ('fixture-package',), app_id=300)
        self.assertEqual(self.api.calls, [])

    def test_digest_is_verified_before_reading_malformed_archive(self):
        self.archive = b'not a zip'
        with self.assertRaisesRegex(QualificationError, 'digest'):
            self.verify()

    def test_nonregular_or_unexpected_archive_members_are_refused(self):
        for filename, mode in [('report.json', stat.S_IFLNK | 0o777), ('../report.json', 0o100644)]:
            with self.subTest(filename=filename):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w') as archive:
                    archive.writestr('qualification.json', json.dumps(self.receipt))
                    info = zipfile.ZipInfo(filename)
                    info.external_attr = mode << 16
                    archive.writestr(info, json.dumps(self.report))
                self.archive = stream.getvalue()
                self.api.responses['actions/artifacts/900']['digest'] = (
                    'sha256:' + hashlib.sha256(self.archive).hexdigest())
                with self.assertRaises(QualificationError):
                    self.verify()


if __name__ == '__main__':
    unittest.main()
