# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import io
import json
import unittest
from http.client import HTTPException
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ci.native.checks import (
    CheckReporter,
    CheckWriteError,
    _request,
    check_error_details,
)


class CheckErrorDetailsTest(unittest.TestCase):
    def failed_request(self, body, status=422):
        stream = io.BytesIO(body)
        error = HTTPError(
            'https://example.invalid/private-token',
            status,
            'private-message',
            {},
            stream,
        )
        with patch('ci.native.checks.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(CheckWriteError) as caught:
                _request(
                    'PATCH',
                    'https://example.invalid/private-token',
                    'private-token',
                    {},
                )
        self.assertTrue(stream.closed)
        return caught.exception

    def test_http_status_and_allowed_fields_survive_wrapping_without_secrets(self):
        body = json.dumps(
            {
                'message': 'private-message',
                'documentation_url': 'https://private.invalid',
                'errors': [
                    {
                        'field': 'conclusion',
                        'code': 'invalid',
                        'message': 'private-token',
                    },
                    {'field': 'private-token', 'code': 'invalid'},
                    {'field': 'completed_at', 'code': 'private-token'},
                ],
            }
        ).encode()
        cause = self.failed_request(body)
        outer = CheckWriteError('private-wrapper')
        outer.__cause__ = cause
        details = check_error_details(outer)
        self.assertEqual(
            details,
            {
                'kind': 'http',
                'http_status': 422,
                'validation_errors': [{'field': 'conclusion', 'code': 'invalid'}],
            },
        )
        self.assertNotIn('private', json.dumps(details))

    def test_malformed_or_oversized_http_body_cannot_leak(self):
        for body in [
            b'private-token',
            b'x' * 9000,
            b'{"errors":"private-token"}',
            b'{"errors":[],"errors":[{"field":"conclusion","code":"invalid"}]}',
        ]:
            with self.subTest(size=len(body)):
                self.assertEqual(
                    check_error_details(self.failed_request(body, 503)),
                    {'kind': 'http', 'http_status': 503, 'validation_errors': []},
                )

    def test_schema_message_exposes_only_fixed_field_reason(self):
        body = json.dumps(
            {
                'message': "private-token For 'properties/conclusion', nil is not a string. "
                "For 'properties/completed_at', null is not a string. "
                "For 'properties/private-token', nil is not a string."
            }
        ).encode()
        details = check_error_details(self.failed_request(body))
        self.assertEqual(
            details['message_hints'],
            [
                {'field': 'completed_at', 'reason': 'null_not_string'},
                {'field': 'conclusion', 'reason': 'null_not_string'},
            ],
        )
        self.assertNotIn('private', json.dumps(details))

    def test_unreadable_error_body_preserves_http_status(self):
        class Body(io.BytesIO):
            def read(self, size=-1):
                raise HTTPException('private-body')

        stream = Body()
        error = HTTPError('https://private.invalid', 503, 'private-message', {}, stream)
        with patch('ci.native.checks.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(CheckWriteError) as caught:
                _request('PATCH', 'https://private.invalid', 'fixture', {})
        self.assertEqual(
            check_error_details(caught.exception),
            {'kind': 'http', 'http_status': 503, 'validation_errors': []},
        )
        self.assertTrue(stream.closed)

    def test_error_body_read_is_bounded(self):
        class Body(io.BytesIO):
            def __init__(self, value):
                super().__init__(value)
                self.sizes = []

            def read(self, size=-1):
                self.sizes.append(size)
                return super().read(size)

        body = Body(b'x' * 10000)
        error = HTTPError('https://example.invalid', 422, 'bad', {}, body)
        with patch('ci.native.checks.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(CheckWriteError):
                _request('PATCH', 'https://example.invalid', 'fixture', {})
        self.assertEqual(body.sizes, [8193])

    def test_error_body_limit_boundary(self):
        raw = json.dumps(
            {'errors': [{'field': 'conclusion', 'code': 'invalid'}]}
        ).encode()
        body = raw + b' ' * (8192 - len(raw))
        self.assertEqual(
            check_error_details(self.failed_request(body))['validation_errors'],
            [{'field': 'conclusion', 'code': 'invalid'}],
        )
        self.assertEqual(
            check_error_details(self.failed_request(body + b' '))['validation_errors'],
            [],
        )

    def test_transport_cause_is_classified_without_message_or_url(self):
        error = CheckWriteError('private-wrapper')
        error.__cause__ = URLError('https://private.invalid/token')
        self.assertEqual(check_error_details(error), {'kind': 'transport'})

    def test_local_filesystem_errors_are_not_transport_failures(self):
        for cause in [FileExistsError('private-path'), PermissionError('private-path')]:
            with self.subTest(type=type(cause).__name__):
                error = CheckWriteError('private-message')
                error.__cause__ = cause
                self.assertEqual(check_error_details(error), {'kind': 'io'})
                self.assertEqual(check_error_details(cause), {'kind': 'io'})

    def test_oserror_at_http_boundary_is_transport(self):
        with patch('ci.native.checks.build_opener') as opener:
            opener.return_value.open.side_effect = OSError('private-socket')
            with self.assertRaises(CheckWriteError) as caught:
                _request('PATCH', 'https://example.invalid', 'fixture', {})
        self.assertEqual(check_error_details(caught.exception), {'kind': 'transport'})

    def test_invalid_success_json_is_distinct_from_transport(self):
        error = CheckWriteError('private-wrapper')
        try:
            json.loads('private-response')
        except json.JSONDecodeError as cause:
            error.__cause__ = cause
        self.assertEqual(check_error_details(error), {'kind': 'response_json'})

    def test_mismatched_fields_are_names_not_response_values(self):
        client = CheckReporter(
            SimpleNamespace(repository='owner/repo', app_id=300),
            'fixture',
            500,
            lambda: None,
        )
        value = {
            'id': 800,
            'app': {'id': 300},
            'name': 'name',
            'head_sha': 'a' * 40,
            'external_id': 'b' * 64,
            'details_url': client.details_url,
            'status': 'completed',
            'conclusion': 'private-token',
            'completed_at': 'private-date',
        }
        with self.assertRaises(CheckWriteError) as caught:
            client._check(value, 'a' * 40, 'name', 'b' * 64, 'in_progress', None)
        self.assertEqual(
            check_error_details(caught.exception),
            {
                'kind': 'response_mismatch',
                'fields': ['status', 'conclusion', 'completed_at'],
            },
        )

    def test_each_check_field_remains_required(self):
        client = CheckReporter(
            SimpleNamespace(repository='owner/repo', app_id=300),
            'fixture',
            500,
            lambda: None,
        )
        base = {
            'id': 800,
            'app': {'id': 300},
            'name': 'name',
            'head_sha': 'a' * 40,
            'external_id': 'b' * 64,
            'details_url': client.details_url,
            'status': 'in_progress',
            'conclusion': None,
            'completed_at': None,
        }
        changes = {
            'app_id': 301,
            'name': 'wrong',
            'head_sha': 'c' * 40,
            'external_id': 'd' * 64,
            'details_url': 'https://other.invalid',
            'status': 'completed',
            'conclusion': 'failure',
            'completed_at': '2026-10-31T00:00:00Z',
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                item = dict(base, app=dict(base['app']))
                if field == 'app_id':
                    item['app']['id'] = value
                else:
                    item[field] = value
                with self.assertRaises(CheckWriteError) as caught:
                    client._check(item, 'a' * 40, 'name', 'b' * 64, 'in_progress', None)
                self.assertEqual(
                    check_error_details(caught.exception)['fields'], [field]
                )

    def test_unknown_cyclic_chain_is_bounded_and_redacted(self):
        error = CheckWriteError('private-token')
        error.__cause__ = error
        self.assertEqual(check_error_details(error), {'kind': 'unknown'})

    def test_cause_chain_depth_is_bounded(self):
        root = CheckWriteError('private-token')
        current = root
        for _ in range(10):
            cause = CheckWriteError('private-token')
            current.__cause__ = cause
            current = cause
        current.__cause__ = HTTPError(
            'https://private.invalid', 422, 'private', {}, None
        )
        self.assertEqual(check_error_details(root), {'kind': 'unknown'})

    def test_cause_depth_exact_boundary(self):
        for wrappers, kind in [(7, 'http'), (8, 'unknown')]:
            with self.subTest(wrappers=wrappers):
                error = HTTPError(
                    'https://private.invalid', 422, 'private', {}, io.BytesIO()
                )
                root = error
                for _ in range(wrappers):
                    outer = CheckWriteError('private')
                    outer.__cause__ = root
                    root = outer
                try:
                    self.assertEqual(check_error_details(root)['kind'], kind)
                finally:
                    error.close()


if __name__ == '__main__':
    unittest.main()
