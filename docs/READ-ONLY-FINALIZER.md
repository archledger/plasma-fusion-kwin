<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Read-only finalizer command

`tools/verify_native_proof.py` composes signed-merge, historical qualification,
current-association and current-policy verification. It never pushes, changes
checks, imports keys or changes repository settings. Even a successful result
contains `"merge_authorized": false`: this is an observation, not a lock or a
production merge command.

## Inputs

The operator must provide a trusted local repository containing the final signed
merge object, a trusted public verification keyring, and a trusted JSON policy.
Do not take those policy/keyring choices from a PR or a downloaded proof. The
command does not fetch missing Git objects or discover credentials automatically.

The policy must explicitly contain every `QualificationPolicy` field, plus
`signoff`: `repository`, `repository_id`, `workflow_id`, `launcher`, `fingerprint`,
`packages` (a JSON array), `app_id`, `workflow_path`, `check_name`,
`upstream_commit`, and `signoff`. No unknown or missing field is accepted.

Also supply the result artifact ID and both downloaded ZIPs described in
[the qualification contract](QUALIFICATION-EVIDENCE.md). Both ZIPs are checked
against authenticated API metadata; a filename or local receipt is not authority.
The manual producer implements this artifact format, but its operational
qualification remains pending. Old native qualification artifacts are not
interchangeable with these proof bundles.

Example, after the matching producer and trusted policy exist:

```sh
python3 tools/verify_native_proof.py \
  --policy "$TRUSTED_POLICY" --source "$TRUSTED_REPOSITORY" \
  --keyring "$TRUSTED_PUBLIC_KEYRING" \
  --artifact-id "$RESULT_ARTIFACT_ID" --artifact "$RESULT_ZIP" \
  --admission "$ADMISSION_ZIP" --timeout 120
```

`GH_TOKEN`, if explicitly supplied by the operator, is used for read-only GitHub
requests. The command does not read `NATIVE_GATE_PRIVATE_KEY` or other secret
variables. No token is passed as a command-line argument or printed.

## Boundaries and output

Verification runs in a child process with a private temporary HOME/TMPDIR,
isolated Python imports and a minimal environment. Standard input is closed.
The parent enforces the entire worker's deadline, kills its process group on
timeout or interruption, and cleans up its owned temporary directory.

Policy files are capped at 256 KiB; archives use the existing proof-size limits.
Only regular files are accepted, with final symlinks and FIFOs refused. The
worker uses a finite API request budget and explicit bounded pagination.

Exit codes:

- `0`: the read-only checks passed at observation time.
- `1`: verification failed or the worker result was invalid.
- `2`: the whole-operation deadline expired, or arguments were invalid.

Output is JSON containing the outcome and public proof identifiers. Errors do
not include raw native logs, policy contents or worker tracebacks.

Actual finalization remains blocked on serialization against new activation,
producer/check-transition validation and the remaining live integration tests.
The exact-base Git update primitive alone does not close the activation race.
Before enforcement, a valid qualification can still fail full readiness because
the required native App/check mapping is absent. Record the specific refusal
without weakening policy checks; see the [integration plan](MAIN-INTEGRATION.md).
