# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real signatures use temporary fixture keys, never the maintainer's keyring."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ci.native.merge_update import MergeCommit
from ci.native.signed_merge import (
    SignatureError,
    signature_fingerprint,
    verify_signed_merge,
)

SIGNOFF = 'Signed-off-by: Fixture <fixture@example.invalid>'


class SignedMergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='signed-merge-keys-')
        cls.keyring = Path(cls.temporary.name)
        cls.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        cls.env.update(GNUPGHOME=str(cls.keyring), GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='Fixture',
                       GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_NAME='Fixture',
                       GIT_COMMITTER_EMAIL='fixture@example.invalid')
        cls.addClassCleanup(cls.clean_keyring)
        cls.fingerprints = []
        for name in ['approved', 'other']:
            uid = f'{name} <{name}@example.invalid>'
            subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                            '--quick-generate-key', uid, 'ed25519', 'sign', '0'],
                           env=cls.env, check=True, capture_output=True, timeout=30)
            listing = subprocess.check_output(['gpg', '--batch', '--with-colons', '--list-keys', uid],
                                              env=cls.env, text=True, stderr=subprocess.PIPE)
            cls.fingerprints.append(next(line.split(':')[9] for line in listing.splitlines()
                                         if line.startswith('fpr:')))

    @classmethod
    def clean_keyring(cls):
        subprocess.run(['gpgconf', '--homedir', str(cls.keyring), '--kill', 'gpg-agent'],
                       env=cls.env, check=False, capture_output=True, timeout=15)
        cls.temporary.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='signed-merge-repo-')
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name)
        self.git('init', '-q')
        self.git('config', 'gpg.program', shutil.which('gpg'))
        self.tree = self.git('mktree', input='')
        self.base = self.commit('Base')
        self.head = self.commit('Head', self.base)
        self.merge = self.commit('Approved\n\n' + SIGNOFF, self.base, self.head,
                                 signer=self.fingerprints[0])
        self.target = MergeCommit(self.base, self.head, self.merge, self.tree)

    def git(self, *arguments, input=None):
        return subprocess.check_output(['git', *arguments], cwd=self.source, env=self.env,
                                       text=True, input=input, stderr=subprocess.PIPE).strip()

    def commit(self, message, *parents, signer=None):
        arguments = ['commit-tree', self.tree]
        for parent in parents:
            arguments.extend(['-p', parent])
        if signer:
            arguments.append('-S' + signer)
        return self.git(*arguments, input=message + '\n')

    def verify(self, *, signer=None):
        return verify_signed_merge(self.source, self.target, keyring=self.keyring,
                                   fingerprint=signer or self.fingerprints[0], signoff=SIGNOFF)

    def test_valid_signature_and_exact_merge_identity_pass(self):
        verified = self.verify()
        self.assertEqual(verified.commit, self.merge)
        self.assertEqual(verified.fingerprint, self.fingerprints[0])
        self.assertEqual(verified.tree, self.tree)

    def test_another_valid_signer_is_rejected(self):
        with self.assertRaises(SignatureError):
            self.verify(signer=self.fingerprints[1])

    def test_unsigned_merge_is_rejected(self):
        commit = self.commit('Approved\n\n' + SIGNOFF, self.base, self.head)
        self.target = MergeCommit(self.base, self.head, commit, self.tree)
        with self.assertRaises(SignatureError):
            self.verify()

    def test_missing_public_key_does_not_import_or_retrieve_it(self):
        with tempfile.TemporaryDirectory(prefix='empty-verification-keyring-') as directory, \
                self.assertRaises(SignatureError):
            verify_signed_merge(self.source, self.target, keyring=Path(directory),
                                fingerprint=self.fingerprints[0], signoff=SIGNOFF)

    def test_tampered_signed_message_is_rejected(self):
        raw = self.git('cat-file', 'commit', self.merge).replace('Approved', 'Tampered') + '\n'
        commit = self.git('hash-object', '-t', 'commit', '-w', '--stdin', input=raw)
        self.target = MergeCommit(self.base, self.head, commit, self.tree)
        with self.assertRaises(SignatureError):
            self.verify()

    def test_wrong_or_extra_attribution_trailer_is_rejected(self):
        for footer in ['Signed-off-by: Other <other@example.invalid>',
                       SIGNOFF + '\nCo-Authored-By: Other <other@example.invalid>']:
            with self.subTest(footer=footer):
                commit = self.commit('Approved\n\n' + footer, self.base, self.head,
                                     signer=self.fingerprints[0])
                self.target = MergeCommit(self.base, self.head, commit, self.tree)
                with self.assertRaises(SignatureError):
                    self.verify()

    def test_separated_duplicate_or_other_signoffs_are_rejected(self):
        for earlier in [SIGNOFF, 'Signed-off-by: Other <other@example.invalid>',
                        'signed-off-by: Other <other@example.invalid>',
                        '\tSigned-off-by : Other <other@example.invalid>']:
            with self.subTest(earlier=earlier):
                message = 'Approved\n\n' + earlier + '\n\n' + SIGNOFF
                commit = self.commit(message, self.base, self.head, signer=self.fingerprints[0])
                self.target = MergeCommit(self.base, self.head, commit, self.tree)
                with self.assertRaises(SignatureError):
                    self.verify()

    def test_footer_whitespace_is_not_normalized(self):
        for footer in [SIGNOFF + ' ', SIGNOFF + '\t', SIGNOFF + '\r', SIGNOFF + '\n',
                       ' ' + SIGNOFF, '\t' + SIGNOFF]:
            with self.subTest(footer=footer):
                message = 'Approved\n\n' + footer
                commit = self.commit(message, self.base, self.head, signer=self.fingerprints[0])
                self.target = MergeCommit(self.base, self.head, commit, self.tree)
                raw = subprocess.check_output(['git', 'cat-file', 'commit', commit],
                                              cwd=self.source, env=self.env)
                self.assertEqual(raw.partition(b'\n\n')[2], (message + '\n').encode())
                with self.assertRaises(SignatureError):
                    self.verify()

    def test_signature_does_not_excuse_wrong_parents(self):
        self.target = MergeCommit(self.head, self.base, self.merge, self.tree)
        with self.assertRaises(SignatureError):
            self.verify()

    def test_repository_cannot_substitute_a_signature_verifier(self):
        fake = self.source / 'fake-gpg'
        marker = self.source / 'verifier-was-run'
        fake.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
        fake.chmod(0o700)
        self.git('config', 'gpg.program', str(fake))
        self.git('config', 'gpg.openpgp.program', str(fake))
        self.git('config', 'gpg.format', 'ssh')
        self.verify()
        self.assertFalse(marker.exists())


