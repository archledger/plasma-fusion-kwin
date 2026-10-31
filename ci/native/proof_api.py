# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded GitHub JSON reads for finalizer evidence, never API mutations.

Repository and optional read token are trusted operator inputs. This transport
does not make returned data an authorization; the proof layer must bind every
response to its expected repository, run, candidate and policy.
"""
import json
import math
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_INTEGER_DIGITS = 128
RESOURCES = re.compile(
    r'(?:|actions/runs/[1-9][0-9]*(?:/attempts/[1-9][0-9]*/jobs|/artifacts)?'
    r'|actions/(?:artifacts|jobs|workflows)/[1-9][0-9]*'
    r'|check-runs/[1-9][0-9]*|commits/[0-9a-f]{40}/check-runs'
    r'|pulls/[1-9][0-9]*(?:/(?:reviews|commits))?'
    r'|git/commits/[0-9a-f]{40}'
    r'|git/ref/(?:heads/main|pull/[1-9][0-9]*/(?:head|merge))'
    r'|branches/main/protection(?:/required_signatures)?'
    r'|rules/branches/main'
    r'|collaborators/[A-Za-z0-9][A-Za-z0-9-]{0,38}/permission)'
)


class ProofReadError(ValueError):
    """Required evidence could not be retrieved safely."""


def require(condition, message):
    if not condition:
        raise ProofReadError(message)


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON field in GitHub response')
        result[key] = value
    return result


def reject_constant(_):
    raise ProofReadError('non-JSON number in GitHub response')


def finite_float(text):
    value = float(text)
    require(math.isfinite(value), 'non-finite number in GitHub response')
    return value


def bounded_integer(text):
    require(len(text.lstrip('-')) <= MAX_INTEGER_DIGITS, 'GitHub integer exceeds size limit')
    return int(text)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProofAPI:
    def __init__(self, repository, *, token=None, max_calls=32):
        require(type(repository) is str and re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', repository)
            and repository.split('/')[1] not in {'.', '..'}, 'invalid repository')
        require(token is None or (type(token) is str and 0 < len(token) <= 4096
                                  and all(33 <= ord(char) <= 126 for char in token)),
                'invalid read token')
        require(type(max_calls) is int and 1 <= max_calls <= 64, 'invalid request budget')
        self.repository = repository
        self._token = token
        self._remaining = max_calls
        self._redirect_handler = NoRedirect()
        self._opener = build_opener(self._redirect_handler)

    def get(self, resource, *, page=None, per_page=100, check_filter=None):
        """Read an allowlisted endpoint; list pagination is explicit, never automatic."""
        require(type(resource) is str and len(resource) <= 512 and RESOURCES.fullmatch(resource),
                'unexpected GitHub evidence endpoint')
        require(type(per_page) is int and 1 <= per_page <= 100, 'invalid page size')
        require(page is None or (type(page) is int and 1 <= page <= 10000), 'invalid page')
        require(check_filter is None or (type(check_filter) is str and check_filter in {'all', 'latest'}
                and re.fullmatch(r'commits/[0-9a-f]{40}/check-runs', resource)),
                'invalid check-run filter')
        require(self._remaining > 0, 'GitHub request budget exhausted')
        url = 'https://api.github.com/repos/' + self.repository
        if resource:
            url += '/' + resource
        query = {} if page is None else {'page': page, 'per_page': per_page}
        if check_filter is not None:
            query['filter'] = check_filter
        if query:
            url += '?' + urlencode(query)
        headers = {'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'plasma-fusion-kwin-finalizer'}
        if self._token is not None:
            headers['Authorization'] = 'Bearer ' + self._token
        request = Request(url, headers=headers, method='GET')
        self._remaining -= 1
        try:
            with self._opener.open(request, timeout=20) as response:
                require(response.status == 200, 'GitHub evidence is not a complete response')
                require(response.headers.get_content_type() == 'application/json',
                        'GitHub evidence is not JSON')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            raise ProofReadError(f'GitHub evidence unavailable (HTTP {status})') from None
        except (URLError, OSError, HTTPException):
            raise ProofReadError('GitHub evidence transport failed') from None
        require(len(raw) <= MAX_RESPONSE_BYTES, 'GitHub evidence exceeds size limit')
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs,
                               parse_constant=reject_constant, parse_float=finite_float,
                               parse_int=bounded_integer)
        except ProofReadError:
            raise
        except (ValueError, RecursionError):
            raise ProofReadError('malformed GitHub evidence JSON') from None
        require(type(value) in {dict, list}, 'unexpected GitHub evidence shape')
        return value
