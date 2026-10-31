# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the read-only proof verifier in a deadline-bounded isolated process group."""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeadlineExceeded(Exception):
    def __init__(self, process_id):
        super().__init__('read-only verification exceeded its whole-operation deadline')
        self.process_id = process_id


class VerificationInterrupted(Exception):
    pass


@contextmanager
def handled_interruptions():
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in signals}

    def interrupt(number, frame):
        # Let owned-process cleanup finish even if the operator repeats a signal.
        for item in signals:
            signal.signal(item, signal.SIG_IGN)
        raise VerificationInterrupted('read-only verification interrupted')

    for number in signals:
        signal.signal(number, interrupt)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_isolated(command, *, env, timeout):
    process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, close_fds=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise DeadlineExceeded(process.pid) from None
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Verify native merge evidence; never push or write checks.'
    )
    for name in [
        'policy',
        'source',
        'keyring',
        'artifact',
        'admission',
        'coordination-anchor',
    ]:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--artifact-id', type=int, required=True)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 300:
        parser.error('--timeout must be between 1 and 300 seconds')
    options = {
        name: str(Path(getattr(args, name)).expanduser().absolute())
        for name in [
            'policy',
            'source',
            'keyring',
            'artifact',
            'admission',
            'coordination_anchor',
        ]
    }
    options['artifact_id'] = args.artifact_id
    program = (
        'import json,sys; sys.path.insert(0, ' + repr(str(ROOT)) + '); '
        'from ci.native.readiness import _worker; raise SystemExit(_worker(json.loads(sys.argv[1])))'
    )
    try:
        with (
            handled_interruptions(),
            tempfile.TemporaryDirectory(prefix='native-proof-worker-') as directory,
        ):
            env = {
                'PATH': os.defpath,
                'HOME': directory,
                'TMPDIR': directory,
                'LANG': 'C.UTF-8',
            }
            if 'GH_TOKEN' in os.environ:
                env['GH_TOKEN'] = os.environ['GH_TOKEN']
            result = run_isolated(
                [sys.executable, '-I', '-B', '-c', program, json.dumps(options)],
                env=env,
                timeout=args.timeout,
            )
        if len(result.stdout) > 1024 * 1024:
            raise ValueError('oversized worker result')
        value = json.loads(result.stdout)
        if (
            type(value) is not dict
            or type(value.get('ok')) is not bool
            or value.get('mode') != 'read-only'
            or value.get('merge_authorized') is not False
            or (result.returncode == 0) != value['ok']
        ):
            raise ValueError('invalid worker result')
    except DeadlineExceeded as error:
        value = {
            'ok': False,
            'mode': 'read-only',
            'merge_authorized': False,
            'error': str(error),
        }
        print(json.dumps(value, sort_keys=True))
        return 2
    except (VerificationInterrupted, KeyboardInterrupt):
        value = {
            'ok': False,
            'mode': 'read-only',
            'merge_authorized': False,
            'error': 'read-only verification interrupted',
        }
    except (OSError, ValueError, TypeError):
        value = {
            'ok': False,
            'mode': 'read-only',
            'merge_authorized': False,
            'error': 'verification worker did not return a valid result',
        }
    print(json.dumps(value, sort_keys=True))
    return 0 if value['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
