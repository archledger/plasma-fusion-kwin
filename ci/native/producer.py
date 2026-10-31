# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The four trusted-native jobs. Candidate bytes never supply executable code."""

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from urllib.request import build_opener

from ci.hypervisor.boot_guest import GUEST_SHA256
from ci.native.check_journal import CheckJournal
from ci.native.check_records import CheckIdentity, select_check, validate_check
from ci.native.checks import CheckReporter
from ci.native.coordination import read_signed_coordination
from ci.native.current_candidate import verify_admission_association
from ci.native.merge_update import MergeCommit, read_raw_object
from ci.native.pagination import get_all
from ci.native.proof_api import NoRedirect, ProofAPI, object_pairs
from ci.native.qualification import (
    MAX_RECEIPT_BYTES,
    MAX_REPORT_BYTES,
    CheckBinding,
    QualificationPolicy,
    canonical_json,
    digest,
    identifier,
    match_admission_v2,
    require,
    timestamp,
    validate_admission_v2,
    validate_receipt_v2,
    verify_native,
)
from ci.native.signed_merge import verify_signed_merge
from ci.native.worker_claim import WorkerClaim
from tools.native_inputs import (
    BUILDER_CONFIG,
    BUILDER_REFERENCE,
    prepare_image,
    regular_digest,
)
from tools.patchset import fetch_source, load_manifest
from tools.probe_hosted_boot import LABEL, owned_container, validate_volume
from tools.probe_live_worker import (
    FINGERPRINT,
    PUBLIC_KEY_SHA256,
    SIGNOFF,
    upload,
    wait_next,
)
from tools.run_native_guest import pack_inputs, run_guest
from tools.verify_native_proof import handled_interruptions

REPOSITORY = 'archledger/plasma-fusion-kwin'
ENDPOINT = 'https://github.com/' + REPOSITORY + '.git'
COORDINATION_REF = 'refs/heads/native-gate-coordination'
SOURCE_LIMIT = 128 * 1024**2


def read_files(raw, expected_digest, limits):
    require(
        type(raw) is bytes and len(raw) <= sum(limits.values()) + 1024**2,
        'artifact archive exceeds bounds',
    )
    require(
        expected_digest == 'sha256:' + hashlib.sha256(raw).hexdigest(),
        'artifact digest differs',
    )
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(
            len(infos) == len(limits) and {i.filename for i in infos} == set(limits),
            'artifact members differ',
        )
        for entry in infos:
            mode = entry.external_attr >> 16
            require(
                not entry.is_dir()
                and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                and not entry.flag_bits & 1
                and 0 <= entry.file_size <= limits[entry.filename]
                and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                'unsafe artifact member',
            )
            data = archive.read(entry)
            require(len(data) == entry.file_size, 'artifact length differs')
            result[entry.filename] = data
    return result


def source_matches(raw, expected):
    require(
        len(raw) == expected['bytes']
        and hashlib.sha256(raw).hexdigest() == expected['sha256'],
        'prepared source differs from admission',
    )


def _json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs)


