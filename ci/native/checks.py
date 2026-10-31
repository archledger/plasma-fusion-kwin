# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded Checks-only App client. No Contents write or implicit retry."""

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from ci.native.check_journal import CheckJournal
from ci.native.check_records import CheckIdentity, validate_check, validate_creation
from ci.native.pagination import get_all
from ci.native.proof_api import (
    NoRedirect,
    ProofAPI,
    bounded_integer,
    finite_float,
    object_pairs,
    reject_constant,
)
from ci.native.qualification import canonical_json, digest, identifier, require

INSTALLATION_ID = 176966224


class CheckWriteError(ValueError):
    """A check operation was refused or has an unknown outcome; do not retry."""


_ERROR_FIELDS = frozenset(
    {
        'name',
        'head_sha',
        'details_url',
        'external_id',
        'status',
        'conclusion',
        'completed_at',
        'started_at',
        'output',
        'app_id',
    }
)
_ERROR_CODES = frozenset(
    {'missing', 'missing_field', 'invalid', 'already_exists', 'unprocessable', 'custom'}
)
_MAX_ERROR_BYTES = 8192


class _CheckHTTPError(CheckWriteError):
    def __init__(self, status, fields, hints):
        super().__init__('App operation failed or is uncertain; reconcile before retry')
        self.status = status
        self.fields = fields
        self.hints = hints


class _CheckResponseError(CheckWriteError):
    def __init__(self, fields):
        super().__init__('check response does not match the operation')
        self.fields = fields


class _CheckTransportError(CheckWriteError):
    """A failure caught inside the HTTP transport, not generic caller I/O."""


def _http_error_details(error):
    """Extract fixed field/code names only; never retain raw API error text."""
    try:
        raw = error.read(_MAX_ERROR_BYTES + 1)
        if len(raw) > _MAX_ERROR_BYTES:
            return [], []
        value = json.loads(
            raw.decode(),
            object_pairs_hook=object_pairs,
            parse_int=bounded_integer,
            parse_float=finite_float,
            parse_constant=reject_constant,
        )
        errors = value.get('errors', []) if type(value) is dict else []
        if type(errors) is not list:
            errors = []
        result = []
        for item in errors[:8]:
            if type(item) is not dict:
                continue
            field, code = item.get('field'), item.get('code')
            if (
                type(field) is str
                and field in _ERROR_FIELDS
                and type(code) is str
                and code in _ERROR_CODES
            ):
                pair = {'field': field, 'code': code}
                if pair not in result:
                    result.append(pair)
        hints = []
        message = value.get('message') if type(value) is dict else None
        if type(message) is str:
            for field in sorted(_ERROR_FIELDS):
                pattern = rf"['\"]properties/{re.escape(field)}['\"], (?:nil|null) is not a string"
                if re.search(pattern, message):
                    hints.append({'field': field, 'reason': 'null_not_string'})
        return result, hints
    except (ValueError, TypeError, OSError, RecursionError, HTTPException):
        return [], []


def check_error_details(error):
    """Return bounded, non-sensitive diagnostics from explicit exception causes."""
    seen = set()
    for _ in range(8):
        if error is None or id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, (_CheckHTTPError, HTTPError)):
            status = error.status if isinstance(error, _CheckHTTPError) else error.code
            result = {
                'kind': 'http',
                'validation_errors': error.fields
                if isinstance(error, _CheckHTTPError)
                else [],
            }
            if isinstance(error, _CheckHTTPError) and error.hints:
                result['message_hints'] = error.hints
            if type(status) is int and 100 <= status <= 599:
                result['http_status'] = status
            return result
        if isinstance(error, _CheckResponseError):
            return {
                'kind': 'response_mismatch',
                'fields': [
                    field
                    for field in error.fields
                    if type(field) is str and field in _ERROR_FIELDS
                ],
            }
        if isinstance(error, (_CheckTransportError, URLError, HTTPException)):
            return {'kind': 'transport'}
        if isinstance(error, OSError):
            return {'kind': 'io'}
        if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
            return {'kind': 'response_json'}
        if isinstance(error, subprocess.SubprocessError):
            return {'kind': 'subprocess'}
        error = error.__cause__
    return {'kind': 'unknown'}


