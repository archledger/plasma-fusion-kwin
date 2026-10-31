# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual, main-only App contract probe on a disposable validation ref."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from ci.native.checks import CheckReporter, check_error_details
from ci.native.proof_api import ProofAPI
from ci.native.qualification import canonical_json, digest, identifier, require

REPOSITORY = 'archledger/plasma-fusion-kwin'
REPOSITORY_ID = 1439058578
APP_ID = 5544780
WORKFLOW = '.github/workflows/check-lifecycle-probe.yml'
AUTHORITY_REF = 'refs/heads/native-gate-coordination'
PROBE_REF = 'refs/heads/validation/check-lifecycle-contract'
MAX_REPORT_BYTES = 64 * 1024


def verify_run(api, run_id, launcher):
    run = api.get(f'actions/runs/{run_id}')
    require(
        run['id'] == run_id
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
        and run['status'] == 'in_progress'
        and run['conclusion'] is None
        and run['actor']['id'] == run['triggering_actor']['id'],
        'probe run identity differs',
    )
    identifier(run['workflow_id'])
    permission = api.get('collaborators/' + run['actor']['login'] + '/permission')
    require(
        permission['user']['id'] == run['actor']['id']
        and permission['permission'] in {'write', 'maintain', 'admin'},
        'probe actor is not authorized',
    )


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


def exercise(client, head, run_id, save):
    """Record the legacy control, then candidate resets after failure and success."""
    name = client.policy.check_name
    qids = [
        hashlib.sha256(canonical_json({'probe_run': run_id, 'cycle': i})).hexdigest()
        for i in range(3)
    ]
    report = {
        'schema_version': 1,
        'scope': 'disposable App contract only; no native qualification',
        'run_id': run_id,
        'head': head,
        'check_name': name,
        'success': False,
        'observations': [],
        'pending_write': None,
    }
    phase = 'create'
    check_id = None

    def intent(action, qid):
        report['pending_write'] = {
            'phase': phase,
            'action': action,
            'check_id': check_id,
            'external_id': qid,
        }
        save(report)

    def observe(qid, status, conclusion):
        value = client._send('GET', f'check-runs/{check_id}')
        fields = [
            'id',
            'name',
            'head_sha',
            'status',
            'conclusion',
            'completed_at',
            'external_id',
            'details_url',
        ]
        report['observations'].append(
            dict(phase=phase, **{field: value.get(field) for field in fields})
        )
        save(report)
        client._check(value, head, name, qid, status, conclusion)
        report['pending_write'] = None
        save(report)

    try:
        intent('begin', qids[0])
        check_id = client.begin(head, name, qids[0])
        report['check_id'] = check_id
        report['pending_write'] = None
        save(report)
        phase = 'initial-failure'
        intent('complete-failure', qids[0])
        client.complete(check_id, head, name, qids[0], success=False)
        observe(qids[0], 'completed', 'failure')

        phase = 'legacy-update'
        intent('legacy-update', qids[1])
        client._before_write()
        client._send(
            'PATCH',
            f'check-runs/{check_id}',
            {
                'name': name,
                'status': 'in_progress',
                'external_id': qids[1],
                'details_url': client.details_url,
            },
        )
        observe(qids[1], 'completed', 'failure')

        phase = 'reopened-failure'
        intent('begin', qids[1])
        require(client.begin(head, name, qids[1]) == check_id, 'probe check ID changed')
        observe(qids[1], 'in_progress', None)
        phase = 'completed-success'
        intent('complete-success', qids[1])
        client.complete(check_id, head, name, qids[1], success=True)
        observe(qids[1], 'completed', 'success')
        phase = 'reopened-success'
        intent('begin', qids[2])
        require(client.begin(head, name, qids[2]) == check_id, 'probe check ID changed')
        observe(qids[2], 'in_progress', None)
        phase = 'final-failure'
        intent('complete-failure', qids[2])
        client.complete(check_id, head, name, qids[2], success=False)
        observe(qids[2], 'completed', 'failure')
        report['success'] = True
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

        def fence():
            check_fence(api, run_id, launcher, authority, head, work=work)

        # Only these four attributes are needed by the Checks client. This is
        # deliberately not a native qualification policy or candidate proof.
        target = SimpleNamespace(
            repository=REPOSITORY,
            repository_id=REPOSITORY_ID,
            app_id=APP_ID,
            check_name=f'Validation / check lifecycle #{run_id}',
        )
        phase = 'app-client'
        client = CheckReporter.from_key(
            target, os.environ['NATIVE_GATE_PRIVATE_KEY'], run_id, fence
        )
        phase = 'exercise'
        result = exercise(
            client, head, run_id, lambda value: write_report(output, value)
        )
        print(json.dumps({'success': result['success'], 'scope': result['scope']}))
        return 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
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
