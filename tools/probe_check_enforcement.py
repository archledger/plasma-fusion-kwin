# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict disposable enforcement-probe data, never native qualification."""

import copy
import hashlib
import json
import os
import re
import subprocess
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.request import build_opener

from ci.native.check_journal import CheckJournal
from ci.native.check_records import CheckIdentity, validate_check
from ci.native.checks import CheckReporter
from ci.native.merge_update import git, read_raw_object
from ci.native.pagination import get_all
from ci.native.proof_api import (
    NoRedirect,
    ProofAPI,
    bounded_integer,
    finite_float,
    object_pairs,
    reject_constant,
)
from ci.native.qualification import (
    canonical_json,
    digest,
    identifier,
    keys,
    read_archive,
    require,
    timestamp,
)
from ci.native.signed_merge import verify_signed_commit
from tools.probe_check_lifecycle import download_artifact
from tools.probe_live_worker import FINGERPRINT, PUBLIC_KEY_SHA256, SIGNOFF

REPOSITORY = "archledger/plasma-fusion-kwin"
REPOSITORY_ID = 1439058578
APP_ID = 5544780
CHECK_NAME = "Native KWin / trusted"
WORKFLOW = ".github/workflows/check-enforcement-probe.yml"
MAX_REPORT_BYTES = 64 * 1024
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


def _now():
    return datetime.now(timezone.utc)


def expiry_time(value):
    require(
        type(value) is str
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
        "invalid enforcement expiry",
    )
    return timestamp(value)


def require_unexpired(value, *, created_at=None):
    expiry = expiry_time(value)
    if created_at is not None:
        require(
            timedelta(0) < expiry - timestamp(created_at) <= timedelta(hours=1),
            "enforcement expiry exceeds the one-hour run window",
        )
    require(_now() < expiry, "enforcement deadline expired")
    return expiry


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


