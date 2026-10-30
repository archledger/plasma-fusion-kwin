# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import io
import unittest
from email.message import Message
from http.client import HTTPException, IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ci.native.proof_api import (
    MAX_INTEGER_DIGITS,
    MAX_RESPONSE_BYTES,
    ProofAPI,
    ProofReadError,
)


class Response(io.BytesIO):
    def __init__(self, body=b'{"id": 42}', content_type='application/json', status=200):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers['Content-Type'] = content_type


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class ProofAPITest(unittest.TestCase):
    def setUp(self):
        self.api = ProofAPI('owner/downstream', token='fixture-token-not-a-credential')
        self.opener = Opener()
        self.api._opener = self.opener

    def test_exact_get_endpoint_and_auth_header(self):
        self.assertEqual(self.api.get('actions/runs/42'), {'id': 42})
        request, timeout = self.opener.calls[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, 'https://api.github.com/repos/owner/downstream/actions/runs/42')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-token-not-a-credential')
        self.assertEqual(timeout, 20)

    def test_public_read_does_not_add_authorization(self):
        api = ProofAPI('owner/downstream')
        api._opener = self.opener
        api.get('')
        request = self.opener.calls[0][0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/owner/downstream')
        self.assertIsNone(request.get_header('Authorization'))

    def test_page_parameters_are_numeric_and_bounded(self):
        self.api.get('pulls/7/reviews', page=2, per_page=10)
        self.assertTrue(self.opener.calls[0][0].full_url.endswith('?page=2&per_page=10'))
        for page, count in [(True, 10), (0, 10), (10001, 10), (1, 101), (1, '10')]:
            with self.subTest(page=page, count=count), self.assertRaises(ProofReadError):
                self.api.get('pulls/7/reviews', page=page, per_page=count)

    def test_unexpected_hosts_paths_and_write_resources_never_reach_transport(self):
        for resource in ['https://evil.invalid', '//evil.invalid', '../settings',
                         'actions/runs/42/cancel', 'git/refs/heads/main',
                         'pulls/7?redirect=https://evil.invalid', 'pulls/%37',
                         'pulls/7\n', 'actions/artifacts/42/zip']:
            with self.subTest(resource=resource), self.assertRaises(ProofReadError):
                self.api.get(resource)
        self.assertEqual(self.opener.calls, [])

    def test_invalid_repository_and_token_are_rejected(self):
        for repository in ['../downstream', 'owner/..', 'owner/repo/extra',
                           'owner@evil.invalid/repo', 'owner/repo?x=1']:
            with self.subTest(repository=repository), self.assertRaises(ProofReadError):
                ProofAPI(repository)
        with self.assertRaises(ProofReadError):
            ProofAPI('owner/repo', token='fixture\nInjected: value')

    def test_redirect_is_not_followed(self):
        headers = Message()
        headers['Location'] = 'https://evil.invalid'
        self.opener.error = HTTPError('https://api.github.com', 302, 'redirect', headers, None)
        with self.assertRaises(ProofReadError):
            self.api.get('pulls/7')
        self.assertEqual(len(self.opener.calls), 1)
        self.assertIsNone(self.api._redirect_handler.redirect_request(
            self.opener.calls[0][0], None, 302, 'redirect', headers, 'https://evil.invalid'))

    def test_http_and_transport_errors_fail_closed_without_echoing_token(self):
        for error in [HTTPError('https://api.github.com', 403, 'forbidden', {}, None),
                      URLError('offline'), TimeoutError('timeout')]:
            self.opener.error = error
            with self.subTest(error=type(error).__name__), self.assertRaises(ProofReadError) as caught:
                self.api.get('pulls/7')
            self.assertNotIn('fixture-token', str(caught.exception))
            if isinstance(error, HTTPError):
                self.assertTrue(error.fp.closed)

    def test_invalid_json_and_duplicate_keys_are_rejected(self):
        for body in [b'not json', b'{"id":1,"id":2}', b'{"score":NaN}', b'null', b'42',
                     b'\xff', b'[' * 2000]:
            self.opener.response = Response(body)
            with self.subTest(body=body), self.assertRaises(ProofReadError):
                self.api.get('pulls/7')

    def test_exponent_overflow_is_rejected_at_any_depth(self):
        for body in [b'{"value":1e999999}', b'{"value":-1e999999}',
                     b'{"nested":[{"value":1e999999}]}']:
            self.opener.response = Response(body)
            with self.subTest(body=body), self.assertRaises(ProofReadError):
                self.api.get('pulls/7')

    def test_finite_float_and_integer_values_remain_supported(self):
        self.opener.response = Response(b'{"value":1.25e-3,"nested":[-42,1e308]}')
        self.assertEqual(self.api.get('pulls/7'), {'value': 0.00125, 'nested': [-42, 1e308]})

    def test_truncated_and_malformed_http_reads_are_normalized_and_closed(self):
        class BrokenResponse(Response):
            def read(self, size=-1):
                raise IncompleteRead(b'private response detail', 100)

        self.opener.response = BrokenResponse()
        with self.assertRaises(ProofReadError) as caught:
            self.api.get('pulls/7')
        self.assertNotIn('private response detail', str(caught.exception))
        self.assertTrue(self.opener.response.closed)
        self.opener.error = HTTPException('private transport detail')
        with self.assertRaises(ProofReadError) as caught:
            self.api.get('pulls/7')
        self.assertNotIn('private transport detail', str(caught.exception))

    def test_oversized_integer_token_is_refused(self):
        self.opener.response = Response(b'{"id":' + b'9' * 5000 + b'}')
        with self.assertRaises(ProofReadError):
            self.api.get('pulls/7')

    def test_explicit_integer_limit_has_a_valid_boundary(self):
        value = b'9' * MAX_INTEGER_DIGITS
        self.opener.response = Response(b'{"id":-' + value + b'}')
        self.assertEqual(self.api.get('pulls/7'), {'id': -int(value)})
        self.opener.response = Response(b'{"nested":[-' + value + b'9]}')
        with self.assertRaises(ProofReadError):
            self.api.get('pulls/7')

    def test_unexpected_numeric_conversion_failure_is_normalized(self):
        with patch('ci.native.proof_api.json.loads', side_effect=ValueError('private detail')), \
                self.assertRaises(ProofReadError) as caught:
            self.api.get('pulls/7')
        self.assertNotIn('private detail', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_intentional_json_hook_refusals_keep_their_reason(self):
        for body, reason in [(b'{"id":1,"id":2}', 'duplicate JSON field'),
                             (b'{"value":NaN}', 'non-JSON number')]:
            self.opener.response = Response(body)
            with self.subTest(reason=reason), self.assertRaises(ProofReadError) as caught:
                self.api.get('pulls/7')
            self.assertIn(reason, str(caught.exception))

    def test_wrong_type_status_and_oversized_response_are_rejected(self):
        for response in [Response(content_type='text/html'), Response(status=204),
                         Response(b'x' * (MAX_RESPONSE_BYTES + 1))]:
            self.opener.response = response
            with self.subTest(response=response), self.assertRaises(ProofReadError):
                self.api.get('pulls/7')

    def test_call_budget_is_not_an_unbounded_retry_loop(self):
        api = ProofAPI('owner/downstream', max_calls=1)
        api._opener = self.opener
        api.get('pulls/7')
        with self.assertRaises(ProofReadError):
            api.get('pulls/7')
        self.assertEqual(len(self.opener.calls), 1)


if __name__ == '__main__':
    unittest.main()
