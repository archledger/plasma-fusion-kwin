<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Read-only finalizer command

`tools/verify_native_proof.py` composes signed-merge, v2 qualification,
current signed-operation, current-association and current-policy verification. It never pushes, changes
checks, imports keys or changes repository settings. Even a successful result
contains `"merge_authorized": false`: this is an observation, not a lock or a
production merge command.

## Inputs

The operator must provide a trusted local repository containing the final signed
merge and coordination objects, a trusted public verification keyring, a trusted
JSON policy and an explicit `--coordination-anchor` file.
Do not take those policy/keyring choices from a PR or a downloaded proof. The
command does not fetch missing Git objects or discover credentials automatically.

The anchor must come from the coordinator or a reviewed reconciliation, never a
proof artifact or an automatically selected latest ref. It contains exactly:

```json
{
  "commit": "<exact coordination commit>",
  "parent": "<exact parent commit>",
  "generation": 1,
  "epoch": "<32 lowercase hexadecimal characters>",
  "cutover_digest": "<64 lowercase hexadecimal characters>"
}
```

The placeholders illustrate fields, not usable identifiers. `parent` may be
null only at generation zero; that bootstrap state cannot satisfy readiness.
Local signature, DCO, raw-parent, schema and policy checks must pass. The fixed
`refs/heads/native-gate-coordination` tip is checked before and after readiness
observations and must still equal the supplied commit. These checks authenticate
the snapshot, not the transition history or runtime quiescence.

The policy must explicitly contain every `QualificationPolicy` field, plus
`signoff`: `repository`, `repository_id`, `workflow_id`, `launcher`, `fingerprint`,
`packages` (a JSON array), `app_id`, `workflow_path`, `check_name`,
`upstream_commit`, and `signoff`. No unknown or missing field is accepted.

Also supply the result artifact ID and both downloaded ZIPs described in
[the qualification contract](QUALIFICATION-EVIDENCE.md). Both ZIPs are checked
against authenticated API metadata; a filename or local receipt is not authority.
The manual producer implements the v2 artifact format, but its operational
qualification remains pending. Old native qualification artifacts are not
interchangeable with these proof bundles.

Example, after the matching producer and trusted policy exist:

```sh
python3 tools/verify_native_proof.py \
  --policy "$TRUSTED_POLICY" --source "$TRUSTED_REPOSITORY" \
  --keyring "$TRUSTED_PUBLIC_KEYRING" \
  --artifact-id "$RESULT_ARTIFACT_ID" --artifact "$RESULT_ZIP" \
  --admission "$ADMISSION_ZIP" --coordination-anchor "$TRUSTED_ANCHOR" \
  --timeout 120
```

`GH_TOKEN`, if explicitly supplied by the operator, is used for read-only GitHub
requests. The command does not read `NATIVE_GATE_PRIVATE_KEY` or other secret
variables. No token is passed as a command-line argument or printed.

## Boundaries and output

Verification runs in a child process with a private temporary HOME/TMPDIR,
isolated Python imports and a minimal environment. Standard input is closed.
The parent enforces the entire worker's deadline, kills its process group on
timeout or interruption, and cleans up its owned temporary directory.

Policy files are capped at 256 KiB and anchor files at 64 KiB; archives use the
existing proof-size limits. Missing or invalid anchor input is refused before
the worker creates its API client.
Only regular files are accepted, with final symlinks and FIFOs refused. The
worker uses a finite API request budget and explicit bounded pagination.

Exit codes:

- `0`: the read-only checks passed at observation time.
- `1`: verification failed or the worker result was invalid.
- `2`: the whole-operation deadline expired, or arguments were invalid.

Output is JSON containing the outcome, public proof identifiers, observed
`coordination_sha` and `coordination_state`. Errors do not include raw native
logs, policy contents or worker tracebacks.

Current authority must satisfy one of these predicates:

| Signed state | Required binding |
| --- | --- |
| `running` or `finalizing` | Matching operation nonce, recomputed QID, workflow/launcher/admission, run/number and native job from verified v2 evidence. |
| `idle` | Matching `qualified-only` completion, operation nonce and QID, with null `main_sha`. |
| Any other state | Refuse readiness. |

Only the admitted native and activation IDs are selected. Their direct reads
and bounded visible inventories must agree, and both complete snapshots are
compared again. Observed duplicates, replays or other nonterminal records fail.
Well-formed older terminal native records are inert history, not current
authority. Other required-check providers retain their original strict rules.
PR, review, revocation, signature and protection requirements are not relaxed.

Actual finalization remains blocked on the separately reviewed lock-held main
write, live producer/check qualification and the remaining integration gates.
The exact-base Git update primitive alone does not close the activation race.
Before enforcement, a valid qualification can still fail full readiness because
the required native App/check mapping is absent. Record the specific refusal
without weakening policy checks; see the [integration plan](MAIN-INTEGRATION.md).

The legacy historical verifier is not a fallback for this command. A successful
read-only result does not establish GitHub's own required-check selection
behavior; that needs its separate approved protected-ref experiment.
