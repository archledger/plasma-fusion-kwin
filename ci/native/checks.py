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
        error.close()
        raise CheckWriteError(
            'App operation failed or is uncertain; reconcile before retry'
        ) from error
    except (URLError, HTTPException, OSError, ValueError) as error:
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
    def __init__(self, policy, token, run_id, before_write):
        self.policy = policy
        self._token = token
        self.reader = ProofAPI(policy.repository, token=token, max_calls=32)
        self.details_url = (
            f'https://github.com/{policy.repository}/actions/runs/{identifier(run_id)}'
        )
        require(callable(before_write), 'missing coordination fence')
        self._before_write = before_write

    @classmethod
    def from_key(cls, policy, private_key, run_id, before_write):
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
        return cls(policy, token, run_id, before_write)

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
        require(
            identifier(value['app']['id']) == self.policy.app_id
            and value['name'] == name
            and value['head_sha'] == head
            and value['external_id'] == qid
            and value['details_url'] == self.details_url
            and value['status'] == status
            and value['conclusion'] == conclusion,
            'check response does not match the operation',
        )
        return identifier(value['id'])

    def _identity(self, head, name, qid):
        digest(head)
        digest(qid, 64)
        require(
            name == self.policy.check_name
            or re.fullmatch(r'Native KWin / activation #[1-9][0-9]*', name),
            'unexpected check name',
        )

    def begin(self, head, name, qid):
        try:
            self._identity(head, name, qid)
            checks = get_all(
                self.reader,
                f'commits/{head}/check-runs',
                key='check_runs',
                check_filter='all',
            )
            matched = [
                c
                for c in checks
                if c['name'] == name and c['app']['id'] == self.policy.app_id
            ]
            require(len(matched) <= 1, 'duplicate App-owned checks')
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
            if matched:
                require(
                    matched[0]['head_sha'] == head, 'existing check has wrong commit'
                )
                result = self._send(
                    'PATCH', f'check-runs/{identifier(matched[0]["id"])}', body
                )
                require(result['id'] == matched[0]['id'], 'updated check ID differs')
            else:
                result = self._send('POST', 'check-runs', dict(body, head_sha=head))
            return self._check(result, head, name, qid, 'in_progress', None)
        except (ValueError, KeyError, TypeError) as error:
            raise CheckWriteError(
                'Cannot begin exact native check; reconcile any write'
            ) from error

    def complete(self, check_id, head, name, qid, *, success):
        try:
            self._identity(head, name, qid)
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
        except (ValueError, KeyError, TypeError) as error:
            raise CheckWriteError(
                'Cannot complete exact native check; reconcile any write'
            ) from error
