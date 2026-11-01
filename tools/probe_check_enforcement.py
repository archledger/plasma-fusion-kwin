# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict disposable enforcement-probe data, never native qualification."""

import hashlib
import json
import re

from ci.native.check_records import CheckIdentity, validate_check
from ci.native.merge_update import git, read_raw_object
from ci.native.proof_api import (
    bounded_integer,
    finite_float,
    object_pairs,
    reject_constant,
)
from ci.native.qualification import canonical_json, digest, identifier, keys, require
from ci.native.signed_merge import verify_signed_commit

REPOSITORY = "archledger/plasma-fusion-kwin"
REPOSITORY_ID = 1439058578
APP_ID = 5544780
CHECK_NAME = "Native KWin / trusted"
SCOPE = "disposable check-enforcement probe only; no native qualification"
MARKER = "check-enforcement-fixture.json"
STAGES = ("A1", "A2", "A3", "B1", "B2", "B3")
CREATION_STAGES = STAGES[:-1]
CHECK_FIELDS = (
    "id",
    "name",
    "head_sha",
    "external_id",
    "details_url",
    "status",
    "conclusion",
    "completed_at",
    "app",
)


def experiment_refs(experiment_id):
    digest(experiment_id, 32)
    prefix = "refs/heads/validation/check-enforcement-" + experiment_id
    return {
        name: prefix + "-" + suffix
        for name, suffix in [
            ("target", "target"),
            ("case_a", "case-a"),
            ("case_b", "case-b"),
        ]
    }


def stage_qid(experiment_id, stage, run_id, head, nonce):
    experiment_refs(experiment_id)
    require(type(stage) is str and stage in CREATION_STAGES, "invalid origin stage")
    return hashlib.sha256(
        canonical_json(
            {
                "scope": SCOPE,
                "experiment_id": experiment_id,
                "stage": stage,
                "run_id": identifier(run_id),
                "head": digest(head),
                "nonce": digest(nonce, 32),
            }
        )
    ).hexdigest()


def validate_fixtures(fixtures, launcher, authority):
    keys(fixtures, ["base", "case_a", "case_b"])
    for value in fixtures.values():
        digest(value)
    require(
        fixtures["base"] == digest(launcher)
        and len(set(fixtures.values())) == 3
        and digest(authority) not in fixtures.values(),
        "invalid fixture identities",
    )
    return fixtures


def stage_head(stage, fixtures):
    require(type(stage) is str and stage in STAGES, "invalid probe stage")
    return fixtures["case_a" if stage.startswith("A") else "case_b"]


def stage_outcome(stage):
    require(type(stage) is str and stage in STAGES, "invalid probe stage")
    if stage == "B2":
        return None
    return "failure" if stage in {"A2", "B3"} else "success"


def verify_fixtures(
    source,
    fixtures,
    experiment_id,
    *,
    launcher,
    authority,
    keyring,
    fingerprint,
    signoff,
):
    """Read the signed, marker-only A→B graph; never execute or fetch a fixture."""
    experiment_refs(experiment_id)
    validate_fixtures(fixtures, launcher, authority)
    read_raw_object(source, fixtures["base"], "commit", limit=64 * 1024)
    require(
        not git(source, "ls-tree", fixtures["base"], "--", MARKER),
        "fixture marker already exists in the launcher",
    )
    for case, parent in [("A", fixtures["base"]), ("B", fixtures["case_a"])]:
        commit = fixtures["case_" + case.lower()]
        raw, _ = verify_signed_commit(
            source, commit, keyring=keyring, fingerprint=fingerprint, signoff=signoff
        )
        headers = raw.partition("\n\n")[0].splitlines()
        require(
            [line for line in headers if line.startswith("parent ")]
            == ["parent " + parent],
            "fixture parent differs",
        )
        require(
            git(source, "diff", "--name-only", parent, commit).splitlines() == [MARKER],
            "fixture changes other content",
        )
        entry = git(source, "ls-tree", commit, "--", MARKER)
        match = re.fullmatch(r"100644 blob ([0-9a-f]{40})\t" + re.escape(MARKER), entry)
        require(match is not None, "fixture marker is not a regular file")
        data = read_raw_object(source, match[1], "blob", limit=4096)
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_int=bounded_integer,
            parse_float=finite_float,
            parse_constant=reject_constant,
        )
        keys(value, ["schema_version", "scope", "experiment_id", "case", "parent"])
        require(
            type(value["schema_version"]) is int
            and value["schema_version"] == 1
            and value["scope"] == SCOPE
            and value["experiment_id"] == experiment_id
            and value["case"] == case
            and value["parent"] == parent,
            "fixture marker identity differs",
        )
    return fixtures


def validate_report(value):
    """Validate shape and binding only; API/fixture authentication is separate."""
    keys(
        value,
        [
            "schema_version",
            "scope",
            "experiment_id",
            "stage",
            "launcher",
            "authority",
            "writer_run_id",
            "writer_nonce",
            "fixtures",
            "records",
        ],
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["scope"] == SCOPE,
        "wrong enforcement report scope",
    )
    experiment_refs(value["experiment_id"])
    stage = value["stage"]
    require(type(stage) is str and stage in STAGES, "invalid report stage")
    fixtures = validate_fixtures(
        value["fixtures"], value["launcher"], value["authority"]
    )
    writer = identifier(value["writer_run_id"])
    digest(value["writer_nonce"], 32)
    records = value["records"]
    count = min(STAGES.index(stage) + 1, len(CREATION_STAGES))
    require(type(records) is list and len(records) == count, "incomplete stage history")
    ids, runs, nonces = set(), set(), set()
    for origin, record in zip(CREATION_STAGES, records):
        keys(record, ["stage", "run_id", "nonce", "qualification_id", "check"])
        require(record["stage"] == origin, "stage order differs")
        run_id = identifier(record["run_id"])
        nonce = digest(record["nonce"], 32)
        head = stage_head(origin, fixtures)
        qid = stage_qid(value["experiment_id"], origin, run_id, head, nonce)
        require(record["qualification_id"] == qid, "check identity differs")
        check = record["check"]
        keys(check, CHECK_FIELDS)
        keys(check["app"], ["id"])
        outcome = stage_outcome("B3" if stage == "B3" and origin == "B2" else origin)
        validate_check(
            check,
            CheckIdentity(REPOSITORY, APP_ID, head, CHECK_NAME, qid, run_id),
            identifier(check["id"]),
            status="in_progress" if outcome is None else "completed",
            conclusion=outcome,
        )
        require(
            check["id"] not in ids and run_id not in runs and nonce not in nonces,
            "duplicate probe identity",
        )
        ids.add(check["id"])
        runs.add(run_id)
        nonces.add(nonce)
    last = records[-1]
    if stage == "B3":
        require(
            writer not in runs and value["writer_nonce"] not in nonces,
            "cleanup writer reused the origin identity",
        )
    else:
        require(
            writer == last["run_id"] and value["writer_nonce"] == last["nonce"],
            "writer differs from the new check origin",
        )
    return value
