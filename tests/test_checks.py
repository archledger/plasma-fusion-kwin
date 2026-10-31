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

from ci.native.checks import CheckReporter, CheckWriteError, _request, app_jwt
from ci.native.qualification import QualificationPolicy


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy(
            'owner/repo', 100, 200, 'a' * 40, 'A' * 40, ('pkg',), app_id=300
        )
        self.fences, self.writes = [], []
        self.client = CheckReporter(
            self.policy, 'fixture-token', 500, lambda: self.fences.append(True)
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
        return value

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

    def test_existing_check_is_reopened_without_creating_duplicate(self):
        with (
            patch('ci.native.checks.get_all', return_value=[self.check]),
            patch.object(self.client, '_send', self.send),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.assertEqual(self.writes[0][:2], ('PATCH', 'check-runs/800'))

    def test_duplicate_or_wrong_app_response_cannot_succeed(self):
        with (
            patch('ci.native.checks.get_all', return_value=[self.check, self.check]),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)
        self.check['app']['id'] = 301
        with (
            patch('ci.native.checks.get_all', return_value=[]),
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)

    def test_completion_cannot_overwrite_another_qualification(self):
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
            self.writes.clear()
            with patch.object(self.client, '_send', self.send):
                self.client.complete(
                    800, 'b' * 40, self.policy.check_name, 'c' * 64, success=success
                )
            self.assertEqual(
                self.writes[-1][2]['conclusion'], 'success' if success else 'failure'
            )
        self.assertEqual(len(self.fences), 2)

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
        with (
            patch.object(self.client, '_send', self.send),
            self.assertRaises(CheckWriteError),
        ):
            self.client.complete(
                800, 'b' * 40, self.policy.check_name, 'c' * 64, success=True
            )
        self.assertEqual([item[0] for item in self.writes], ['GET'])

    def test_completed_success_reopens_only_with_cleared_conclusion(self):
        old = dict(self.check, status='completed', conclusion='success')
        with (
            patch('ci.native.checks.get_all', return_value=[old]),
            patch.object(self.client, '_send', return_value=self.check) as send,
        ):
            self.assertEqual(
                self.client.begin('b' * 40, self.policy.check_name, 'c' * 64), 800
            )
            self.assertEqual(send.call_args.args[0], 'PATCH')
        with (
            patch('ci.native.checks.get_all', return_value=[old]),
            patch.object(self.client, '_send', return_value=old),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)

    def test_reopen_explicitly_clears_retained_completion_fields(self):
        for conclusion in ['failure', 'success']:
            with self.subTest(conclusion=conclusion):
                server = dict(
                    self.check,
                    status='completed',
                    conclusion=conclusion,
                    completed_at='2026-10-31T01:00:00Z',
                )

                def update(method, resource, body, server=server):
                    self.assertEqual((method, resource), ('PATCH', 'check-runs/800'))
                    server.update(body)
                    # Model the observed retained-conclusion behavior. Explicit
                    # null clearing remains subject to the live contract probe.
                    if server['conclusion'] is not None:
                        server['status'] = 'completed'
                    return copy.deepcopy(server)

                with (
                    patch('ci.native.checks.get_all', return_value=[server]),
                    patch.object(self.client, '_send', side_effect=update),
                ):
                    self.assertEqual(
                        self.client.begin('b' * 40, self.policy.check_name, 'c' * 64),
                        800,
                    )
                self.assertEqual(server['status'], 'in_progress')
                self.assertIsNone(server['conclusion'])
                self.assertIsNone(server['completed_at'])

    def test_pending_readback_rejects_a_retained_completion_timestamp(self):
        response = dict(self.check, completed_at='2026-10-31T01:00:00Z')
        with (
            patch('ci.native.checks.get_all', return_value=[response]),
            patch.object(self.client, '_send', return_value=response),
            self.assertRaises(CheckWriteError),
        ):
            self.client.begin('b' * 40, self.policy.check_name, 'c' * 64)


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
