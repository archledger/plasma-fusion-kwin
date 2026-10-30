# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only exact-merge signature checks; not admission or native evidence.

The keyring, fingerprint and signoff must come from trusted maintainer policy,
never from a candidate or its proof artifact. No key is imported or retrieved.
"""
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ci.native.merge_update import MergeUpdateError, read_exact_merge


class SignatureError(ValueError):
    """The exact candidate's signature or required signoff could not be verified."""


@dataclass(frozen=True)
class SignedMerge:
    commit: str
    tree: str
    fingerprint: str
    base: str
    head: str


def require(condition, message):
    if not condition:
        raise SignatureError(message)


def signature_fingerprint(status, expected):
    """Interpret GnuPG's documented machine status, not its human-readable UID.

    Field order: GnuPG doc/DETAILS, VALIDSIG. A valid cryptographic signature
    alone does not rule out an expired or revoked signing key.
    """
    records = [line.removeprefix('[GNUPG:] ').split() for line in status.splitlines()
               if line.startswith('[GNUPG:] ')]
    bad = {'BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'NO_PUBKEY',
           'NODATA', 'FAILURE', 'ERROR', 'BADARMOR', 'UNEXPECTED', 'KEYEXPIRED', 'SIGEXPIRED'}
    require(not any(record and record[0] in bad for record in records),
            'signature status reports failure, expiration or revocation')
    valid = [record[1:] for record in records if record and record[0] == 'VALIDSIG']
    require(len(valid) == 1 and len(valid[0]) in {9, 10}, 'expected one valid signature')
    require(sum(record[0] == 'GOODSIG' for record in records if record) == 1,
            'expected one good signature')
    values = valid[0]
    primary = values[9] if len(values) == 10 else values[0]
    require(primary == expected, 'signature is not from the pinned primary key')
    require(values[7] in {'8', '9', '10'}, 'signature must use SHA-256, SHA-384 or SHA-512')
    return primary


def verify_signed_merge(source, target, *, keyring, fingerprint, signoff):
    """Verify structure, DCO footer and OpenPGP identity without fetching or pushing."""
    require(type(fingerprint) is str and re.fullmatch(r'(?:[A-F0-9]{40}|[A-F0-9]{64})',
                                                    fingerprint), 'invalid pinned fingerprint')
    require(type(signoff) is str and signoff.startswith('Signed-off-by: ')
            and '\n' not in signoff and '\r' not in signoff, 'invalid required signoff')
    try:
        raw = read_exact_merge(source, target)
        keyring = Path(keyring).resolve(strict=True)
        require(keyring.is_dir(), 'keyring is not a directory')
    except (MergeUpdateError, OSError, TypeError) as error:
        raise SignatureError('cannot verify the exact merge or trusted keyring') from error
    headers, _, message = raw.partition('\n\n')
    require(sum(line.startswith('gpgsig ') for line in headers.split('\n')) == 1,
            'expected one embedded OpenPGP signature')
    # DCO lines anywhere in the message count; only one terminal LF is optional.
    # Do not normalize spaces/tabs into an apparently policy-exact footer.
    dco_lines = [line for line in message.split('\n')
                 if re.match(r'[ \t]*Signed-off-by[ \t]*:', line, re.IGNORECASE)]
    require(dco_lines == [signoff]
            and message.removesuffix('\n').rsplit('\n\n', 1)[-1] == signoff,
            'expected the sole required DCO footer')
    gpg = shutil.which('gpg')
    require(gpg is not None, 'GnuPG is unavailable')
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1')
    with tempfile.TemporaryDirectory(prefix='native-signature-') as temporary:
        wrapper = Path(temporary) / 'verify-gpg'
        command = [gpg, '--no-options', '--homedir', str(keyring), '--batch', '--no-tty',
                   '--no-auto-key-locate', '--no-auto-key-retrieve', '--no-auto-key-import',
                   '--no-auto-check-trustdb', '--no-autostart']
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
        wrapper.chmod(0o700)
        try:
            result = subprocess.run(
                ['git', '--no-replace-objects', '-c', 'gpg.format=openpgp',
                 '-c', 'gpg.program=' + str(wrapper), '-c', 'gpg.openpgp.program=' + str(wrapper),
                 'verify-commit', '--raw', target.commit], cwd=source, env=env,
                capture_output=True, text=True, errors='replace', timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SignatureError('signature verification did not complete') from error
    require(result.returncode == 0, 'signature verification failed')
    primary = signature_fingerprint(result.stderr, fingerprint)
    return SignedMerge(target.commit, target.tree, primary, target.base, target.head)