def _request(method, url, token, body=None):
    request = Request(
        url,
        data=None if body is None else canonical_json(body),
        method=method,
        headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'plasma-fusion-native-reporter',
        },
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
            require(
                response.status in {200, 201} and len(raw) <= 1024 * 1024,
                'invalid check response',
            )
            value = json.loads(
                raw.decode(),
                object_pairs_hook=object_pairs,
                parse_int=bounded_integer,
                parse_float=finite_float,
                parse_constant=reject_constant,
            )
            require(type(value) is dict, 'invalid check response shape')
            return value
    except HTTPError as error:
        try:
            fields, hints = _http_error_details(error)
        finally:
            error.close()
        raise _CheckHTTPError(error.code, fields, hints) from error
    except (URLError, HTTPException, OSError) as error:
        raise _CheckTransportError(
            'App operation failed or is uncertain; reconcile before retry'
        ) from error
    except ValueError as error:
        raise CheckWriteError(
            'App operation failed or is uncertain; reconcile before retry'
        ) from error


def app_jwt(private_key, app_id, *, now=None):
    """RS256 JWT per GitHub App documentation; key bytes never enter argv/logs."""
    identifier(app_id)
    require(
        type(private_key) is str and 0 < len(private_key) <= 65536,
        'invalid App key input',
    )
    stamp = int(time.time() if now is None else now)
    encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=')
    message = (
        encode(canonical_json({'alg': 'RS256', 'typ': 'JWT'}))
        + b'.'
        + encode(
            canonical_json({'iat': stamp - 60, 'exp': stamp + 540, 'iss': str(app_id)})
        )
    )
    with tempfile.TemporaryDirectory(prefix='native-app-key-') as directory:
        path = Path(directory) / 'key.pem'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(private_key)
        try:
            signature = subprocess.run(
                ['openssl', 'dgst', '-sha256', '-sign', str(path)],
                input=message,
                capture_output=True,
                timeout=15,
                check=True,
                env={'PATH': os.defpath, 'HOME': directory, 'LANG': 'C.UTF-8'},
            ).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise CheckWriteError('App signing failed') from error
    return (message + b'.' + encode(signature)).decode('ascii')


