# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compose the no-push proof checks; operator policy is the trust root."""
import json
import os
import stat
from dataclasses import asdict, fields

from ci.native.coordination import MAX_RECORD_BYTES, read_signed_coordination
from ci.native.current_policy import verify_current_policy
from ci.native.merge_update import MergeCommit
from ci.native.proof_api import (
    ProofAPI,
    bounded_integer,
    finite_float,
    object_pairs,
    reject_constant,
)
from ci.native.qualification import (
    MAX_ARCHIVE_BYTES,
    MAX_RECEIPT_BYTES,
    MAX_REPORT_BYTES,
    QualificationError,
    QualificationPolicy,
    digest,
    identifier,
    read_archive,
    require,
    verify_qualification,
)
from ci.native.signed_merge import SignatureError, verify_signed_merge

MAX_POLICY_BYTES = 256 * 1024
MAX_ANCHOR_BYTES = MAX_RECORD_BYTES


def read_regular(path, maximum):
    """Read a bounded regular input without following a final symlink or blocking on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_size <= maximum, 'unsafe or oversized input file')
            raw = stream.read(maximum + 1)
            require(len(raw) <= maximum, 'input file grew beyond its limit')
            return raw
    except OSError:
        raise QualificationError('cannot read a required regular input file') from None


def load_policy(raw):
    """Parse explicit trusted operator policy, never a policy from a proof artifact."""
    try:
        require(type(raw) is bytes and len(raw) <= MAX_POLICY_BYTES, 'invalid policy size')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs,
                           parse_constant=reject_constant, parse_int=bounded_integer,
                           parse_float=finite_float)
        names = {field.name for field in fields(QualificationPolicy)}
        require(type(value) is dict and set(value) == names | {'signoff'}, 'incomplete or unknown policy fields')
        signoff = value.pop('signoff')
        require(type(signoff) is str and signoff.startswith('Signed-off-by: ')
                and '\n' not in signoff and '\r' not in signoff, 'invalid policy signoff')
        require(type(value['packages']) is list, 'invalid policy package inventory')
        value['packages'] = tuple(value['packages'])
        return QualificationPolicy(**value), signoff
    except QualificationError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise QualificationError('invalid operator policy') from None


def _validate_coordination_anchor(value):
    require(
        type(value) is dict
        and set(value) == {'commit', 'parent', 'generation', 'epoch', 'cutover_digest'},
        'invalid coordination anchor fields',
    )
    digest(value['commit'])
    generation = value['generation']
    require(
        type(generation) is int and 0 <= generation < 2**63,
        'invalid coordination anchor generation',
    )
    require(
        (value['parent'] is None) == (generation == 0),
        'invalid coordination anchor parent',
    )
    if value['parent'] is not None:
        digest(value['parent'])
    digest(value['epoch'], 32)
    digest(value['cutover_digest'], 64)
    return dict(value)


def load_coordination_anchor(raw):
    """Parse an explicit operator anchor, never discover one from a proof or ref."""
    try:
        require(
            type(raw) is bytes and len(raw) <= MAX_ANCHOR_BYTES,
            'invalid coordination anchor size',
        )
        value = json.loads(
            raw.decode('utf-8'),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
            parse_int=bounded_integer,
            parse_float=finite_float,
        )
        return _validate_coordination_anchor(value)
    except QualificationError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise QualificationError('invalid operator coordination anchor') from None


def verify_coordination_anchor(api, policy, *, source, keyring, signoff, anchor):
    """Read one local signed state and match its fixed remote tip; never fetch.

    Signature and ref equality do not authorize a transition, establish
    quiescence or make the operation currently ready. The operator owns the
    anchor and its protocol history. Current-state predicates are separate.
    """
    try:
        expected = _validate_coordination_anchor(anchor)
        require(
            type(policy) is QualificationPolicy and api.repository == policy.repository,
            'wrong coordination policy or repository',
        )
        state = read_signed_coordination(
            source,
            expected['commit'],
            policy=policy,
            keyring=keyring,
            signoff=signoff,
            expected_parent=expected['parent'],
            expected_generation=expected['generation'],
            expected_epoch=expected['epoch'],
            expected_cutover_digest=expected['cutover_digest'],
        )
        ref = api.get('git/ref/heads/native-gate-coordination')
        require(
            ref['ref'] == 'refs/heads/native-gate-coordination'
            and ref['object']['type'] == 'commit'
            and digest(ref['object']['sha']) == expected['commit'],
            'coordination ref differs from the trusted anchor',
        )
        return state
    except QualificationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        raise QualificationError(
            'cannot verify the trusted coordination anchor'
        ) from None


def verify_readiness(
    api, policy, *, source, keyring, signoff, artifact_id, archive, admission_archive
):
    """Verify historical proof plus current policy; never invoke a Git update."""
    try:
        require(api.repository == policy.repository, 'wrong proof API repository')
        artifact = api.get('actions/artifacts/' + str(identifier(artifact_id)))
        require(identifier(artifact['id']) == artifact_id and artifact['expired'] is False
                and identifier(artifact['workflow_run']['repository_id']) == policy.repository_id
                and identifier(artifact['workflow_run']['head_repository_id']) == policy.repository_id
                and artifact['workflow_run']['head_sha'] == policy.launcher, 'wrong initial artifact provenance')
        content = read_archive(archive, artifact,
                               {'qualification.json': MAX_RECEIPT_BYTES, 'report.json': MAX_REPORT_BYTES})
        admission = content['qualification.json']['identity']['admission']
        target = MergeCommit(admission['base'], admission['head'], admission['signed_merge'], admission['tree'])
        signed = verify_signed_merge(source, target, keyring=keyring,
                                     fingerprint=policy.fingerprint, signoff=signoff)
        evidence = verify_qualification(api, policy, signed, artifact_id=artifact_id,
                                        archive=archive, admission_archive=admission_archive)
        snapshot = verify_current_policy(api, policy, evidence)
        require(evidence.signed_merge == signed
                and snapshot.qualification_id == evidence.qualification_id
                and evidence.check_run_id in snapshot.required_check_ids,
                'proof layers returned mismatched identities')
        return {'ok': True, 'mode': 'read-only', 'merge_authorized': False,
                'qualification_id': evidence.qualification_id, 'run_id': evidence.run_id,
                'signed_merge': signed.commit, 'base': signed.base, 'head': signed.head,
                'pr_number': evidence.producer_attestation.pr_number,
                'native_test_entries': evidence.tests['qt_test_passed'], 'snapshot': asdict(snapshot)}
    except QualificationError:
        raise
    except SignatureError as error:
        raise QualificationError(str(error)) from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise QualificationError('invalid read-only proof inputs') from None


def _worker(options):
    """Internal worker; the command-line parent enforces the whole-operation deadline."""
    try:
        policy, signoff = load_policy(read_regular(options['policy'], MAX_POLICY_BYTES))
        api = ProofAPI(policy.repository, token=os.environ.get('GH_TOKEN'), max_calls=64)
        value = verify_readiness(api, policy, source=options['source'], keyring=options['keyring'], signoff=signoff,
                                 artifact_id=options['artifact_id'],
                                 archive=read_regular(options['artifact'], MAX_ARCHIVE_BYTES),
                                 admission_archive=read_regular(options['admission'], MAX_ARCHIVE_BYTES))
    except QualificationError as error:
        value = {'ok': False, 'mode': 'read-only', 'merge_authorized': False, 'error': str(error)}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        value = {'ok': False, 'mode': 'read-only', 'merge_authorized': False,
                 'error': 'unexpected read-only verification failure'}
    print(json.dumps(value, sort_keys=True))
    return 0 if value['ok'] else 1
