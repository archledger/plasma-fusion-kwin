# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Entrypoint for the four pinned trusted-native jobs; never merge or dispatch."""

import argparse
import json
import subprocess
import zipfile
from pathlib import Path

from ci.native.coordinator import coordinate
from ci.native.producer import (
    Context,
    admit_job,
    native_job,
    prepare_job,
    report_job,
    request_job,
)
from ci.native.proof_api import object_pairs
from ci.native.readiness import load_policy, read_regular


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'role',
        choices=['prepare', 'request', 'admit', 'native', 'report', 'coordinate'],
    )
    parser.add_argument('--source', type=Path)
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--keyring', type=Path)
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.role == 'coordinate':
            if not all(
                [args.source, args.policy, args.keyring, args.admission, args.state]
            ) or (args.apply and not args.journal):
                parser.error(
                    'coordinate requires source/policy/keyring/admission/state; apply also requires a new journal'
                )
            policy, signoff = load_policy(read_regular(args.policy, 256 * 1024))
            admission = json.loads(
                read_regular(args.admission, 65536), object_pairs_hook=object_pairs
            )
            anchor = json.loads(
                read_regular(args.state, 65536), object_pairs_hook=object_pairs
            )
            result = coordinate(
                args.source,
                policy,
                signoff,
                args.keyring,
                admission,
                anchor,
                args.journal,
                apply=args.apply,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        result = {
            'prepare': prepare_job,
            'request': request_job,
            'admit': admit_job,
            'native': native_job,
            'report': report_job,
        }[args.role](Context())
        return 1 if result is False else 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as error:
        # Do not echo secrets or arbitrary API/guest payloads into a privileged log.
        print(
            json.dumps(
                {
                    'success': False,
                    'phase': args.role,
                    'error_type': type(error).__name__,
                }
            )
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
