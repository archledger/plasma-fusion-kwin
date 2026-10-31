# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import base64
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from ci.native.check_journal import CheckJournal
from ci.native.checks import CheckReporter, CheckWriteError, _request, app_jwt
from ci.native.qualification import QualificationPolicy


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy(
            'owner/repo', 100, 200, 'a' * 40, 'A' * 40, ('pkg',), app_id=300
        )
        self.fences, self.writes = [], []
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = CheckJournal(
            Path(self.directory.name) / 'admit.json',
            qualification_id='c' * 64,
            run_id=500,
            nonce='d' * 32,
            phase='admit',
        )
        self.client = CheckReporter(
            self.policy,
            'fixture-token',
            500,
            lambda: self.fences.append(True),
            journal=self.journal,
        )
        self.check = {
            'id': 800,
            'name': self.policy.check_name,
            'head_sha': 'b' * 40,
            'external_id': 'c' * 64,
            'details_url': self.client.details_url,
            'status': 'in_progress',
            'conclusion': None,
            'completed_at': None,
            'app': {'id': 300},
        }

    def send(self, method, resource, body=None):
        self.writes.append((method, resource, body))
        value = copy.deepcopy(self.check)
        if body:
            value.update(body)
        if method == 'PATCH':
            value['completed_at'] = '2026-10-31T01:00:00Z'
            self.check = copy.deepcopy(value)
        return value

    def reporting(self):
        self.client.journal = CheckJournal(
            Path(self.directory.name) / 'report.json',
            qualification_id='c' * 64,
            run_id=500,
            nonce='d' * 32,
            phase='report',
        )

    def test_create_is_fenced_and_checks_independent_app(self):
        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(self.client, '_send', self.send),
        ):
            self.assertEqual(
                self.client.begin('b' * 40, self.policy.check_name, 'c' * 64), 800
            )
        self.assertEqual(self.writes[0][0], 'POST')
        self.assertEqual(len(self.fences), 1)

    def test_existing_operation_is_refused_without_adopting_it(self):
        with (
            patch('ci.native.checks.get_all', return_value=[self.check]),
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.assertEqual(self.writes, [])

    def test_duplicate_or_wrong_app_response_cannot_succeed(self):
        with (
            patch('ci.native.checks.get_all', return_value=[self.check, self.check]),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.check['app']['id'] = 301
        self.client = CheckReporter(
            self.policy, 'fixture', 500, lambda: None, journal=self.journal
        )
        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)

    def test_completion_cannot_overwrite_another_qualification(self):
        self.reporting()
        self.check['external_id'] = 'd' * 64
        with (
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.complete(
                800, 'b' * 40, self.policy.check_name, 'c' * 64, success=True
            )
        self.assertEqual([method for method, _, _ in self.writes], ['GET'])

    def test_success_and_failure_are_explicit_fenced_completions(self):
        for success in [False, True]:
            self.setUp()
            self.reporting()
            self.writes.clear()
            with patch.object(self.client, '_send', self.send):
                self.client.complete(
                    800, 'b' * 40, self.policy.check_name, 'c' * 64, success=success
                )
            self.assertEqual(
                self.writes[1][2]['conclusion'], 'success' if success else 'failure'
            )
            self.assertEqual(len(self.fences), 1)

    def test_retained_old_conclusion_refuses_reopen(self):
        self.check['conclusion'] = 'success'
        with (
            patch('ci.native.checks.get_all', return_value=[self.check]),
            patch.object(self.client, '_send', return_value=self.check),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)

    def test_refused_fence_prevents_mutation(self):
        self.client._before_write = lambda: (_ for _ in ()).throw(
            ValueError('held by another operation')
        )
        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.assertEqual(self.writes, [])
        self.client = CheckReporter(
            self.policy, 'fixture', 500, self.client._before_write, journal=self.journal
        )
        self.reporting()
        with (
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.complete(
                800, 'b' * 40, self.policy.check_name, 'c' * 64, success=True
            )
        self.assertEqual([item[0] for item in self.writes], ['GET'])

    def test_completed_current_operation_cannot_be_reopened(self):
        old = dict(
            self.check,
            status='completed',
            conclusion='success',
            completed_at='2026-10-31T01:00:00Z',
        )
        with (
            patch('ci.native.checks.get_all', return_value=[old]),
            patch.object(self.client, '_send') as send,
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        send.assert_not_called()

    def test_fresh_creation_preserves_prior_completion_fields(self):
        for conclusion in ['failure', 'success']:
            with self.subTest(conclusion=conclusion):
                self.setUp()
                server = dict(
                    self.check,
                    id=700,
                    external_id='e' * 64,
                    details_url='https://github.com/owner/repo/actions/runs/400',
                    status='completed',
                    conclusion=conclusion,
                    completed_at='2026-10-31T01:00:00Z',
                )

                with (
                    patch('ci.native.checks.get_all', return_value=[server]),
                    patch.object(self.client, '_send', side_effect=self.send),
                ):
                    self.assertEqual(
                        self.client.begin('b' * 40, self.policy.check_name, 'c' * 64),
                        800,
                    )
                self.assertEqual(server['status'], 'completed')
                self.assertEqual(server['conclusion'], conclusion)
                self.assertEqual(server['completed_at'], '2026-10-31T01:00:00Z')
                self.assertEqual([w[0] for w in self.writes], ['POST', 'GET'])

    def test_pending_readback_rejects_a_retained_completion_timestamp(self):
        response = dict(self.check, completed_at='2026-10-31T01:00:00Z')
        with (
            patch('ci.native.checks.get_all', return_value=[response]),
            patch.object(self.client, '_send', return_value=response),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)


class FreshChecksTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.policy = QualificationPolicy(
            'owner/repo', 100, 200, 'a' * 40, 'A' * 40, ('pkg',), app_id=300
        )
        self.calls, self.fences, self.server = [], [], {}
        self.journal = self.make_journal('admit')

    def make_journal(self, phase, **changes):
        return CheckJournal(
            Path(self.directory.name) / (phase + '.json'),
            **(
                {
                    'qualification_id': 'c' * 64,
                    'run_id': 500,
                    'nonce': 'd' * 32,
                    'phase': phase,
                }
                | changes
            ),
        )

    def client(self, journal=None):
        return CheckReporter(
            self.policy,
            'fixture',
            500,
            lambda: self.fences.append(True),
            journal=journal or self.journal,
        )

    def send(self, method, resource, body=None):
        self.calls.append((method, resource, copy.deepcopy(body)))
        if method == 'POST':
            state = json.loads(self.journal.path.read_text())
            self.assertEqual(state['writes'][-1]['status'], 'pending')
            check_id = len(self.server) + 800
            self.server[check_id] = dict(
                body, id=check_id, app={'id': 300}, conclusion=None, completed_at=None
            )
        else:
            check_id = int(resource.split('/')[-1])
            if method == 'PATCH':
                self.server[check_id].update(body, completed_at='2026-10-31T01:00:00Z')
        return copy.deepcopy(self.server[check_id])

    def test_terminal_history_is_not_reset_or_adopted(self):
        client = self.client()
        for i, conclusion in enumerate(['failure', 'success']):
            self.server[700 + i] = {
                'id': 700 + i,
                'app': {'id': 300},
                'name': self.policy.check_name,
                'head_sha': 'b' * 40,
                'external_id': str(i + 1) * 64,
                'details_url': f'https://github.com/owner/repo/actions/runs/{400 + i}',
                'status': 'completed',
                'conclusion': conclusion,
                'completed_at': '2026-10-31T00:00:00Z',
            }
        before = copy.deepcopy(self.server)
        with (
            patch('ci.native.checks.get_all', return_value=list(self.server.values())),
            patch.object(client, '_send', self.send),
        ):
            check_id = client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.assertNotIn(check_id, before)
        self.assertEqual([c[0] for c in self.calls], ['POST', 'GET'])
        self.assertEqual({i: self.server[i] for i in before}, before)
        self.assertNotIn('conclusion', self.calls[0][2])
        self.assertNotIn('completed_at', self.calls[0][2])

    def test_unknown_creation_prevents_the_other_role_and_retry(self):
        client = self.client()
        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(client, '_send', side_effect=OSError('lost')) as send,
        ):
            for name in [self.policy.check_name, 'Native KWin / activation #1']:
                with self.assertRaises(CheckWriteError):
                    client.begin('b' * 40, name, 'c' * 64)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            json.loads(self.journal.path.read_text())['writes'][0]['status'], 'pending'
        )

    def test_missing_or_mismatched_journal_prevents_writes(self):
        for journal in [None, self.journal]:
            client = CheckReporter(
                self.policy, 'fixture', 501, lambda: None, journal=journal
            )
            with (
                patch.object(client, '_send') as send,
                self.assertRaises(CheckWriteError),
            ):
                client.begin('b' * 40, self.policy.check_name, 'c' * 64)
            send.assert_not_called()

    def test_completion_is_read_back_and_cannot_be_repeated(self):
        client = self.client()
        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(client, '_send', self.send),
        ):
            check_id = client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        reporter = self.client(self.make_journal('report'))
        self.calls.clear()
        with patch.object(reporter, '_send', self.send):
            reporter.complete(
                check_id, 'b' * 40, self.policy.check_name, 'c' * 64, success=True
            )
            with self.assertRaises(CheckWriteError):
                reporter.complete(
                    check_id, 'b' * 40, self.policy.check_name, 'c' * 64, success=True
                )
        self.assertEqual([c[0] for c in self.calls], ['GET', 'PATCH', 'GET'])
        self.assertEqual(self.server[check_id]['conclusion'], 'success')

    def test_intent_and_ack_persistence_failures_block_subsequent_mutations(self):
        for operation in ['begin', 'confirm']:
            with self.subTest(operation=operation):
                self.setUp()
                client = self.client()
                with (
                    patch('ci.native.checks.get_all', return_value=[]),
                    patch.object(client, '_send', self.send),
                    patch.object(self.journal, operation, side_effect=OSError('disk')),
                ):
                    with self.assertRaises(CheckWriteError):
                        client.begin('b' * 40, self.policy.check_name, 'c' * 64)
                    with self.assertRaises(CheckWriteError):
                        client.begin('b' * 40, 'Native KWin / activation #1', 'c' * 64)
                self.assertEqual(
                    sum(c[0] == 'POST' for c in self.calls),
                    0 if operation == 'begin' else 1,
                )

    def test_create_acknowledgment_requires_unchanged_direct_readback(self):
        client = self.client()
        original = self.send

        def changed(method, resource, body=None):
            result = original(method, resource, body)
            if method == 'GET':
                result['id'] += 1
            return result

        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(client, '_send', changed),
            self.assertRaises(CheckWriteError),
        ):
            client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.assertEqual(
            json.loads(self.journal.path.read_text())['writes'][0]['status'], 'pending'
        )

    def test_qid_and_phase_must_match_the_durable_journal(self):
        for qid, phase in [('e' * 64, 'admit'), ('c' * 64, 'report')]:
            with self.subTest(qid=qid, phase=phase):
                self.setUp()
                journal = (
                    self.journal if phase == 'admit' else self.make_journal('report')
                )
                client = self.client(journal)
                with (
                    patch.object(client, '_send') as send,
                    self.assertRaises(CheckWriteError),
                ):
                    client.begin('b' * 40, self.policy.check_name, qid)
                send.assert_not_called()