def stage_qid(experiment_id, stage, run_id, head, nonce, expires_at):
    experiment_refs(experiment_id)
    expiry_time(expires_at)
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
                "expires_at": expires_at,
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
            "success",
            "schema_version",
            "scope",
            "experiment_id",
            "stage",
            "launcher",
            "authority",
            "writer_run_id",
            "writer_nonce",
            "expires_at",
            "fixtures",
            "records",
        ],
    )
    require(
        value["success"] is True
        and type(value["schema_version"]) is int
        and value["schema_version"] == 2
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
    expiry_time(value["expires_at"])
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
        qid = stage_qid(
            value["experiment_id"], origin, run_id, head, nonce, value["expires_at"]
        )
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


def verify_probe_run(api, run_id, launcher, *, completed):
    run = api.get(f"actions/runs/{identifier(run_id)}")
    require(
        run["id"] == run_id
        and run["repository"]["id"] == REPOSITORY_ID
        and run["repository"]["full_name"] == REPOSITORY
        and run["head_repository"]["id"] == REPOSITORY_ID
        and run["head_sha"] == launcher
        and run["head_branch"] == "main"
        and run["path"] == WORKFLOW
        and run["event"] == "workflow_dispatch"
        and type(run["run_attempt"]) is int
        and run["run_attempt"] == 1
        and run["status"] == ("completed" if completed else "in_progress")
        and run["conclusion"] == ("success" if completed else None)
        and identifier(run["actor"]["id"]) == identifier(run["triggering_actor"]["id"]),
        "probe workflow identity differs",
    )
    identifier(run["workflow_id"])
    permission = api.get("collaborators/" + run["actor"]["login"] + "/permission")
    require(
        permission["user"]["id"] == run["actor"]["id"]
        and permission["permission"] in {"write", "maintain", "admin"},
        "probe actor is not authorized",
    )
    return run


def verify_phase_journal(value, record, phase):
    keys(
        value,
        ["schema_version", "qualification_id", "run_id", "nonce", "phase", "writes"],
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["qualification_id"] == record["qualification_id"]
        and value["run_id"] == record["run_id"]
        and type(value["run_id"]) is int
        and value["nonce"] == record["nonce"]
        and value["phase"] == phase,
        "preceding journal identity differs",
    )
    check_id = record["check"]["id"]
    require(
        value["writes"]
        == [
            {
                "role": "native",
                "action": "create" if phase == "admit" else "complete",
                "expected_check_id": None if phase == "admit" else check_id,
                "acknowledged_check_id": check_id,
                "status": "confirmed",
            }
        ],
        "preceding journal write is not acknowledged",
    )


def load_predecessor(
    api,
    stage,
    predecessor_run,
    current_run,
    *,
    launcher,
    authority,
    fixtures,
    experiment_id,
    expires_at,
    download,
):
    """Authenticate a bounded prior report and its acknowledged writes."""
    require(stage in STAGES, "invalid predecessor stage")
    if stage == "A1":
        require(predecessor_run is None, "first stage has a predecessor")
        return None
    current = verify_probe_run(api, current_run["id"], launcher, completed=False)
    previous = verify_probe_run(
        api, identifier(predecessor_run), launcher, completed=True
    )
    require(
        previous["id"] != current["id"]
        and previous["workflow_id"] == current["workflow_id"]
        and previous["actor"]["id"] == current["actor"]["id"],
        "preceding workflow differs",
    )
    jobs = get_all(api, f"actions/runs/{predecessor_run}/attempts/1/jobs", key="jobs")
    require(len(jobs) == 1, "unexpected preceding jobs")
    job = jobs[0]
    require(
        job["name"] == "probe"
        and job["run_id"] == predecessor_run
        and type(job["run_attempt"]) is int
        and job["run_attempt"] == 1
        and job["head_sha"] == launcher
        and job["status"] == "completed"
        and job["conclusion"] == "success"
        and timestamp(previous["created_at"])
        <= timestamp(job["started_at"])
        <= timestamp(job["completed_at"])
        <= timestamp(current["created_at"]),
        "preceding job identity or order differs",
    )
    artifacts = get_all(
        api, f"actions/runs/{predecessor_run}/artifacts", key="artifacts"
    )
    require(len(artifacts) == 1, "unexpected preceding artifacts")
    artifact = artifacts[0]
    association = artifact["workflow_run"]
    require(
        artifact["name"] == f"check-enforcement-{predecessor_run}-1"
        and artifact["expired"] is False
        and association["id"] == predecessor_run
        and association["repository_id"]
        == association["head_repository_id"]
        == REPOSITORY_ID
        and association["head_sha"] == launcher
        and timestamp(job["started_at"])
        <= timestamp(artifact["created_at"])
        <= timestamp(job["completed_at"]),
        "preceding artifact provenance differs",
    )
    previous_stage = STAGES[STAGES.index(stage) - 1]
    limits = {"report.json": MAX_REPORT_BYTES, "admit-journal.json": MAX_REPORT_BYTES}
    if previous_stage != "B2":
        limits["report-journal.json"] = MAX_REPORT_BYTES
    files = read_archive(download(identifier(artifact["id"])), artifact, limits)
    report = validate_report(files["report.json"])
    require(
        report["writer_run_id"] == predecessor_run
        and report["stage"] == previous_stage
        and report["launcher"] == launcher
        and report["authority"] == authority
        and report["fixtures"] == fixtures
        and report["experiment_id"] == experiment_id,
        "preceding report context differs",
    )
    require(report["expires_at"] == expires_at, "preceding expiry differs")
    record = report["records"][-1]
    verify_phase_journal(files["admit-journal.json"], record, "admit")
    if previous_stage != "B2":
        verify_phase_journal(files["report-journal.json"], record, "report")
    return report


class ValidationReporter(CheckReporter):
    """Change display text only; parent transport, identity and journal stay intact."""

    def _send(self, method, resource, body=None):
        if method in {"POST", "PATCH"}:
            body = dict(body, output={"title": "Validation only", "summary": SCOPE})
            require_unexpired(self.policy.expires_at)
        return super()._send(method, resource, body)


def snapshot(check):
    return {
        key: {"id": check["app"]["id"]} if key == "app" else check[key]
        for key in CHECK_FIELDS
    }


def validate_output(check):
    output = check.get("output")
    require(
        type(output) is dict
        and output.get("title") == "Validation only"
        and output.get("summary") == SCOPE,
        "probe output is not validation-only",
    )


def previous_records(
    previous,
    *,
    stage,
    experiment_id,
    launcher,
    authority,
    fixtures,
    writer_run_id,
    writer_nonce,
    expires_at,
):
    require(type(stage) is str and stage in STAGES, "invalid stage")
    if stage == "A1":
        require(previous is None, "first stage cannot adopt history")
        return []
    require(previous is not None, "missing preceding stage")
    validate_report(previous)
    require(
        previous["stage"] == STAGES[STAGES.index(stage) - 1]
        and previous["experiment_id"] == experiment_id
        and previous["launcher"] == launcher
        and previous["authority"] == authority
        and previous["fixtures"] == fixtures,
        "previous stage context differs",
    )
    require(previous["expires_at"] == expires_at, "previous stage expiry differs")
    require(
        writer_run_id != previous["writer_run_id"]
        and writer_nonce != previous["writer_nonce"]
        and all(
            writer_run_id != r["run_id"] and writer_nonce != r["nonce"]
            for r in previous["records"]
        ),
        "stage invocation reused",
    )
    return copy.deepcopy(previous["records"])


def observe_history(client, records):
    for record in records:
        actual = client._send("GET", f"check-runs/{record['check']['id']}")
        require(snapshot(actual) == record["check"], "earlier check changed")


def observe_inventory(client, head, records):
    values = get_all(
        client.reader,
        f"commits/{head}/check-runs",
        key="check_runs",
        check_filter="all",
    )
    matching = [
        snapshot(value)
        for value in values
        if value["app"]["id"] == APP_ID and value["name"] == CHECK_NAME
    ]
    expected = [r["check"] for r in records if r["check"]["head_sha"] == head]
    require(
        sorted(matching, key=lambda item: item["id"])
        == sorted(expected, key=lambda item: item["id"]),
        "visible check history differs",
    )


def exercise(
    client,
    *,
    stage,
    experiment_id,
    writer_run_id,
    writer_nonce,
    launcher,
    authority,
    fixtures,
    expires_at,
    report_journal,
    previous=None,
    save,
):
    """One stage using real client guards; all external I/O is caller-scoped."""
    identifier(writer_run_id)
    digest(writer_nonce, 32)
    expiry_time(expires_at)
    require(
        getattr(client.policy, "expires_at", None) == expires_at,
        "writer expiry differs",
    )
    validate_fixtures(fixtures, launcher, authority)
    records = previous_records(
        previous,
        stage=stage,
        experiment_id=experiment_id,
        launcher=launcher,
        authority=authority,
        fixtures=fixtures,
        writer_run_id=writer_run_id,
        writer_nonce=writer_nonce,
        expires_at=expires_at,
    )
    head = stage_head(stage, fixtures)
    origin = records[-1] if stage == "B3" else None
    origin_run = origin["run_id"] if origin else writer_run_id
    qid = (
        origin["qualification_id"]
        if origin
        else stage_qid(
            experiment_id, stage, writer_run_id, head, writer_nonce, expires_at
        )
    )
    require(
        type(client) is ValidationReporter and client.run_id == origin_run,
        "wrong check writer",
    )
    value = {
        "success": False,
        "schema_version": 2,
        "scope": SCOPE,
        "experiment_id": experiment_id,
        "stage": stage,
        "launcher": launcher,
        "authority": authority,
        "writer_run_id": writer_run_id,
        "writer_nonce": writer_nonce,
        "expires_at": expires_at,
        "fixtures": fixtures,
        "records": records,
    }
    try:
        observe_history(client, records)
        observe_inventory(client, head, records)
        check_id = (
            origin["check"]["id"] if origin else client.begin(head, CHECK_NAME, qid)
        )
        pending = client._send("GET", f"check-runs/{check_id}")
        validate_output(pending)
        record = origin or {
            "stage": stage,
            "run_id": writer_run_id,
            "nonce": writer_nonce,
            "qualification_id": qid,
            "check": snapshot(pending),
        }
        if origin is None:
            records.append(record)
        save(copy.deepcopy(value))
        outcome = stage_outcome(stage)
        if outcome is not None:
            client.journal = report_journal
            client.complete(
                check_id, head, CHECK_NAME, qid, success=outcome == "success"
            )
            completed = client._send("GET", f"check-runs/{check_id}")
            validate_output(completed)
            record["check"] = snapshot(completed)
        observe_history(client, records)
        observe_inventory(client, head, records)
        value["success"] = True
        validate_report(value)
        save(copy.deepcopy(value))
        return value
    except (ValueError, KeyError, TypeError, OSError):
        value["success"] = False
        save(copy.deepcopy(value))
        raise


def public_git(directory, *args):
    env = {
        "PATH": os.defpath,
        "HOME": str(directory),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    return (
        subprocess.check_output(
            ["git", "--no-replace-objects", *args],
            cwd=directory,
            env=env,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        .decode()
        .strip()
    )


def read_refs(work, experiment_id):
    names = [
        "refs/heads/main",
        "refs/heads/native-gate-coordination",
        *experiment_refs(experiment_id).values(),
    ]
    raw = public_git(
        work, "ls-remote", "--refs", "https://github.com/" + REPOSITORY + ".git", *names
    )
    rows = [line.split() for line in raw.splitlines()]
    require(
        len(rows) == len(names) and all(len(row) == 2 for row in rows),
        "incomplete enforcement refs",
    )
    values = {ref: digest(sha) for sha, ref in rows}
    require(set(values) == set(names), "unexpected enforcement refs")
    return values


def load_keys(work):
    with build_opener(NoRedirect()).open(
        "https://github.com/archledger.gpg", timeout=30
    ) as response:
        raw = response.read(65537)
    require(hashlib.sha256(raw).hexdigest() == PUBLIC_KEY_SHA256, "public key differs")
    keyfile = work / "public.asc"
    keyfile.write_bytes(raw)
    keyring = work / "keys"
    keyring.mkdir(mode=0o700)
    subprocess.run(
        [
            "gpg",
            "--no-options",
            "--homedir",
            str(keyring),
            "--batch",
            "--no-autostart",
            "--no-auto-check-trustdb",
            "--import",
            str(keyfile),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return keyring


def run_worker():
    require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
        "not a trusted probe invocation",
    )
    expires_at = os.environ["ENFORCEMENT_EXPIRES_AT"]
    require_unexpired(expires_at)
    stage = os.environ["ENFORCEMENT_STAGE"]
    require(stage in STAGES, "invalid enforcement stage")
    experiment = os.environ["ENFORCEMENT_EXPERIMENT_ID"]
    names = experiment_refs(experiment)
    authority = digest(os.environ["ENFORCEMENT_AUTHORITY_SHA"])
    launcher = digest(os.environ["GITHUB_SHA"])
    run_id = identifier(int(os.environ["GITHUB_RUN_ID"]))
    previous_text = os.environ.get("ENFORCEMENT_PREDECESSOR_RUN", "")
    require(
        (stage == "A1" and previous_text == "")
        or (stage != "A1" and re.fullmatch(r"[1-9][0-9]{0,18}", previous_text)),
        "invalid predecessor run input",
    )
    previous_id = None if not previous_text else identifier(int(previous_text))
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(
        root == Path(__file__).resolve().parents[1]
        and git(root, "rev-parse", "HEAD") == launcher
        and git(root, "status", "--porcelain") == "",
        "source checkout differs",
    )
    work = (
        Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / "check-enforcement-probe"
    )
    work.mkdir(mode=0o700)
    api = ProofAPI(REPOSITORY, token=os.environ["GH_TOKEN"], max_calls=64)
    current = verify_probe_run(api, run_id, launcher, completed=False)
    require_unexpired(expires_at, created_at=current["created_at"])
    refs = read_refs(work, experiment)
    fixtures = {
        "base": launcher,
        "case_a": refs[names["case_a"]],
        "case_b": refs[names["case_b"]],
    }
    validate_fixtures(fixtures, launcher, authority)
    expected_target = fixtures["base"] if stage.startswith("A") else fixtures["case_a"]
    expected_refs = {
        "refs/heads/main": launcher,
        "refs/heads/native-gate-coordination": authority,
        names["target"]: expected_target,
        names["case_a"]: fixtures["case_a"],
        names["case_b"]: fixtures["case_b"],
    }
    require(refs == expected_refs, "enforcement refs changed")
    keyring = load_keys(work)
    objects = work / "objects.git"
    public_git(work, "init", "--bare", "-q", str(objects))
    public_git(
        objects,
        "fetch",
        "--no-tags",
        "--depth=3",
        "https://github.com/" + REPOSITORY + ".git",
        fixtures["case_b"],
    )
    verify_fixtures(
        objects,
        fixtures,
        experiment,
        launcher=launcher,
        authority=authority,
        keyring=keyring,
        fingerprint=FINGERPRINT,
        signoff=SIGNOFF,
    )
    previous = load_predecessor(
        api,
        stage,
        previous_id,
        current,
        launcher=launcher,
        authority=authority,
        fixtures=fixtures,
        experiment_id=experiment,
        expires_at=expires_at,
        download=download_artifact,
    )
    writer_nonce = uuid.uuid4().hex
    # Finish all unprivileged checks before minting a scoped App token.
    records = previous_records(
        previous,
        stage=stage,
        experiment_id=experiment,
        launcher=launcher,
        authority=authority,
        fixtures=fixtures,
        writer_run_id=run_id,
        writer_nonce=writer_nonce,
        expires_at=expires_at,
    )
    origin = records[-1] if stage == "B3" else None
    origin_id = origin["run_id"] if origin else run_id
    origin_nonce = origin["nonce"] if origin else writer_nonce
    qid = (
        origin["qualification_id"]
        if origin
        else stage_qid(
            experiment,
            stage,
            run_id,
            stage_head(stage, fixtures),
            writer_nonce,
            expires_at,
        )
    )
    journals = {}
    for phase in ([] if stage == "B3" else ["admit"]) + (
        [] if stage == "B2" else ["report"]
    ):
        journals[phase] = CheckJournal(
            work / (phase + "-journal.json"),
            qualification_id=qid,
            run_id=origin_id,
            nonce=origin_nonce,
            phase=phase,
        )

    def fence():
        require_unexpired(expires_at)
        verify_probe_run(api, run_id, launcher, completed=False)
        require(
            read_refs(work, experiment) == expected_refs, "enforcement fence changed"
        )
        require_unexpired(expires_at)

    fence()
    policy = SimpleNamespace(
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        app_id=APP_ID,
        check_name=CHECK_NAME,
        expires_at=expires_at,
    )
    client = ValidationReporter.from_key(
        policy,
        os.environ["NATIVE_GATE_PRIVATE_KEY"],
        origin_id,
        fence,
        journal=journals["report" if stage == "B3" else "admit"],
    )

    def save(value):
        (work / "report.json").write_bytes(canonical_json(value) + b"\n")

    return exercise(
        client,
        stage=stage,
        experiment_id=experiment,
        writer_run_id=run_id,
        writer_nonce=writer_nonce,
        launcher=launcher,
        authority=authority,
        fixtures=fixtures,
        expires_at=expires_at,
        report_journal=journals.get("report"),
        previous=previous,
        save=save,
    )


def main():
    try:
        value = run_worker()
        print(
            json.dumps(
                {
                    "success": True,
                    "scope": SCOPE,
                    "stage": value["stage"],
                    "writer_run_id": value["writer_run_id"],
                }
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as error:
        print(
            json.dumps(
                {"success": False, "scope": SCOPE, "error_type": type(error).__name__}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