class CheckReporter:
    def __init__(self, policy, token, run_id, before_write, *, journal=None):
        self.policy = policy
        self._token = token
        self.reader = ProofAPI(policy.repository, token=token, max_calls=32)
        self.details_url = (
            f'https://github.com/{policy.repository}/actions/runs/{identifier(run_id)}'
        )
        require(callable(before_write), 'missing coordination fence')
        self._before_write = before_write
        self.run_id = identifier(run_id)
        self.journal = journal
        self._failed = False

    @classmethod
    def from_key(cls, policy, private_key, run_id, before_write, *, journal=None):
        jwt = app_jwt(private_key, policy.app_id)
        result = _request(
            'POST',
            f'https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens',
            jwt,
            {
                'repository_ids': [policy.repository_id],
                'permissions': {'checks': 'write', 'metadata': 'read'},
            },
        )
        require(
            result.get('permissions') == {'checks': 'write', 'metadata': 'read'}
            and [identifier(item['id']) for item in result['repositories']]
            == [policy.repository_id],
            'App token scope differs',
        )
        token = result['token']
        require(
            type(token) is str and 0 < len(token) <= 4096, 'invalid installation token'
        )
        return cls(policy, token, run_id, before_write, journal=journal)

    def _send(self, method, resource, body=None):
        require(
            (method == 'POST' and resource == 'check-runs')
            or (
                method in {'GET', 'PATCH'}
                and re.fullmatch(r'check-runs/[1-9][0-9]*', resource)
            ),
            'unexpected App operation',
        )
        return _request(
            method,
            f'https://api.github.com/repos/{self.policy.repository}/{resource}',
            self._token,
            body,
        )

    def _check(self, value, head, name, qid, status, conclusion):
        checks = {
            'app_id': identifier(value['app']['id']) == self.policy.app_id,
            'name': value['name'] == name,
            'head_sha': value['head_sha'] == head,
            'external_id': value['external_id'] == qid,
            'details_url': value['details_url'] == self.details_url,
            'status': value['status'] == status,
            'conclusion': value['conclusion'] == conclusion,
            'completed_at': status == 'completed' or value['completed_at'] is None,
        }
        mismatched = [field for field, matches in checks.items() if not matches]
        if mismatched:
            raise _CheckResponseError(mismatched)
        check_id = identifier(value['id'])
        return validate_check(
            value,
            self._expected(head, name, qid),
            check_id,
            status=status,
            conclusion=conclusion,
        )

    def _expected(self, head, name, qid):
        return CheckIdentity(
            self.policy.repository, self.policy.app_id, head, name, qid, self.run_id
        )

    def _identity(self, head, name, qid):
        digest(head)
        digest(qid, 64)
        require(
            name == self.policy.check_name
            or (
                name == getattr(self.policy, 'activation_name', None)
                if getattr(self.policy, 'activation_name', None) is not None
                else re.fullmatch(r'Native KWin / activation #[1-9][0-9]*', name)
            ),
            'unexpected check name',
        )
        return 'native' if name == self.policy.check_name else 'activation'

    def _context(self, head, name, qid, phase):
        require(not self._failed, 'check writer is unusable after an error')
        role = self._identity(head, name, qid)
        require(type(self.journal) is CheckJournal, 'missing one-shot check journal')
        self.journal.check_context(qid, self.run_id, phase, role)
        return role

    def begin(self, head, name, qid):
        try:
            role = self._context(head, name, qid, 'admit')
            checks = get_all(
                self.reader,
                f'commits/{head}/check-runs',
                key='check_runs',
                check_filter='all',
            )
            validate_creation(checks, self._expected(head, name, qid))
            body = {
                'name': name,
                'external_id': qid,
                'details_url': self.details_url,
                'status': 'in_progress',
                'output': {
                    'title': 'Native qualification pending',
                    'summary': 'Exact admitted operation is running.',
                },
            }
            self._before_write()
            self.journal.begin(role, 'create')
            result = self._send('POST', 'check-runs', dict(body, head_sha=head))
            check_id = self._check(result, head, name, qid, 'in_progress', None)
            require(
                self._check(
                    self._send('GET', f'check-runs/{check_id}'),
                    head,
                    name,
                    qid,
                    'in_progress',
                    None,
                )
                == check_id,
                'created check readback ID differs',
            )
            self.journal.confirm(check_id)
            return check_id
        except (
            ValueError,
            KeyError,
            TypeError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            self._failed = True
            raise CheckWriteError(
                'Cannot begin exact native check; reconcile any write'
            ) from error

    def complete(self, check_id, head, name, qid, *, success):
        try:
            role = self._context(head, name, qid, 'report')
            require(type(success) is bool, 'completion must be explicit')
            resource = f'check-runs/{identifier(check_id)}'
            require(
                self._check(
                    self._send('GET', resource), head, name, qid, 'in_progress', None
                )
                == check_id,
                'queried check ID differs',
            )
            self._before_write()
            self.journal.begin(role, 'complete', check_id=check_id)
            conclusion = 'success' if success else 'failure'
            result = self._send(
                'PATCH',
                resource,
                {
                    'status': 'completed',
                    'conclusion': conclusion,
                    'output': {
                        'title': 'Native qualification ' + conclusion,
                        'summary': 'Result is bound to the exact admitted operation.',
                    },
                },
            )
            require(
                self._check(result, head, name, qid, 'completed', conclusion)
                == check_id,
                'completed check ID differs',
            )
            require(
                self._check(
                    self._send('GET', resource),
                    head,
                    name,
                    qid,
                    'completed',
                    conclusion,
                )
                == check_id,
                'completed check readback ID differs',
            )
            self.journal.confirm(check_id)
        except (
            ValueError,
            KeyError,
            TypeError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            self._failed = True
            raise CheckWriteError(
                'Cannot complete exact native check; reconcile any write'
            ) from error
