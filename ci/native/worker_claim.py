# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot local acceptance of an already authenticated worker claim.

No grant signing, ref updates, polling or workload launch occurs here. The caller
must verify the signed snapshot and current coordination ownership separately.
Never deserialize SignedCoordination from worker-supplied data.
"""
import os
import uuid
from pathlib import Path

from ci.native.coordination import SignedCoordination, validate_record
from ci.native.qualification import QualificationPolicy, digest, identifier, require


class WorkerClaimError(ValueError):
    """The invocation cannot accept this claim, or its marker is already used."""


class WorkerClaim:
    def __init__(self, directory, policy, *, run_id, job_id):
        """Create a fresh invocation; there is deliberately no nonce/resume input.

        directory is a caller-owned private invocation workspace. Do not remove
        its marker to retry. A restarted invocation receives a different nonce.
        """
        require(type(policy) is QualificationPolicy, 'expected trusted policy')
        self._policy = policy
        self._run_id, self._job_id = identifier(run_id), identifier(job_id)
        self._directory = Path(directory).resolve(strict=True)
        self._nonce = uuid.uuid4().hex

    @property
    def nonce(self):
        return self._nonce

    def accept(self, signed):
        """Record local acceptance once; return the commit, not launch authority."""
        try:
            require(type(signed) is SignedCoordination
                    and signed.fingerprint == self._policy.fingerprint, 'unauthenticated claim')
            validate_record(signed.record, self._policy)
            require(signed.record['state'] == 'running', 'worker has no running claim')
            grant = signed.record['grant']
            require(grant['run_id'] == self._run_id and grant['native_job_id'] == self._job_id
                    and grant['worker_nonce'] == self._nonce, 'claim belongs to another invocation')
            payload = (digest(signed.commit) + '\n').encode('ascii')
            directory = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(directory)
                require(info.st_uid == os.geteuid() and not info.st_mode & 0o022,
                        'workspace is not privately writable by this user')
                marker = os.open('worker-claim', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
                try:
                    require(os.write(marker, payload) == len(payload), 'incomplete claim marker')
                finally:
                    os.close(marker)
            finally:
                os.close(directory)
            return signed.commit
        except (OSError, ValueError, TypeError, KeyError) as error:
            # A created marker survives every failure; failure is not permission
            # to retry an invocation whose acceptance may have partially completed.
            raise WorkerClaimError('cannot accept this worker claim once') from error