class Context:
    """Trusted job context, constructed from runtime identity and signed state."""

    def __init__(self):
        require(
            os.environ['GITHUB_REPOSITORY'] == REPOSITORY
            and os.environ['GITHUB_REF'] == 'refs/heads/main'
            and os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
            and os.environ['GITHUB_RUN_ATTEMPT'] == '1',
            'not a trusted main-branch invocation',
        )
        self.root = Path(os.environ['GITHUB_WORKSPACE']).resolve()
        self.work = Path(os.environ['RUNNER_TEMP']) / 'trusted-native'
        self.work.mkdir(mode=0o700, exist_ok=True)
        self.run_id = identifier(int(os.environ['GITHUB_RUN_ID']))
        self.api = ProofAPI(REPOSITORY, token=os.environ.get('GH_TOKEN'), max_calls=64)
        run = self.api.get(f'actions/runs/{self.run_id}')
        self.policy = QualificationPolicy(
            REPOSITORY,
            1439058578,
            identifier(run['workflow_id']),
            digest(os.environ['GITHUB_SHA']),
            FINGERPRINT,
            tuple(
                line
                for line in (self.root / 'ci/builder/packages.lock')
                .read_text()
                .splitlines()
                if line and not line.startswith('#')
            ),
        )
        self.execution = {
            'workflow_id': self.policy.workflow_id,
            'run_id': self.run_id,
            'run_number': identifier(int(os.environ['GITHUB_RUN_NUMBER'])),
            'run_attempt': 1,
            'launcher': self.policy.launcher,
        }
        self.keyring = self.work / 'keys'
        self.keyring.mkdir(mode=0o700, exist_ok=True)
        with build_opener(NoRedirect()).open(
            'https://github.com/archledger.gpg', timeout=30
        ) as response:
            key = response.read(65537)
        require(
            hashlib.sha256(key).hexdigest() == PUBLIC_KEY_SHA256,
            'public signing key differs',
        )
        keyfile = self.work / 'public.asc'
        keyfile.write_bytes(key)
        subprocess.run(
            [
                'gpg',
                '--no-options',
                '--homedir',
                str(self.keyring),
                '--batch',
                '--no-autostart',
                '--no-auto-check-trustdb',
                '--import',
                str(keyfile),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        self.objects = self.work / 'objects.git'
        self.git('init', '--bare', '-q', str(self.objects))
        sha = digest(os.environ['COORDINATION_SHA'])
        self.fetch(sha)
        self.initial = read_signed_coordination(
            self.objects,
            sha,
            policy=self.policy,
            keyring=self.keyring,
            signoff=SIGNOFF,
            expected_parent=digest(os.environ['PARENT_SHA']),
            expected_generation=int(os.environ['EXPECTED_GENERATION']),
            expected_epoch=os.environ['EPOCH'],
            expected_cutover_digest=os.environ['CUTOVER_DIGEST'],
        )
        require(
            self.initial.record['state'] == 'dispatching', 'not a dispatched operation'
        )
        self.admission = self.initial.record['operation']['admission']
        self.execution['nonce'] = self.initial.record['operation']['nonce']
        self.identity = {
            'schema_version': 1,
            'admission': self.admission,
            'execution': self.execution,
        }
        self.qid = hashlib.sha256(canonical_json(self.identity)).hexdigest()
        self.verify_run(run)
        target = MergeCommit(
            self.admission['base'],
            self.admission['head'],
            self.admission['signed_merge'],
            self.admission['tree'],
        )
        self.fetch(target.commit)
        self.signed = verify_signed_merge(
            self.objects,
            target,
            keyring=self.keyring,
            fingerprint=self.policy.fingerprint,
            signoff=SIGNOFF,
        )
        self.current()
        self.association()

    def git(self, *args):
        env = {
            'PATH': os.environ['PATH'],
            'HOME': str(self.work),
            'LANG': 'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_TERMINAL_PROMPT': '0',
        }
        return (
            subprocess.check_output(
                ['git', '--git-dir=' + str(self.objects), *args],
                env=env,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            .decode()
            .strip()
        )

    def fetch(self, sha):
        self.git('fetch', '--no-tags', '--depth=3', ENDPOINT, digest(sha))

    def verify_run(self, run):
        require(
            run['repository']['id'] == self.policy.repository_id
            and run['repository']['full_name'] == REPOSITORY
            and run['id'] == self.run_id
            and run['head_repository']['id'] == self.policy.repository_id
            and run['head_branch'] == 'main'
            and run['head_sha'] == self.policy.launcher
            and run['path'] == self.policy.workflow_path
            and run['event'] == 'workflow_dispatch'
            and type(run['run_attempt']) is int
            and run['run_attempt'] == 1
            and run['status'] == 'in_progress'
            and run['conclusion'] is None
            and run['run_number'] == self.execution['run_number']
            and run['actor']['id']
            == run['triggering_actor']['id']
            == self.admission['approver_id'],
            'workflow identity differs',
        )
        permission = self.api.get(
            'collaborators/' + run['actor']['login'] + '/permission'
        )
        require(
            permission['user']['id'] == self.admission['approver_id']
            and permission['permission'] in {'write', 'maintain', 'admin'},
            'approver no longer authorized',
        )

    def current(self):
        observed = self.git('ls-remote', '--refs', ENDPOINT, COORDINATION_REF).split()
        require(
            len(observed) == 2 and observed[1] == COORDINATION_REF,
            'coordination ref unavailable',
        )
        tip = digest(observed[0])
        if tip == self.initial.commit:
            return self.initial
        chain = []
        cursor = tip
        for _ in range(2):
            self.fetch(cursor)
            raw = read_raw_object(self.objects, cursor, 'commit', limit=65536).decode()
            parents = re.findall(
                r'^parent ([0-9a-f]{40})$', raw.partition('\n\n')[0], re.MULTILINE
            )
            require(len(parents) == 1, 'invalid coordination parent')
            chain.append(cursor)
            cursor = parents[0]
            if cursor == self.initial.commit:
                break
        require(
            cursor == self.initial.commit, 'coordination no longer belongs to this job'
        )
        previous = self.initial
        for sha in reversed(chain):
            current = read_signed_coordination(
                self.objects,
                sha,
                policy=self.policy,
                keyring=self.keyring,
                signoff=SIGNOFF,
                expected_parent=previous.commit,
                expected_generation=previous.record['generation'] + 1,
                expected_epoch=self.initial.record['epoch'],
                expected_cutover_digest=self.initial.record['cutover_digest'],
            )
            require(
                current.record['state']
                == ('granted' if previous == self.initial else 'running')
                and canonical_json(current.record['operation'])
                == canonical_json(self.initial.record['operation']),
                'coordination transition differs',
            )
            grant = current.record['grant']
            require(
                grant['run_id'] == self.run_id
                and grant['run_number'] == self.execution['run_number']
                and grant['qualification_id'] == self.qid,
                'coordination grant differs',
            )
            previous = current
        return previous

    def association(self):
        verify_admission_association(
            self.api, self.policy, self.admission, self.signed, self.qid
        )
        merge = self.api.get('git/commits/' + self.admission['merge'])
        require(
            merge['tree']['sha'] == self.signed.tree
            and [p['sha'] for p in merge['parents']]
            == [self.signed.base, self.signed.head],
            'merge changed',
        )

    def jobs(self):
        jobs = get_all(
            self.api, f'actions/runs/{self.run_id}/attempts/1/jobs', key='jobs'
        )
        require(
            0 < len(jobs) <= 4
            and len({j['name'] for j in jobs}) == len(jobs)
            and len({identifier(j['id']) for j in jobs}) == len(jobs)
            and {j['name'] for j in jobs} <= {'prepare', 'admit', 'native', 'report'},
            'unexpected job graph',
        )
        for job in jobs:
            identifier(job['id'])
            require(
                job['run_id'] == self.run_id
                and type(job['run_attempt']) is int
                and job['run_attempt'] == 1
                and job['head_sha'] == self.policy.launcher,
                'job identity differs',
            )
        return {j['name']: j for j in jobs}

    def artifact(self, name, limits, role):
        artifacts = get_all(
            self.api, f'actions/runs/{self.run_id}/artifacts', key='artifacts'
        )
        matches = [a for a in artifacts if a['name'] == name]
        require(len(matches) == 1, 'missing or duplicate producer artifact')
        artifact = matches[0]
        association = artifact['workflow_run']
        require(
            type(artifact['size_in_bytes']) is int
            and 0 < artifact['size_in_bytes'] <= sum(limits.values()) + 1024**2,
            'artifact download exceeds bounds',
        )
        require(
            artifact['expired'] is False
            and association['id'] == self.run_id
            and association['repository_id'] == self.policy.repository_id
            and association['head_repository_id'] == self.policy.repository_id
            and association['head_sha'] == self.policy.launcher,
            'artifact association differs',
        )
        job = self.jobs()[role]
        require(
            job['status'] == 'completed'
            and job['conclusion'] == 'success'
            and timestamp(job['started_at'])
            <= timestamp(artifact['created_at'])
            <= timestamp(job['completed_at']),
            'artifact is not from its successful producer job',
        )
        env = {
            'PATH': os.environ['PATH'],
            'HOME': os.environ['HOME'],
            'GH_TOKEN': os.environ['GH_TOKEN'],
        }
        result = subprocess.run(
            [
                'gh',
                'api',
                f'repos/{REPOSITORY}/actions/artifacts/{identifier(artifact["id"])}/zip',
            ],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        require(result.returncode == 0, 'artifact download failed')
        return read_files(result.stdout, artifact['digest'], limits), artifact


def candidate_data(ctx):
    """Copy only bounded manifest/patch blobs; never checkout candidate code."""
    ctx.fetch(ctx.signed.base)
    changed = ctx.git(
        'diff', '--name-only', ctx.signed.base, ctx.signed.commit
    ).splitlines()
    require(
        changed
        and all(
            p == 'patchset.json' or re.fullmatch(r'patches/[A-Za-z0-9_.-]+\.patch', p)
            for p in changed
        ),
        'candidate changes the trusted control plane',
    )
    records = ctx.git(
        'ls-tree', '-r', ctx.signed.commit, '--', 'patchset.json', 'patches'
    ).splitlines()
    require(0 < len(records) <= 33, 'too many candidate files')
    root = ctx.work / 'candidate'
    root.mkdir(mode=0o700)
    digests = []
    total = 0
    for record in records:
        match = re.fullmatch(
            r'100644 blob ([0-9a-f]{40})\t(patchset\.json|patches/[A-Za-z0-9_.-]+\.patch)',
            record,
        )
        require(match is not None, 'unsafe candidate file')
        raw = read_raw_object(ctx.objects, match[1], 'blob', limit=4 * 1024**2)
        total += len(raw)
        require(total <= 32 * 1024**2, 'candidate exceeds bounds')
        target = root / match[2]
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
        digests.append({'path': match[2], 'sha256': hashlib.sha256(raw).hexdigest()})
    manifest = load_manifest(root)
    require(
        manifest.commit == ctx.policy.upstream_commit, 'unsupported upstream revision'
    )
    require(
        {d['path'] for d in digests}
        == {'patchset.json', *[p.filename for p in manifest.patches]},
        'extra candidate files',
    )
    require(
        hashlib.sha256(
            canonical_json(sorted(digests, key=lambda d: d['path']))
        ).hexdigest()
        == ctx.admission['payload_sha256'],
        'candidate payload digest differs',
    )
    return root, manifest


def validate_preparer(info, name, token, volume):
    owned_container(info, name, token)
    host = info['HostConfig']
    require(
        info['Image'] == 'sha256:' + BUILDER_CONFIG
        and info['Config']['Image'] == BUILDER_REFERENCE
        and info['Config']['User'] == '1000:1000'
        and info['AppArmorProfile'] == 'docker-default'
        and host['ReadonlyRootfs'] is True
        and host['Privileged'] is False
        and host['NetworkMode'] == 'none'
        and not host['Binds']
        and not host['Devices']
        and not host.get('DeviceRequests')
        and host['CapDrop'] == ['ALL']
        and not host['CapAdd']
        and host['Memory'] == host['MemorySwap'] == 2 * 1024**3
        and host['PidsLimit'] == 256
        and host['NanoCpus'] == 2 * 10**9
        and host['CgroupnsMode'] == 'private'
        and host['PidMode'] in {'', 'private'}
        and host['IpcMode'] == 'private'
        and host['UTSMode'] in {'', 'private'}
        and host['RestartPolicy']['Name'] == 'no'
        and any(
            option in {'no-new-privileges', 'no-new-privileges=true'}
            for option in host['SecurityOpt']
        )
        and not any(option == 'seccomp=unconfined' for option in host['SecurityOpt']),
        'preparer confinement differs',
    )
    mounts = info['Mounts']
    require(
        len(mounts) == 1
        and mounts[0]['Type'] == 'volume'
        and mounts[0]['Name'] == volume
        and mounts[0]['Destination'] == '/work'
        and mounts[0]['RW'] is True,
        'preparer has an unexpected mount',
    )


def isolated_source(ctx, candidate, manifest):
    """Apply candidate data only in a no-device/no-network constrained container."""
    upstream = ctx.work / 'upstream'
    upstream.mkdir()
    fetch_source(upstream, manifest)
    name = 'pf-prepare-' + uuid.uuid4().hex
    token = name.removeprefix('pf-prepare-')
    volume = name + '-data'
    env = {
        'PATH': os.environ['PATH'],
        'HOME': str(ctx.work),
        'DOCKER_CONFIG': str(ctx.work),
        'LANG': 'C.UTF-8',
    }
    (ctx.work / 'config.json').write_text('{"auths":{}}\n')

    def docker(*args, data=None, timeout=300):
        return subprocess.run(
            ['docker', *args],
            input=data,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=True,
        ).stdout

    created = False
    volume_created = False
    try:
        docker('pull', '--quiet', BUILDER_REFERENCE)
        require(
            docker('image', 'inspect', '--format={{.Id}}', BUILDER_REFERENCE)
            .decode()
            .strip()
            == 'sha256:' + BUILDER_CONFIG,
            'preparer image differs',
        )
        docker(
            'volume',
            'create',
            '--driver=local',
            '--label=' + LABEL + '=' + token,
            volume,
        )
        volume_created = True
        code = (
            "import json,sys;from pathlib import Path;sys.path.insert(0,'/work/payload/control');"
            'from tools.patchset import load_manifest,apply_patchset;'
            'from tools.native_inputs import archive_tree;'
            "m=load_manifest(Path('/work/payload/candidate'));"
            "apply_patchset(Path('/work/payload/upstream'),m);"
            "s=archive_tree(Path('/work/payload/upstream'),m.expected_tree,Path('/work/payload/out/source.tar'));"
            "Path('/work/payload/out/source.json').write_text(json.dumps(s))"
        )
        docker(
            'create',
            '--name=' + name,
            '--label=' + LABEL + '=' + token,
            '--pull=never',
            '--network=none',
            '--read-only',
            '--user=1000:1000',
            '--cap-drop=ALL',
            '--security-opt=no-new-privileges',
            '--security-opt=apparmor=docker-default',
            '--pids-limit=256',
            '--cpus=2',
            '--cgroupns=private',
            '--ipc=private',
            '--restart=no',
            '--memory=2g',
            '--memory-swap=2g',
            '--tmpfs=/tmp:rw,nosuid,nodev',
            '--mount=type=volume,src=' + volume + ',dst=/work',
            '--env=HOME=/tmp',
            '--env=GIT_CONFIG_NOSYSTEM=1',
            '--env=GIT_CONFIG_GLOBAL=/dev/null',
            '--entrypoint=python3',
            BUILDER_REFERENCE,
            '-I',
            '-B',
            '-c',
            code,
        )
        created = True
        info = json.loads(docker('inspect', name))[0]
        validate_preparer(info, name, token, volume)
        with tempfile.TemporaryFile(dir=ctx.work) as archive:
            with tarfile.open(fileobj=archive, mode='w') as tar:

                def normalized(info):
                    info.uid = info.gid = 1000
                    info.uname = info.gname = ''
                    return info

                for directory in [
                    'payload',
                    'payload/control',
                    'payload/control/tools',
                    'payload/out',
                ]:
                    info = tarfile.TarInfo(directory)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o700
                    normalized(info)
                    tar.addfile(info)
                tar.add(upstream, arcname='payload/upstream', filter=normalized)
                tar.add(candidate, arcname='payload/candidate', filter=normalized)
                for path in [
                    'tools/__init__.py',
                    'tools/patchset.py',
                    'tools/native_inputs.py',
                ]:
                    tar.add(
                        ctx.root / path,
                        arcname='payload/control/' + path,
                        filter=normalized,
                    )
            archive.seek(0)
            subprocess.run(
                ['docker', 'cp', '--archive', '-', name + ':/work'],
                stdin=archive,
                env=env,
                capture_output=True,
                check=True,
                timeout=300,
            )
        docker('start', '--attach', name, timeout=300)
        raw = docker('cp', name + ':/work/payload/out/.', '-')
        require(len(raw) <= SOURCE_LIMIT + 1024**2, 'preparer output exceeds bounds')
        files = {}
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for index, member in enumerate(archive):
                require(index < 8, 'too many preparer output members')
                if member.isdir():
                    continue
                key = member.name.removeprefix('./')
                require(
                    member.isfile()
                    and key in {'source.tar', 'source.json'}
                    and key not in files
                    and 0
                    < member.size
                    <= (SOURCE_LIMIT if key == 'source.tar' else 4096),
                    'unsafe preparer output',
                )
                files[key] = archive.extractfile(member).read()
        require(
            set(files) == {'source.tar', 'source.json'}, 'preparer output incomplete'
        )
        require(
            canonical_json(_json(files['source.json']))
            == canonical_json(ctx.admission['source']),
            'preparer source identity differs',
        )
        source_matches(files['source.tar'], ctx.admission['source'])
        return files['source.tar']
    finally:
        if created:
            info = json.loads(docker('inspect', name))[0]
            owned_container(info, name, token)
            docker('rm', '--force', name)
        if volume_created:
            info = json.loads(docker('volume', 'inspect', volume))[0]
            validate_volume(info, volume, token)
            docker('volume', 'rm', volume)


def prepared_inputs(ctx, files):
    metadata = _json(files['prepared.json'])
    require(
        canonical_json(metadata['identity']) == canonical_json(ctx.identity)
        and metadata['qualification_id'] == ctx.qid,
        'prepared identity differs',
    )
    source_matches(files['patched-source.tar'], ctx.admission['source'])
    inputs = _json(files['inputs.json'])
    require(
        canonical_json(inputs['source']) == canonical_json(ctx.admission['source']),
        'prepared source metadata differs',
    )
    directory = ctx.work / 'inputs'
    directory.mkdir(mode=0o700)
    for name in ['patched-source.tar', 'inputs.json']:
        (directory / name).write_bytes(files[name])
    actual = prepare_image(directory)
    require(
        canonical_json(inputs['builder']) == canonical_json(actual),
        'prepared builder differs',
    )
    return directory, metadata


def prepare_job(ctx):
    require(ctx.current().commit == ctx.initial.commit, 'operation already advanced')
    candidate, manifest = candidate_data(ctx)
    source = isolated_source(ctx, candidate, manifest)
    directory = ctx.work / 'prepared'
    directory.mkdir(mode=0o700)
    (directory / 'patched-source.tar').write_bytes(source)
    image = prepare_image(directory)
    inputs = {
        'source': ctx.admission['source'],
        'builder': image,
        'patchset': {
            'upstream': {
                'repository': 'https://invent.kde.org/plasma/kwin.git',
                'release': manifest.release,
                'commit': manifest.commit,
            },
            'result_tree': manifest.expected_tree,
        },
    }
    (directory / 'inputs.json').write_bytes(canonical_json(inputs))
    with tempfile.TemporaryFile(dir=ctx.work) as archive:
        _, _, manifest_sha = pack_inputs(
            ctx.root,
            directory,
            None,
            archive,
            expected_source=ctx.admission['source'],
            invocation={'nonce': ctx.execution['nonce'], 'qualification_id': ctx.qid},
        )
    metadata = {
        'identity': ctx.identity,
        'qualification_id': ctx.qid,
        'input_manifest_sha256': manifest_sha,
    }
    (directory / 'prepared.json').write_bytes(canonical_json(metadata))
    ctx.association()
    require(
        ctx.current().commit == ctx.initial.commit,
        'operation changed during preparation',
    )


def check_record(ctx, head, name, check_id, *, conclusion=None):
    """Require the admitted ID directly and in the bounded visible inventory."""
    expected = CheckIdentity(
        ctx.policy.repository, ctx.policy.app_id, head, name, ctx.qid, ctx.run_id
    )
    status = 'in_progress' if conclusion is None else 'completed'
    value = ctx.api.get(f'check-runs/{identifier(check_id)}')
    validate_check(value, expected, check_id, status=status, conclusion=conclusion)
    checks = get_all(
        ctx.api, f'commits/{head}/check-runs', key='check_runs', check_filter='all'
    )
    select_check(checks, expected, check_id, status=status, conclusion=conclusion)
    return value


def check_pair(ctx, binding, *, conclusion=None):
    require(type(binding) is CheckBinding, 'missing admitted check pair')
    return {
        'native': check_record(
            ctx,
            ctx.signed.commit,
            ctx.policy.check_name,
            binding.native_check_id,
            conclusion=conclusion,
        ),
        'activation': check_record(
            ctx,
            ctx.signed.head,
            f'Native KWin / activation #{ctx.admission["pr_number"]}',
            binding.activation_check_id,
            conclusion=conclusion,
        ),
    }


def published_admission(ctx):
    """Only the authenticated successful admit job can supply the bound pair."""
    files, artifact = ctx.artifact(
        f'native-admission-{ctx.run_id}-1',
        {'admission.json': MAX_RECEIPT_BYTES},
        'admit',
    )
    value = _json(files['admission.json'])
    binding = validate_admission_v2(value, ctx.policy, ctx.signed)
    require(
        canonical_json(value['identity']) == canonical_json(ctx.identity)
        and value['qualification_id'] == ctx.qid,
        'published admission differs',
    )
    return value, binding, artifact


def prepared_admission(ctx):
    """Read the same authenticated preparation contract for each consuming job."""
    files, _ = ctx.artifact(
        f'native-prepared-{ctx.run_id}-1',
        {
            'prepared.json': MAX_RECEIPT_BYTES,
            'inputs.json': MAX_RECEIPT_BYTES,
            'patched-source.tar': SOURCE_LIMIT,
        },
        'prepare',
    )
    metadata = _json(files['prepared.json'])
    source_matches(files['patched-source.tar'], ctx.admission['source'])
    require(
        canonical_json(metadata['identity']) == canonical_json(ctx.identity)
        and metadata['qualification_id'] == ctx.qid,
        'preparation admission differs',
    )
    digest(metadata['input_manifest_sha256'], 64)
    return files, metadata


def request_job(ctx):
    """Unprivileged prelude of admit; no App mutation before a run-bound grant."""
    prepared_admission(ctx)
    require(ctx.current().commit == ctx.initial.commit, 'operation already granted')
    (ctx.work / 'request.json').write_bytes(
        canonical_json(
            {'coordination_sha': ctx.initial.commit, 'identity': ctx.identity}
        )
    )


def admit_job(ctx):
    _, metadata = prepared_admission(ctx)
    os.environ['STATE_REF'] = COORDINATION_REF
    granted = wait_next(
        ctx.objects,
        ctx.initial,
        'granted',
        ctx.policy,
        ctx.keyring,
        time.monotonic() + 180,
    )
    require(
        granted.record['grant']['run_id'] == ctx.run_id
        and granted.record['grant']['qualification_id'] == ctx.qid,
        'another workflow received the grant',
    )

    def fence():
        ctx.verify_run(ctx.api.get(f'actions/runs/{ctx.run_id}'))
        ctx.association()
        require(
            ctx.current().commit == granted.commit,
            'admission no longer owns coordination',
        )

    journal = CheckJournal(
        ctx.work / 'admit-journal.json',
        qualification_id=ctx.qid,
        run_id=ctx.run_id,
        nonce=ctx.execution['nonce'],
        phase='admit',
    )
    reporter = CheckReporter.from_key(
        ctx.policy,
        os.environ['NATIVE_GATE_PRIVATE_KEY'],
        ctx.run_id,
        fence,
        journal=journal,
    )
    activation_id = reporter.begin(
        ctx.signed.head,
        f'Native KWin / activation #{ctx.admission["pr_number"]}',
        ctx.qid,
    )
    native_id = reporter.begin(ctx.signed.commit, ctx.policy.check_name, ctx.qid)
    binding = CheckBinding(native_id, activation_id)
    check_pair(ctx, binding)
    value = dict(
        metadata,
        schema_version=2,
        checks={'native_check_id': native_id, 'activation_check_id': activation_id},
    )
    validate_admission_v2(value, ctx.policy, ctx.signed)
    fence()
    (ctx.work / 'admission.json').write_bytes(canonical_json(value))


def native_job(ctx):
    files, metadata = prepared_admission(ctx)
    admission, binding, admission_artifact = published_admission(ctx)
    require(
        canonical_json(admission)
        == canonical_json(dict(metadata, schema_version=2, checks=admission['checks'])),
        'published admission differs',
    )
    check_pair(ctx, binding)
    directory, metadata = prepared_inputs(ctx, files)
    job = ctx.jobs()['native']
    require(job['status'] == 'in_progress', 'native job is not active')
    worker = WorkerClaim(ctx.work, ctx.policy, run_id=ctx.run_id, job_id=job['id'])
    os.environ['STATE_REF'] = COORDINATION_REF
    deadline = time.monotonic() + 180
    granted = ctx.current()
    require(granted.record['state'] == 'granted', 'native worker is not granted')
    require(
        granted.record['grant']['qualification_id'] == ctx.qid, 'grant identity differs'
    )
    claim = {
        'coordination_sha': granted.commit,
        'identity': ctx.identity,
        'native_job_id': job['id'],
        'worker_nonce': worker.nonce,
    }
    upload('claim', claim, ctx.work, deadline)
    running = wait_next(
        ctx.objects, granted, 'running', ctx.policy, ctx.keyring, deadline
    )
    worker.accept(running)
    require(ctx.current().commit == running.commit, 'claim no longer current')
    ctx.association()
    base = ctx.work / 'base.qcow2'
    subprocess.run(
        [
            'curl',
            '--proto',
            '=https',
            '--tlsv1.2',
            '--fail',
            '--location',
            '--max-time',
            '60',
            '--max-filesize',
            '600000000',
            '--output',
            str(base),
            'https://paducahix.mm.fcix.net/fedora/linux/releases/44/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2',
        ],
        check=True,
        timeout=70,
        env={'PATH': os.environ['PATH'], 'HOME': str(ctx.work)},
    )
    require(
        regular_digest(base, 600000000)[0] == GUEST_SHA256, 'guest base image differs'
    )
    output = ctx.work / 'native'
    output.mkdir(mode=0o700)
    elapsed = time.time() - float(os.environ['NATIVE_JOB_STARTED'])
    budget = min(4200, int(4500 - elapsed - 90))
    require(budget > 0, 'job budget exhausted')
    ctx.verify_run(ctx.api.get(f'actions/runs/{ctx.run_id}'))
    require(
        ctx.current().commit == running.commit, 'claim changed before native execution'
    )
    check_pair(ctx, binding)
    with handled_interruptions():
        report = run_guest(
            ctx.root,
            directory,
            base,
            output,
            'native',
            budget,
            qualification_id=ctx.qid,
            nonce=ctx.execution['nonce'],
            expected_input_manifest=metadata['input_manifest_sha256'],
            expected_source=ctx.admission['source'],
        )
    jobs = ctx.jobs()
    receipt = {
        'schema_version': 2,
        'identity': ctx.identity,
        'qualification_id': ctx.qid,
        'evidence': {
            'prepare_job_id': jobs['prepare']['id'],
            'admission_job_id': jobs['admit']['id'],
            'native_job_id': job['id'],
            'admission_artifact_id': admission_artifact['id'],
            'check_run_id': binding.native_check_id,
            'activation_check_run_id': binding.activation_check_id,
            'input_manifest_sha256': metadata['input_manifest_sha256'],
        },
    }
    result = ctx.work / 'result'
    result.mkdir(mode=0o700)
    (result / 'qualification.json').write_bytes(canonical_json(receipt))
    (result / 'report.json').write_bytes(canonical_json(report))
    print(json.dumps({'success': report['success'], 'qualification_id': ctx.qid}))
    return report['success']


def report_job(ctx):
    current = ctx.current()
    require(
        current.record['state'] in {'dispatching', 'granted', 'running'},
        'reporter lost coordination',
    )
    approved, binding, artifact = published_admission(ctx)
    check_pair(ctx, binding)
    success = False
    try:
        files, _ = ctx.artifact(
            f'native-qualification-{ctx.run_id}-1',
            {'qualification.json': MAX_RECEIPT_BYTES, 'report.json': MAX_REPORT_BYTES},
            'native',
        )
        receipt, report = (
            _json(files['qualification.json']),
            _json(files['report.json']),
        )
        admission, execution, evidence = validate_receipt_v2(
            receipt, ctx.policy, ctx.signed
        )
        require(
            canonical_json(admission) == canonical_json(ctx.admission)
            and canonical_json(execution) == canonical_json(ctx.execution),
            'native receipt identity differs',
        )
        require(
            artifact['id'] == evidence['admission_artifact_id']
            and match_admission_v2(approved, receipt, ctx.policy, ctx.signed)
            == binding,
            'native admission differs',
        )
        jobs = ctx.jobs()
        for role, field in [
            ('prepare', 'prepare_job_id'),
            ('admit', 'admission_job_id'),
            ('native', 'native_job_id'),
        ]:
            require(evidence[field] == jobs[role]['id'], 'native job binding differs')
        require(
            current.record['state'] == 'running'
            and current.record['grant']['native_job_id'] == jobs['native']['id'],
            'wrong native claim',
        )
        verify_native(report, receipt, ctx.policy)
        success = True
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ):
        success = False

    def fence():
        ctx.verify_run(ctx.api.get(f'actions/runs/{ctx.run_id}'))
        ctx.association()
        require(ctx.current().commit == current.commit, 'reporter coordination changed')

    journal = CheckJournal(
        ctx.work / 'report-journal.json',
        qualification_id=ctx.qid,
        run_id=ctx.run_id,
        nonce=ctx.execution['nonce'],
        phase='report',
    )
    reporter = CheckReporter.from_key(
        ctx.policy,
        os.environ['NATIVE_GATE_PRIVATE_KEY'],
        ctx.run_id,
        fence,
        journal=journal,
    )
    pointer_name = f'Native KWin / activation #{ctx.admission["pr_number"]}'
    reporter.complete(
        binding.native_check_id,
        ctx.signed.commit,
        ctx.policy.check_name,
        ctx.qid,
        success=success,
    )
    reporter.complete(
        binding.activation_check_id,
        ctx.signed.head,
        pointer_name,
        ctx.qid,
        success=success,
    )
    check_pair(ctx, binding, conclusion='success' if success else 'failure')
    require(success, 'native qualification failed or could not be verified')