class SignatureStatusTest(unittest.TestCase):
    fingerprint = 'A' * 40

    def status(self, *, signer=None, digest='8'):
        return ('[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG 1234 Fixture\n'
                f'[GNUPG:] VALIDSIG {signer or self.fingerprint} 2026-10-30 '
                f'1793383200 0 4 0 22 {digest} 00 {self.fingerprint}\n')

    def test_verified_signing_subkey_is_bound_to_its_primary_fingerprint(self):
        self.assertEqual(signature_fingerprint(self.status(signer='B' * 40), self.fingerprint),
                         self.fingerprint)

    def test_expired_revoked_and_failed_status_overrides_validsig(self):
        for name in ['EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'BADSIG', 'ERRSIG',
                     'KEYEXPIRED', 'SIGEXPIRED', 'ERROR', 'FAILURE', 'NO_PUBKEY', 'NODATA']:
            with self.subTest(name=name), self.assertRaises(SignatureError):
                signature_fingerprint(self.status() + f'[GNUPG:] {name} 1234\n',
                                      self.fingerprint)

    def test_weak_digest_is_rejected(self):
        with self.assertRaises(SignatureError):
            signature_fingerprint(self.status(digest='2'), self.fingerprint)

    def test_multiple_signatures_and_malformed_status_are_rejected(self):
        for status in [self.status() * 2, '[GNUPG:] VALIDSIG invalid\n',
                       self.status().replace('GOODSIG', 'UNKNOWN'), 'Fixture: good signature']:
            with self.subTest(status=status), self.assertRaises(SignatureError):
                signature_fingerprint(status, self.fingerprint)


if __name__ == '__main__':
    unittest.main()
