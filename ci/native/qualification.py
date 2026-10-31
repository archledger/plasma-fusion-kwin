# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bind historical admission and native evidence to authenticated API records.

Policy, the API client and SignedMerge must be supplied by trusted caller code.
This does not revalidate current PR state, reviews or protection, and cannot push.
The pinned workflow must issue admission itself and upload fresh worker evidence;
checksums or JSON fields supplied by an untrusted workflow are not authority.
"""
import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from ci.native.proof_api import (
    bounded_integer,
    finite_float,
    object_pairs,
    reject_constant,
)
from ci.native.signed_merge import SignedMerge
from ci.native.transport import decode
from ci.native.verify import verify_envelope
from tools.probe_hosted_boot import validate_runtime, validate_volume
from tools.run_native_guest import BOOTSTRAP, CONFIG, IMAGE, verify_controller

MAX_ARCHIVE_BYTES = 32 * 1024**2
MAX_REPORT_BYTES = 64 * 1024**2
MAX_RECEIPT_BYTES = 64 * 1024


class QualificationError(ValueError):
    """Qualification provenance or native evidence did not meet trusted policy."""


@dataclass(frozen=True)
class QualificationPolicy:
    repository: str
    repository_id: int
    workflow_id: int
    launcher: str
    fingerprint: str
    packages: tuple[str, ...]
    app_id: int = 5544780
    workflow_path: str = '.github/workflows/trusted-native.yml'
    check_name: str = 'Native KWin / trusted'
    upstream_commit: str = 'ab7df7ccb7c6af20f4b279cd6220f7cd3d2267d7'

    def __post_init__(self):
        require(type(self.repository) is str and re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', self.repository)
            and self.repository.split('/')[1] not in {'.', '..'}, 'invalid trusted repository')
        require(type(self.workflow_path) is str and re.fullmatch(
            r'\.github/workflows/[A-Za-z0-9_-]+\.ya?ml', self.workflow_path), 'invalid trusted workflow')
        require(type(self.check_name) is str and 0 < len(self.check_name) <= 100
                and all(ord(char) >= 32 and ord(char) != 127 for char in self.check_name),
                'invalid trusted check name')
        for value in (self.repository_id, self.workflow_id, self.app_id):
            identifier(value)
        digest(self.launcher)
        digest(self.upstream_commit)
        require(type(self.fingerprint) is str and
                re.fullmatch(r'(?:[A-F0-9]{40}|[A-F0-9]{64})', self.fingerprint),
                'invalid trusted signer')
        require(type(self.packages) is tuple and 0 < len(self.packages) <= 2048
                and all(type(package) is str and 0 < len(package) <= 256 for package in self.packages)
                and len(set(self.packages)) == len(self.packages), 'invalid trusted package inventory')


@dataclass(frozen=True)
class ProducerAttestedInputs:
    """Issuer-recorded metadata; PR/fork/ref associations need separate API verification."""
    pr_number: int
    head_repository_id: int
    synthetic_merge: str
    payload_sha256: str
    source_tree: str
    source_sha256: str
    source_bytes: int
    approver_id: int
    upstream_commit: str


@dataclass(frozen=True)
class QualifiedEvidence:
    qualification_id: str
    run_id: int
    signed_merge: SignedMerge
    producer_attestation: ProducerAttestedInputs
    tests: dict
    check_run_id: int
    run_number: int


@dataclass(frozen=True)
class CheckBinding:
    """Validated shape only; the containing artifact needs authentication."""
    native_check_id: int
    activation_check_id: int

    def __post_init__(self):
        identifier(self.native_check_id)
        identifier(self.activation_check_id)
        require(self.native_check_id != self.activation_check_id, 'check roles share an ID')


@dataclass(frozen=True)
class QualifiedEvidenceV2(QualifiedEvidence):
    """Carry v2 bindings; trusted callers must authenticate the whole proof."""
    activation_check_id: int
    operation_nonce: str
    native_job_id: int

    def __post_init__(self):
        CheckBinding(self.check_run_id, self.activation_check_id)
        digest(self.operation_nonce, 32)
        identifier(self.native_job_id)


def require(condition, message):
    if not condition:
        raise QualificationError(message)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'invalid receipt fields')


def identifier(value):
    require(type(value) is int and 0 < value < 2**63, 'invalid numeric identity')
    return value


def digest(value, length=40):
    require(type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value)
            and value != '0' * length, 'invalid object digest')
    return value


def timestamp(value):
    require(type(value) is str and value.endswith('Z'), 'invalid evidence timestamp')
    return datetime.fromisoformat(value[:-1] + '+00:00')


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read_archive(archive, artifact, limits):
    require(type(archive) is bytes and 0 < len(archive) <= MAX_ARCHIVE_BYTES,
            'proof archive exceeds bounds')
    require(artifact['digest'] == 'sha256:' + hashlib.sha256(archive).hexdigest(),
            'artifact digest differs')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        require(len(entries) == len(limits) and {entry.filename for entry in entries} == set(limits),
                'unexpected or duplicate proof archive members')
        values = {}
        for entry in entries:
            mode = entry.external_attr >> 16
            require(not entry.is_dir() and not stat.S_ISLNK(mode)
                    and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                    and not entry.flag_bits & 1
                    and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    and 0 < entry.file_size <= limits[entry.filename], 'unsafe proof archive member')
            with zipped.open(entry) as member:
                raw = member.read(limits[entry.filename] + 1)
            require(len(raw) == entry.file_size, 'proof member length differs')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs,
                               parse_constant=reject_constant, parse_int=bounded_integer,
                               parse_float=finite_float)
            require(type(value) is dict, 'proof member must be an object')
            values[entry.filename] = value
    return values


def validate_admission(admission):
    """Validate the shared immutable admission shape without granting authority."""
    keys(admission, ['repository_id', 'pr_number', 'head_repository_id', 'base', 'head',
                     'merge', 'signed_merge', 'tree', 'payload_sha256', 'source', 'approver_id',
                     'upstream_commit'])
    for field in ['repository_id', 'pr_number', 'head_repository_id', 'approver_id']:
        identifier(admission[field])
    for field in ['base', 'head', 'merge', 'signed_merge', 'tree', 'upstream_commit']:
        digest(admission[field])
    digest(admission['payload_sha256'], 64)
    keys(admission['source'], ['tree', 'sha256', 'bytes'])
    digest(admission['source']['tree'])
    digest(admission['source']['sha256'], 64)
    require(identifier(admission['source']['bytes']) <= 128 * 1024**2, 'source archive too large')


def _validate_identity(identity, qualification_id, policy, signed):
    keys(identity, ['schema_version', 'admission', 'execution'])
    require(type(identity['schema_version']) is int and identity['schema_version'] == 1,
            'unsupported qualification schema')
    admission, execution = identity['admission'], identity['execution']
    validate_admission(admission)
    keys(execution, ['workflow_id', 'run_id', 'run_attempt', 'run_number', 'launcher', 'nonce'])
    for field in ['workflow_id', 'run_id', 'run_attempt', 'run_number']:
        identifier(execution[field])
    digest(execution['launcher'])
    digest(execution['nonce'], 32)
    require(qualification_id == hashlib.sha256(canonical_json(identity)).hexdigest(),
            'qualification identity differs')
    require(admission['repository_id'] == policy.repository_id
            and admission['upstream_commit'] == policy.upstream_commit
            and execution['workflow_id'] == policy.workflow_id
            and execution['launcher'] == policy.launcher
            and execution['run_attempt'] == 1, 'qualification is outside trusted policy')
    require(type(signed) is SignedMerge and signed.fingerprint == policy.fingerprint
            and (signed.commit, signed.tree, signed.base, signed.head)
            == tuple(admission[field] for field in ['signed_merge', 'tree', 'base', 'head']),
            'admission does not match the verified signed merge')
    return admission, execution


def _validate_receipt_evidence(evidence, *, version):
    extra = ['activation_check_run_id'] if version == 2 else []
    fields = ['prepare_job_id', 'admission_job_id', 'native_job_id',
              'admission_artifact_id', 'check_run_id', 'input_manifest_sha256']
    keys(evidence, fields + extra)
    for field in ['prepare_job_id', 'admission_job_id', 'native_job_id',
                  'admission_artifact_id', 'check_run_id'] + extra:
        identifier(evidence[field])
    digest(evidence['input_manifest_sha256'], 64)
    if version == 2:
        return CheckBinding(evidence['check_run_id'], evidence['activation_check_run_id'])


def validate_receipt(receipt, policy, signed):
    """Legacy receipt shape; never a fallback for the explicit v2 validators."""
    keys(receipt, ['identity', 'qualification_id', 'evidence'])
    admission, execution = _validate_identity(receipt['identity'], receipt['qualification_id'],
                                              policy, signed)
    _validate_receipt_evidence(receipt['evidence'], version=1)
    return admission, execution, receipt['evidence']


def validate_admission_v2(value, policy, signed):
    """Validate v2 structure, not the emitter, job, artifact or current authority."""
    try:
        keys(value, ['schema_version', 'identity', 'qualification_id',
                     'input_manifest_sha256', 'checks'])
        require(type(value['schema_version']) is int and value['schema_version'] == 2,
                'unsupported admission envelope')
        _validate_identity(value['identity'], value['qualification_id'], policy, signed)
        digest(value['input_manifest_sha256'], 64)
        keys(value['checks'], ['native_check_id', 'activation_check_id'])
        return CheckBinding(**value['checks'])
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise QualificationError('invalid v2 admission structure') from error


def validate_receipt_v2(receipt, policy, signed):
    """Explicit v2 envelope; preserve the existing identity hash and proof fields."""
    try:
        keys(receipt, ['schema_version', 'identity', 'qualification_id', 'evidence'])
        require(type(receipt['schema_version']) is int and receipt['schema_version'] == 2,
                'unsupported qualification envelope')
        admission, execution = _validate_identity(receipt['identity'], receipt['qualification_id'],
                                                  policy, signed)
        _validate_receipt_evidence(receipt['evidence'], version=2)
        return admission, execution, receipt['evidence']
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise QualificationError('invalid v2 qualification structure') from error


def match_admission_v2(admission_record, receipt, policy, signed):
    """Match two validated structures; callers must authenticate both artifacts."""
    binding = validate_admission_v2(admission_record, policy, signed)
    _, _, evidence = validate_receipt_v2(receipt, policy, signed)
    require(binding == CheckBinding(evidence['check_run_id'], evidence['activation_check_run_id'])
            and canonical_json(admission_record['identity']) == canonical_json(receipt['identity'])
            and admission_record['qualification_id'] == receipt['qualification_id']
            and admission_record['input_manifest_sha256'] == evidence['input_manifest_sha256'],
            'qualification differs from v2 admission')
    return binding


def verify_run(run, policy, execution):
    require(identifier(run['id']) == execution['run_id']
            and identifier(run['workflow_id']) == policy.workflow_id
            and run['path'] == policy.workflow_path
            and identifier(run['repository']['id']) == policy.repository_id
            and run['repository']['full_name'] == policy.repository
            and identifier(run['head_repository']['id']) == policy.repository_id
            and run['head_branch'] == 'main' and run['head_sha'] == policy.launcher
            and run['event'] == 'workflow_dispatch' and run['status'] == 'completed'
            and run['conclusion'] == 'success' and type(run['run_attempt']) is int
            and run['run_attempt'] == 1 and identifier(run['run_number']) == execution['run_number'],
            'untrusted, incomplete or rerun qualification workflow')


def verify_native(report, receipt, policy):
    admission, evidence = receipt['identity']['admission'], receipt['evidence']
    require(report['qualification_id'] == receipt['qualification_id']
            and canonical_json(report['source']) == canonical_json(admission['source'])
            and report['input_manifest_sha256'] == evidence['input_manifest_sha256']
            and report['mode'] == 'native' and report['image'] == IMAGE and report['image_id'] == CONFIG
            and 'error' not in report, 'native result belongs to different inputs')
    require(all(report[field] is True for field in ['success', 'container_removed', 'volume_removed',
                                                  'cleanup_confirmed', 'device_metadata_unchanged'])
            and type(report['controller_exit']) is int and report['controller_exit'] == 0,
            'native execution or cleanup failed')
    device = report['host_device_before']
    keys(device, ['uid', 'gid', 'mode', 'major', 'minor'])
    require(all(type(value) is int and value >= 0 for value in device.values())
            and (device['major'], device['minor']) == (10, 232)
            and canonical_json(device) == canonical_json(report['host_device_after']),
            'host device evidence differs')
    security = report['engine_security_options']
    require(type(security) is list and 'name=apparmor' in security
            and 'name=seccomp,profile=builtin' in security, 'engine confinement differs')
    runtime = report['runtime']
    name = runtime['Name'].removeprefix('/')
    require(re.fullmatch(r'pf-native-[0-9a-f]{32}', name), 'invalid runtime ownership name')
    token = name.removeprefix('pf-native-')
    volume = 'pf-native-data-' + token
    validate_volume(report['volume'], volume, token)
    containment = validate_runtime(runtime, name, token, volume, IMAGE, CONFIG, device['gid'])
    require(canonical_json(containment) == canonical_json(report['container']),
            'outer containment summary differs')
    require(runtime['Config']['Entrypoint'] == ['/usr/bin/python3']
            and runtime['Config']['Cmd'] == ['-I', '-B', '-c', BOOTSTRAP, 'native'],
            'outer launcher command differs')
    controller = report['controller']
    require(controller['nonce'] == receipt['identity']['execution']['nonce'],
            'native result belongs to a different pre-issued nonce')
    verify_controller(controller, 'native', evidence['input_manifest_sha256'])
    value = decode(controller['wire'], controller['nonce'])
    require(canonical_json(json.loads(value['files']['source.json'], object_pairs_hook=object_pairs))
            == canonical_json(admission['source']), 'guest source receipt differs')
    measured = verify_envelope(value, 'native', {'source': admission['source']}, policy.packages)
    if 'verification' in report:
        require(canonical_json(report['verification']) == canonical_json(measured),
                'native summary contradicts measured evidence')
    return measured['tests']


def verify_qualification(api, policy, signed, *, artifact_id, archive, admission_archive):
    """Verify a completed historical qualification, not current merge authorization.

    The caller must run this under a wall-clock deadline and supply SignedMerge
    from verify_signed_merge(), never deserialize it from an untrusted receipt.
    """
    try:
        require(api.repository == policy.repository, 'wrong API repository')
        artifact = api.get('actions/artifacts/' + str(identifier(artifact_id)))
        require(identifier(artifact['id']) == artifact_id and artifact['expired'] is False,
                'missing or expired qualification artifact')
        files = read_archive(archive, artifact,
                             {'qualification.json': MAX_RECEIPT_BYTES, 'report.json': MAX_REPORT_BYTES})
        receipt, report = files['qualification.json'], files['report.json']
        admission, execution, evidence = validate_receipt(receipt, policy, signed)
        run_id = execution['run_id']
        association = artifact['workflow_run']
        require(identifier(association['id']) == run_id
                and identifier(association['repository_id']) == policy.repository_id
                and identifier(association['head_repository_id']) == policy.repository_id
                and association['head_sha'] == policy.launcher
                and artifact['name'] == f'native-qualification-{run_id}-1', 'wrong artifact provenance')
        approval_artifact = api.get('actions/artifacts/' + str(evidence['admission_artifact_id']))
        approval_association = approval_artifact['workflow_run']
        require(identifier(approval_artifact['id']) == evidence['admission_artifact_id']
                and evidence['admission_artifact_id'] != artifact_id
                and approval_artifact['expired'] is False
                and approval_artifact['name'] == f'native-admission-{run_id}-1'
                and identifier(approval_association['id']) == run_id
                and identifier(approval_association['repository_id']) == policy.repository_id
                and identifier(approval_association['head_repository_id']) == policy.repository_id
                and approval_association['head_sha'] == policy.launcher, 'wrong admission provenance')
        approval = read_archive(admission_archive, approval_artifact,
                                {'admission.json': MAX_RECEIPT_BYTES})['admission.json']
        require(canonical_json(approval) == canonical_json({
                    'identity': receipt['identity'],
                    'qualification_id': receipt['qualification_id'],
                    'input_manifest_sha256': evidence['input_manifest_sha256']}),
                'worker receipt differs from independently published admission')
        run = api.get(f'actions/runs/{run_id}')
        verify_run(run, policy, execution)
        actor = run['actor']
        require(identifier(actor['id']) == admission['approver_id']
                == identifier(run['triggering_actor']['id']), 'approval actor differs')
        permission = api.get('collaborators/' + actor['login'] + '/permission')
        require(identifier(permission['user']['id']) == admission['approver_id']
                and permission['permission'] in {'admin', 'maintain', 'write'}, 'admission revoked')
        inventory = api.get(f'actions/runs/{run_id}/attempts/1/jobs', page=1, per_page=100)
        jobs = inventory['jobs']
        require(type(jobs) is list and type(inventory['total_count']) is int
                and inventory['total_count'] == len(jobs) == 4, 'incomplete or unexpected job inventory')
        require(len({identifier(job['id']) for job in jobs}) == 4, 'duplicate qualification job')
        require({job['name'] for job in jobs} == {'prepare', 'admit', 'native', 'report'},
                'unexpected qualification jobs')
        by_id = {job['id']: job for job in jobs}
        by_name = {job['name']: job for job in jobs}
        ordered = []
        for role, field in [('prepare', 'prepare_job_id'), ('admit', 'admission_job_id'),
                             ('native', 'native_job_id'), ('report', None)]:
            # The native artifact exists before the report job may be allocated.
            job = by_name['report'] if field is None else by_id[evidence[field]]
            require(job['name'] == role and identifier(job['run_id']) == run_id
                    and type(job['run_attempt']) is int and job['run_attempt'] == 1
                    and job['head_sha'] == policy.launcher and job['status'] == 'completed'
                    and job['conclusion'] == 'success', 'job was not trusted same-attempt execution')
            times = timestamp(job['started_at']), timestamp(job['completed_at'])
            require(times[0] <= times[1], 'invalid job interval')
            ordered.append(times)
        require(all(left[1] <= right[0] for left, right in pairwise(ordered))
                and ordered[1][0] <= timestamp(approval_artifact['created_at']) <= ordered[1][1]
                and ordered[2][0] <= timestamp(artifact['created_at']) <= ordered[2][1],
                'artifact or qualification jobs are out of order')
        merge = api.get('git/commits/' + admission['merge'])
        require(merge['sha'] == admission['merge'] and merge['tree']['sha'] == signed.tree
                and [parent['sha'] for parent in merge['parents']] == [signed.base, signed.head],
                'GitHub merge differs from signed candidate')
        check = api.get('check-runs/' + str(evidence['check_run_id']))
        require(identifier(check['id']) == evidence['check_run_id']
                and identifier(check['app']['id']) == policy.app_id
                and check['name'] == policy.check_name and check['head_sha'] == signed.commit
                and check['external_id'] == receipt['qualification_id']
                and check['status'] == 'completed' and check['conclusion'] == 'success'
                and check['details_url'] == f'https://github.com/{policy.repository}/actions/runs/{run_id}'
                and ordered[3][0] <= timestamp(check['completed_at']) <= ordered[3][1],
                'native App check does not match qualification')
        tests = verify_native(report, receipt, policy)
        verify_run(api.get(f'actions/runs/{run_id}'), policy, execution)
        inputs = admission['source']
        admitted = ProducerAttestedInputs(admission['pr_number'], admission['head_repository_id'],
                                          admission['merge'], admission['payload_sha256'], inputs['tree'],
                                          inputs['sha256'], inputs['bytes'], admission['approver_id'],
                                          admission['upstream_commit'])
        return QualifiedEvidence(receipt['qualification_id'], run_id, signed, admitted, tests,
                                 evidence['check_run_id'], execution['run_number'])
    except QualificationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, zipfile.BadZipFile,
            RuntimeError, EOFError, OSError, zlib.error):
        raise QualificationError('invalid or unavailable qualification evidence') from None
