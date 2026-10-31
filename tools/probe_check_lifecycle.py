# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual, main-only App contract probe on a disposable validation ref."""

import copy
import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

from ci.native.check_journal import CheckJournal
from ci.native.check_records import CheckIdentity, select_check, validate_check
from ci.native.checks import CheckReporter, check_error_details
from ci.native.pagination import get_all
from ci.native.proof_api import ProofAPI
from ci.native.qualification import (
    canonical_json,
    digest,
    identifier,
    keys,
    read_archive,
    require,
    timestamp,
)

REPOSITORY = 'archledger/plasma-fusion-kwin'
REPOSITORY_ID = 1439058578
APP_ID = 5544780
WORKFLOW = '.github/workflows/check-lifecycle-probe.yml'
AUTHORITY_REF = 'refs/heads/native-gate-coordination'
PROBE_REF = 'refs/heads/validation/check-lifecycle-contract'
MAX_REPORT_BYTES = 64 * 1024
MAX_DOWNLOAD_BYTES = MAX_REPORT_BYTES + 1024**2
NAMES = {
    'native': 'Validation / check lifecycle native',
    'activation': 'Validation / check lifecycle activation',
}
SCOPE = 'disposable App contract only; no native qualification'
CHECK_FIELDS = (
    'id',
    'name',
    'head_sha',
    'status',
    'conclusion',
    'completed_at',
    'external_id',
    'details_url',
    'app',
)


def verify_run(api, run_id, launcher, *, completed=False):
    run = api.get(f'actions/runs/{run_id}')
    require(
        identifier(run['id']) == identifier(run_id)
        and run['repository']['id'] == REPOSITORY_ID
        and run['repository']['full_name'] == REPOSITORY
        and run['head_repository']['id'] == REPOSITORY_ID
        and run['head_repository']['full_name'] == REPOSITORY
        and run['head_sha'] == launcher
        and run['head_branch'] == 'main'
        and run['path'] == WORKFLOW
        and run['event'] == 'workflow_dispatch'
        and type(run['run_attempt']) is int
        and run['run_attempt'] == 1
        and run['status'] == ('completed' if completed else 'in_progress')
        and run['conclusion'] == ('success' if completed else None)
        and identifier(run['actor']['id']) == identifier(run['triggering_actor']['id']),
        'probe run identity differs',
    )
    identifier(run['workflow_id'])
    permission = api.get('collaborators/' + run['actor']['login'] + '/permission')
    require(
        permission['user']['id'] == run['actor']['id']
        and permission['permission'] in {'write', 'maintain', 'admin'},
        'probe actor is not authorized',
    )
    return run


def runtime_context():
    require(
        os.environ['GITHUB_ACTIONS'] == 'true'
        and os.environ['GITHUB_REPOSITORY'] == REPOSITORY
        and os.environ['GITHUB_REF'] == 'refs/heads/main'
        and os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
        and os.environ['GITHUB_RUN_ATTEMPT'] == '1',
        'probe must run once from main',
    )
    run_id = identifier(int(os.environ['GITHUB_RUN_ID']))
    launcher = digest(os.environ['GITHUB_SHA'])
    authority = digest(os.environ['CHECK_COORDINATION_SHA'])
    api = ProofAPI(REPOSITORY, token=os.environ['GH_TOKEN'], max_calls=32)
    verify_run(api, run_id, launcher)
    return api, run_id, launcher, authority