class CheckAuthenticationTest(unittest.TestCase):
    def test_real_rsa_signature_and_bounded_claims(self):
        key = subprocess.check_output(
            [
                'openssl',
                'genpkey',
                '-algorithm',
                'RSA',
                '-pkeyopt',
                'rsa_keygen_bits:2048',
            ],
            stderr=subprocess.DEVNULL,
        )
        token = app_jwt(key.decode(), 300, now=1000)
        header, claims, signature = token.split('.')
        decode = lambda value: base64.urlsafe_b64decode(
            value + '=' * ((-len(value)) % 4)
        )
        self.assertEqual(json.loads(decode(header)), {'alg': 'RS256', 'typ': 'JWT'})
        self.assertEqual(
            json.loads(decode(claims)), {'iat': 940, 'exp': 1540, 'iss': '300'}
        )
        public = subprocess.check_output(
            ['openssl', 'pkey', '-pubout'], input=key, stderr=subprocess.DEVNULL
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'public.pem').write_bytes(public)
            (root / 'signature').write_bytes(decode(signature))
            result = subprocess.run(
                [
                    'openssl',
                    'dgst',
                    '-sha256',
                    '-verify',
                    str(root / 'public.pem'),
                    '-signature',
                    str(root / 'signature'),
                ],
                input=(header + '.' + claims).encode(),
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_key_and_signing_timeout_fail_closed(self):
        with self.assertRaises(CheckWriteError):
            app_jwt('not a key', 300)
        with (
            patch(
                'ci.native.checks.subprocess.run',
                side_effect=subprocess.TimeoutExpired('openssl', 15),
            ),
            self.assertRaises(CheckWriteError),
        ):
            app_jwt('fixture', 300)

    def test_installation_request_is_repository_and_permission_scoped(self):
        policy = QualificationPolicy(
            'owner/repo', 100, 200, 'a' * 40, 'A' * 40, ('pkg',), app_id=300
        )
        response = {
            'permissions': {'checks': 'write', 'metadata': 'read'},
            'repositories': [{'id': 100}],
            'token': 'fixture',
        }
        with (
            patch('ci.native.checks.app_jwt', return_value='jwt'),
            patch('ci.native.checks._request', return_value=response) as call,
        ):
            CheckReporter.from_key(policy, 'key', 500, lambda: None)
            self.assertEqual(
                call.call_args.args,
                (
                    'POST',
                    'https://api.github.com/app/installations/176966224/access_tokens',
                    'jwt',
                    {
                        'repository_ids': [100],
                        'permissions': {'checks': 'write', 'metadata': 'read'},
                    },
                ),
            )
        for changed in [
            dict(response, permissions={'checks': 'write', 'contents': 'write'}),
            dict(response, repositories=[{'id': 101}]),
            dict(response, token=''),
        ]:
            with (
                patch('ci.native.checks.app_jwt', return_value='jwt'),
                patch('ci.native.checks._request', return_value=changed),
                self.assertRaises(ValueError),
            ):
                CheckReporter.from_key(policy, 'key', 500, lambda: None)

    def test_transport_is_bounded_single_attempt_without_redirect_replay(self):
        for error in [
            HTTPError('https://api.github.com', 302, 'redirect', {}, None),
            HTTPError('https://api.github.com', 500, 'failure', {}, None),
            URLError('offline'),
        ]:
            with (
                self.subTest(error=type(error).__name__),
                patch('ci.native.checks.build_opener') as opener,
            ):
                opener.return_value.open.side_effect = error
                with self.assertRaises(CheckWriteError):
                    _request('POST', 'https://api.github.com/checks', 'fixture', {})
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(len(opener.call_args.args), 1)
                handler = opener.call_args.args[0]
                self.assertEqual(type(handler).__name__, 'NoRedirect')
                self.assertIsNone(
                    handler.redirect_request(
                        None, None, 302, 'redirect', {}, 'https://other.invalid'
                    )
                )
        for raw in [
            b'{',
            b'[]',
            b'{"id":NaN}',
            b'{"id":1,"id":2}',
            b'x' * (1024 * 1024 + 1),
        ]:
            response = MagicMock()
            response.status = 201
            response.read.return_value = raw
            response.__enter__.return_value = response
            with (
                patch('ci.native.checks.build_opener') as opener,
                self.assertRaises(CheckWriteError),
            ):
                opener.return_value.open.return_value = response
                _request('POST', 'https://api.github.com/checks', 'fixture', {})
        response = MagicMock()
        response.status = 201
        response.read.return_value = b'{"id":1}'
        response.__enter__.return_value = response
        with patch('ci.native.checks.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(
                _request(
                    'POST', 'https://api.github.com/checks', 'fixture', {'name': 'test'}
                ),
                {'id': 1},
            )
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer fixture')
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 30)


if __name__ == '__main__':
    unittest.main()
