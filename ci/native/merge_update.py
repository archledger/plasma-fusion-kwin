# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact-base Git update primitive, not a complete native merge authorizer.

The future maintainer finalizer must first verify immutable admission, signed
history, native evidence, and repository protections. Nothing in this module
authenticates an evidence record. There is deliberately no command-line entry
point. Callers must supply a trusted local repository and allowlisted endpoint.
"""
import os
import re
import subprocess
from dataclasses import dataclass

MAX_COMMIT_BYTES = 64 * 1024


class MergeUpdateError(ValueError):
    """An update was refused or failed; a failed push needs remote reconciliation."""


@dataclass(frozen=True)
class MergeCommit:
    base: str
    head: str
    commit: str
    tree: str


@dataclass(frozen=True)
class UpdateResult:
    commit: str
    applied: bool


def require(condition, message):
    if not condition:
        raise MergeUpdateError(message)


def git(source, *arguments, raw=False):
    # Preserve the maintainer's configured authentication and pre-push hooks,
    # but ignore ambient object-directory overrides and replacement objects.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1')
    try:
        result = subprocess.run(['git', '--no-replace-objects', *arguments], cwd=source,
                                env=env, capture_output=True, text=True, errors='surrogateescape',
                                timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MergeUpdateError('Git operation did not complete; reconcile remote state') from error
    require(result.returncode == 0, f'git {arguments[0]} refused or failed; reconcile remote state')
    return result.stdout if raw else result.stdout.strip()


def read_exact_merge(source, target):
    """Read the bounded raw object only after checking its exact tree and parents."""
    require(type(target) is MergeCommit, 'expected an immutable merge target')
    for value in (target.base, target.head, target.commit, target.tree):
        require(type(value) is str and re.fullmatch(r'[0-9a-f]{40}', value)
                and value != '0' * 40, 'invalid Git object identity')
    require(len({target.base, target.head, target.commit}) == 3,
            'expected a distinct merge commit')
    require(git(source, 'cat-file', '-t', target.commit) == 'commit',
            'qualified object is not a commit')
    size = git(source, 'cat-file', '-s', target.commit)
    require(re.fullmatch(r'[0-9]{1,8}', size) and 0 < int(size) <= MAX_COMMIT_BYTES,
            'qualified commit is oversized or has an invalid size')
    # Pretty-print history can honor local grafts even with replacement objects
    # disabled. Only raw object headers describe the commit actually pushed.
    raw = git(source, 'cat-file', 'commit', target.commit, raw=True)
    require(len(raw.encode('utf-8', errors='surrogateescape')) == int(size),
            'qualified commit size differs')
    headers, separator, _ = raw.partition('\n\n')
    require(separator, 'qualified commit has no header terminator')
    lines = headers.split('\n')
    trees = [line for line in lines if line.startswith('tree ')]
    parents = [line for line in lines if line.startswith('parent ')]
    require(trees == [f'tree {target.tree}']
            and parents == [f'parent {target.base}', f'parent {target.head}']
            and lines[:3] == trees + parents,
            'qualified tree or ordered merge parents differ')
    return raw


def push_exact_merge(source, endpoint, target, *, apply=False):
    """Update only refs/heads/main from the expected base to the exact merge.

    Source: https://git-scm.com/docs/git-push (--force-with-lease=<ref>:<expect>).
    A lease by itself allows rewrites: require the actual ordered merge parents
    first so the only permitted update is a fast-forward from target.base.
    The default performs Git's dry run. An ambiguous failed push must never be
    retried blindly; this function does not roll back or change protection rules.
    """
    require(type(apply) is bool, 'apply must be an explicit boolean')
    require(type(endpoint) is str and endpoint and not endpoint.startswith('-')
            and not any(ord(char) < 32 or ord(char) == 127 for char in endpoint),
            'invalid trusted endpoint')
    read_exact_merge(source, target)
    ref = 'refs/heads/main'
    observed = git(source, 'ls-remote', '--exit-code', '--refs', '--', endpoint, ref)
    require(observed == f'{target.base}\t{ref}', 'remote base changed or is ambiguous')
    arguments = ['push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no',
                 f'--force-with-lease={ref}:{target.base}']
    if not apply:
        arguments.append('--dry-run')
    git(source, *arguments, '--', endpoint, f'{target.commit}:{ref}')
    return UpdateResult(target.commit, apply)
