# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-process write intent, never authority, replay recovery or an API lock.

A missing API acknowledgment leaves the action pending. A persistence failure
can leave either snapshot on disk, but poisons the writer in both cases. A new
instance cannot adopt an existing file. Local durability cannot guarantee an
artifact upload after runner loss; callers keep authority held on uncertainty.
"""

import copy
import os
import stat
import tempfile
from pathlib import Path

from ci.native.qualification import (
    QualificationError,
    canonical_json,
    digest,
    identifier,
    require,
)

MAX_JOURNAL_BYTES = 64 * 1024


def _write_all(fd, raw):
    remaining = memoryview(raw)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            raise OSError('journal write made no progress')
        remaining = remaining[count:]
    os.fsync(fd)


class CheckJournal:
    def __init__(self, path, *, qualification_id, run_id, nonce, phase):
        digest(qualification_id, 64)
        identifier(run_id)
        digest(nonce, 32)
        require(
            type(phase) is str and phase in {'admit', 'report'}, 'invalid journal phase'
        )
        self._failed = False
        self._inode = None
        self._value = {
            'schema_version': 1,
            'qualification_id': qualification_id,
            'run_id': run_id,
            'nonce': nonce,
            'phase': phase,
            'writes': [],
        }
        try:
            path = Path(path)
            parent = path.parent.resolve(strict=True)
            info = parent.stat()
            require(
                info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'journal directory must be private',
            )
            self.path = parent / path.name
            self._persist(self._value, initial=True)
        except (OSError, ValueError, TypeError) as error:
            raise QualificationError(
                'cannot create a new private check journal'
            ) from error

    def _persist(self, value, *, initial=False):
        require(not self._failed, 'check journal is unusable after persistence failure')
        temporary = None
        try:
            parent = self.path.parent.stat()
            require(
                parent.st_uid == os.geteuid() and parent.st_mode & 0o077 == 0,
                'journal directory is no longer private',
            )
            raw = canonical_json(value) + b'\n'
            require(len(raw) <= MAX_JOURNAL_BYTES, 'check journal exceeds size limit')
            if initial:
                fd = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
            else:
                info = self.path.lstat()
                require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and (info.st_dev, info.st_ino) == self._inode
                    and info.st_uid == os.geteuid()
                    and info.st_mode & 0o077 == 0,
                    'journal path or ownership changed',
                )
                fd, name = tempfile.mkstemp(
                    prefix='.' + self.path.name + '.', dir=self.path.parent
                )
                temporary = Path(name)
            try:
                _write_all(fd, raw)
                info = os.fstat(fd)
            finally:
                os.close(fd)
            if temporary is not None:
                os.replace(temporary, self.path)
                temporary = None
            self._inode = info.st_dev, info.st_ino
            directory = os.open(
                self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, TypeError) as error:
            self._failed = True
            raise QualificationError(
                'cannot persist check write intent; do not retry'
            ) from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    self._failed = True
                    raise QualificationError(
                        'cannot remove owned journal staging file'
                    ) from error

    def check_context(self, qualification_id, run_id, phase, role):
        """Reject a mismatched operation or any attempted/pending role before I/O."""
        require(not self._failed, 'check journal is unusable after persistence failure')
        require(
            (qualification_id, run_id, phase)
            == (
                self._value['qualification_id'],
                self._value['run_id'],
                self._value['phase'],
            )
            and type(run_id) is int,
            'check journal belongs to another operation or phase',
        )
        require(
            not any(
                w['status'] == 'pending' or w['role'] == role
                for w in self._value['writes']
            ),
            'check write is pending or already attempted',
        )

    def begin(self, role, action, *, check_id=None):
        require(not self._failed, 'check journal is unusable after persistence failure')
        writes = self._value['writes']
        require(
            type(role) is str and role in {'native', 'activation'}, 'invalid check role'
        )
        require(
            action == ('create' if self._value['phase'] == 'admit' else 'complete'),
            'action does not match journal phase',
        )
        require(
            not any(w['status'] == 'pending' or w['role'] == role for w in writes),
            'check write is pending or already attempted',
        )
        if action == 'create':
            require(check_id is None, 'creation cannot adopt a check ID')
        else:
            identifier(check_id)
            require(
                all(w['acknowledged_check_id'] != check_id for w in writes),
                'check roles share an ID',
            )
        value = copy.deepcopy(self._value)
        value['writes'].append(
            {
                'role': role,
                'action': action,
                'expected_check_id': check_id,
                'acknowledged_check_id': None,
                'status': 'pending',
            }
        )
        self._persist(value)
        self._value = value

    def confirm(self, check_id):
        try:
            self._confirm(check_id)
        except (ValueError, OSError, TypeError) as error:
            self._failed = True
            raise QualificationError(
                'cannot acknowledge check write; do not retry'
            ) from error

    def _confirm(self, check_id):
        require(not self._failed, 'check journal is unusable after persistence failure')
        identifier(check_id)
        writes = self._value['writes']
        require(writes and writes[-1]['status'] == 'pending', 'no pending check write')
        pending = writes[-1]
        require(
            pending['expected_check_id'] in {None, check_id},
            'acknowledged check ID differs',
        )
        require(
            all(w['acknowledged_check_id'] != check_id for w in writes[:-1]),
            'check roles share an ID',
        )
        value = copy.deepcopy(self._value)
        value['writes'][-1].update(acknowledged_check_id=check_id, status='confirmed')
        self._persist(value)
        self._value = value