def read_refs(work):
    result = subprocess.run(
        [
            'git',
            '--no-replace-objects',
            'ls-remote',
            '--refs',
            f'https://github.com/{REPOSITORY}.git',
            'refs/heads/main',
            AUTHORITY_REF,
            PROBE_REF,
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env={
            'PATH': os.defpath,
            'HOME': str(work),
            'LANG': 'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_TERMINAL_PROMPT': '0',
        },
    )
    rows = [line.split() for line in result.stdout.splitlines()]
    require(
        len(rows) == 3 and all(len(row) == 2 for row in rows), 'probe refs unavailable'
    )
    refs = {row[1]: digest(row[0]) for row in rows}
    require(
        set(refs) == {'refs/heads/main', AUTHORITY_REF, PROBE_REF},
        'probe ref inventory differs',
    )
    return refs


def check_fence(api, run_id, launcher, authority, head, *, work):
    verify_run(api, run_id, launcher)
    refs = read_refs(work)
    require(
        refs['refs/heads/main'] == launcher and refs[AUTHORITY_REF] == authority,
        'main or held coordination changed',
    )
    require(
        refs[PROBE_REF] != launcher and (head is None or refs[PROBE_REF] == head),
        'disposable probe head changed or equals main',
    )
    return refs[PROBE_REF]


def probe_qid(run_id, head, nonce):
    return hashlib.sha256(
        canonical_json(
            {
                'probe_run': identifier(run_id),
                'head': digest(head),
                'nonce': digest(nonce, 32),
            }
        )
    ).hexdigest()


def snapshot(value):
    return {
        field: ({'id': value['app']['id']} if field == 'app' else value[field])
        for field in CHECK_FIELDS
    }


def verify_report(report):
    """Validate success evidence shape, never authenticate an artifact by itself."""
    keys(
        report,
        [
            'schema_version',
            'scope',
            'stage',
            'run_id',
            'launcher',
            'authority',
            'head',
            'nonce',
            'qualification_id',
            'outcome',
            'success',
            'checks',
            'records',
        ],
    )
    require(
        type(report['schema_version']) is int
        and report['schema_version'] == 2
        and report['scope'] == SCOPE
        and report['success'] is True
        and report['stage'] in {'A', 'B', 'C'},
        'invalid probe report',
    )
    for field in ['launcher', 'authority', 'head']:
        digest(report[field])
    count = ord(report['stage']) - ord('A') + 1
    records = report['records']
    require(type(records) is list and len(records) == count, 'probe history differs')
    seen_runs, seen_checks, seen_qids = set(), set(), set()
    for i, record in enumerate(records):
        keys(
            record,
            ['run_id', 'nonce', 'qualification_id', 'outcome', 'native', 'activation'],
        )
        run_id = identifier(record['run_id'])
        qid = probe_qid(run_id, report['head'], record['nonce'])
        require(
            qid == record['qualification_id']
            and qid not in seen_qids
            and run_id not in seen_runs
            and record['outcome'] == ('success' if i == 1 else 'failure'),
            'probe operation history differs',
        )
        seen_runs.add(run_id)
        seen_qids.add(qid)
        for role, name in NAMES.items():
            value = record[role]
            keys(value, CHECK_FIELDS)
            keys(value['app'], ['id'])
            check_id = identifier(value['id'])
            require(check_id not in seen_checks, 'probe history reuses a check')
            seen_checks.add(check_id)
            validate_check(
                value,
                CheckIdentity(REPOSITORY, APP_ID, report['head'], name, qid, run_id),
                check_id,
                status='completed',
                conclusion=record['outcome'],
            )
    last = records[-1]
    require(
        all(
            report[k] == last[k]
            for k in ['run_id', 'nonce', 'qualification_id', 'outcome']
        ),
        'probe report current operation differs',
    )
    keys(report['checks'], ['native_check_id', 'activation_check_id'])
    require(
        all(
            identifier(report['checks'][role + '_check_id']) == last[role]['id']
            for role in NAMES
        ),
        'probe report pair differs',
    )
    return report


def observe_history(client, records):
    for record in records:
        for role in NAMES:
            expected = record[role]
            actual = snapshot(client._send('GET', f'check-runs/{expected["id"]}'))
            require(actual == expected, 'previous validation check changed')


def download_artifact(artifact_id):
    """Read at most the archive cap plus one byte, within one process deadline."""
    process = subprocess.Popen(
        [
            'gh',
            'api',
            f'repos/{REPOSITORY}/actions/artifacts/{identifier(artifact_id)}/zip',
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env={
            'PATH': os.defpath,
            'HOME': os.environ['HOME'],
            'GH_TOKEN': os.environ['GH_TOKEN'],
        },
    )
    deadline = time.monotonic() + 60
    raw = bytearray()
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                require(
                    remaining > 0 and ready.select(remaining),
                    'artifact download timed out',
                )
                chunk = os.read(
                    process.stdout.fileno(),
                    min(65536, MAX_DOWNLOAD_BYTES + 1 - len(raw)),
                )
                if not chunk:
                    break
                raw.extend(chunk)
                require(
                    len(raw) <= MAX_DOWNLOAD_BYTES, 'artifact download exceeds bounds'
                )
        require(
            process.wait(timeout=max(0, deadline - time.monotonic())) == 0,
            'artifact download failed',
        )
        return bytes(raw)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        raise
    finally:
        process.stdout.close()


def load_predecessor(api, stage, previous_id, run_id, launcher, authority, head):
    """Accept only the exact completed same-source run's digest-bound artifact."""
    require(type(stage) is str and stage in {'A', 'B', 'C'}, 'invalid probe stage')
    require(type(previous_id) is str, 'invalid predecessor run')
    if stage == 'A':
        require(previous_id == '', 'first stage cannot have a predecessor')
        return None
    previous = identifier(int(previous_id))
    require(
        str(previous) == previous_id and previous != run_id, 'invalid predecessor run'
    )
    current = verify_run(api, run_id, launcher)
    old = verify_run(api, previous, launcher, completed=True)
    require(
        old['workflow_id'] == current['workflow_id'], 'predecessor workflow differs'
    )
    jobs = get_all(api, f'actions/runs/{previous}/attempts/1/jobs', key='jobs')
    require(len(jobs) == 1, 'predecessor job graph differs')
    job = jobs[0]
    identifier(job['id'])
    require(
        job['name'] == 'probe'
        and identifier(job['run_id']) == previous
        and type(job['run_attempt']) is int
        and job['run_attempt'] == 1
        and job['head_sha'] == launcher
        and job['status'] == 'completed'
        and job['conclusion'] == 'success',
        'predecessor job differs',
    )
    artifacts = get_all(api, f'actions/runs/{previous}/artifacts', key='artifacts')
    matches = [a for a in artifacts if a['name'] == f'check-lifecycle-{previous}-1']
    require(len(matches) == 1, 'predecessor artifact missing or duplicated')
    artifact = matches[0]
    association = artifact['workflow_run']
    require(
        artifact['expired'] is False
        and type(artifact['size_in_bytes']) is int
        and 0 < artifact['size_in_bytes'] <= MAX_DOWNLOAD_BYTES
        and identifier(association['id']) == previous
        and identifier(association['repository_id']) == REPOSITORY_ID
        and identifier(association['head_repository_id']) == REPOSITORY_ID
        and association['head_sha'] == launcher
        and timestamp(job['started_at'])
        <= timestamp(artifact['created_at'])
        <= timestamp(job['completed_at'])
        <= timestamp(current['created_at']),
        'predecessor artifact association or time differs',
    )
    archive = download_artifact(artifact['id'])
    report = read_archive(archive, artifact, {'report.json': MAX_REPORT_BYTES})[
        'report.json'
    ]
    verify_report(report)
    require(
        report['run_id'] == previous
        and report['stage'] == chr(ord(stage) - 1)
        and (report['launcher'], report['authority'], report['head'])
        == (launcher, authority, head),
        'predecessor report differs',
    )
    return report


def observe_pair(client, head, qid, checks, *, conclusion=None):
    records = get_all(
        client.reader,
        f'commits/{head}/check-runs',
        key='check_runs',
        check_filter='all',
    )
    result = {}
    for role, name in NAMES.items():
        check_id = checks[role + '_check_id']
        expected = CheckIdentity(REPOSITORY, APP_ID, head, name, qid, client.run_id)
        status = 'in_progress' if conclusion is None else 'completed'
        value = select_check(
            records, expected, check_id, status=status, conclusion=conclusion
        )
        direct = client._send('GET', f'check-runs/{check_id}')
        validate_check(direct, expected, check_id, status=status, conclusion=conclusion)
        require(snapshot(value) == snapshot(direct), 'probe pair changed between reads')
        result[role] = snapshot(direct)
    return result


def exercise(
    client,
    head,
    run_id,
    save,
    *,
    stage,
    launcher,
    authority,
    nonce,
    report_journal,
    previous=None,
):
    """One actual run creates/completes one pair; history is authenticated by main."""
    require(
        stage in {'A', 'B', 'C'} and client.run_id == identifier(run_id),
        'invalid probe stage or run',
    )
    records = []
    if stage == 'A':
        require(previous is None, 'first stage cannot adopt predecessor evidence')
    else:
        verify_report(previous)
        require(
            previous['stage'] == chr(ord(stage) - 1)
            and previous['run_id'] != run_id
            and (previous['launcher'], previous['authority'], previous['head'])
            == (launcher, authority, head),
            'probe predecessor differs',
        )
        records = copy.deepcopy(previous['records'])
    qid = probe_qid(run_id, head, nonce)
    report = {
        'schema_version': 2,
        'scope': SCOPE,
        'stage': stage,
        'run_id': run_id,
        'launcher': launcher,
        'authority': authority,
        'head': head,
        'nonce': nonce,
        'qualification_id': qid,
        'outcome': 'success' if stage == 'B' else 'failure',
        'success': False,
        'checks': {},
        'records': records,
    }
    phase = 'history'
    try:
        observe_history(client, records)
        save(report)
        for role, name in NAMES.items():
            phase = 'create-' + role
            report['checks'][role + '_check_id'] = client.begin(head, name, qid)
            save(report)
        phase = 'pending-pair'
        observe_pair(client, head, qid, report['checks'])
        observe_history(client, records)
        client.journal = report_journal
        for role, name in NAMES.items():
            phase = 'complete-' + role
            client.complete(
                report['checks'][role + '_check_id'],
                head,
                name,
                qid,
                success=report['outcome'] == 'success',
            )
        phase = 'terminal-pair'
        pair = observe_pair(
            client, head, qid, report['checks'], conclusion=report['outcome']
        )
        observe_history(client, records)
        report['records'].append(
            dict(
                run_id=run_id,
                nonce=nonce,
                qualification_id=qid,
                outcome=report['outcome'],
                **pair,
            )
        )
        report['success'] = True
        verify_report(report)
        save(report)
        return report
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        report.update(
            failed_phase=phase,
            error_type=type(error).__name__,
            diagnostics=check_error_details(error),
        )
        save(report)
        raise


def write_report(path, value):
    raw = (json.dumps(value, indent=2) + '\n').encode()
    require(len(raw) <= MAX_REPORT_BYTES, 'probe report exceeds size limit')
    path.write_bytes(raw)


def main():
    output = None
    phase = 'context'
    try:
        api, run_id, launcher, authority = runtime_context()
        work = Path(os.environ['RUNNER_TEMP']) / 'check-lifecycle-probe'
        work.mkdir(mode=0o700)
        output = work / 'report.json'
        phase = 'preflight'
        head = check_fence(api, run_id, launcher, authority, None, work=work)
        stage = os.environ['CHECK_PROBE_STAGE']
        phase = 'predecessor'
        previous = load_predecessor(
            api,
            stage,
            os.environ['CHECK_PREDECESSOR_RUN'],
            run_id,
            launcher,
            authority,
            head,
        )
        nonce = uuid.uuid4().hex
        qid = probe_qid(run_id, head, nonce)
        journals = {
            phase: CheckJournal(
                work / (phase + '-journal.json'),
                qualification_id=qid,
                run_id=run_id,
                nonce=nonce,
                phase=phase,
            )
            for phase in ['admit', 'report']
        }

        def fence():
            check_fence(api, run_id, launcher, authority, head, work=work)

        # This is not a production check context, qualification policy or proof.
        target = SimpleNamespace(
            repository=REPOSITORY,
            repository_id=REPOSITORY_ID,
            app_id=APP_ID,
            check_name=NAMES['native'],
            activation_name=NAMES['activation'],
        )
        phase = 'app-client'
        client = CheckReporter.from_key(
            target,
            os.environ['NATIVE_GATE_PRIVATE_KEY'],
            run_id,
            fence,
            journal=journals['admit'],
        )
        phase = 'exercise'
        result = exercise(
            client,
            head,
            run_id,
            lambda value: write_report(output, value),
            stage=stage,
            launcher=launcher,
            authority=authority,
            nonce=nonce,
            report_journal=journals['report'],
            previous=previous,
        )
        print(json.dumps({'success': result['success'], 'scope': result['scope']}))
        return 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as error:
        if output is not None and not output.exists():
            write_report(
                output,
                {
                    'success': False,
                    'failed_phase': phase,
                    'error_type': type(error).__name__,
                    'diagnostics': check_error_details(error),
                },
            )
        print(
            json.dumps(
                {
                    'success': False,
                    'phase': phase,
                    'error_type': type(error).__name__,
                    'diagnostics': check_error_details(error),
                }
            )
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
