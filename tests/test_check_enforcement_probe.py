# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline enforcement-probe contracts, never live GitHub evidence."""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ci.native.check_journal import CheckJournal
from ci.native.checks import CheckReporter, CheckWriteError
from tools import probe_check_enforcement as probe


def report_fixture(stage):
    fixtures = {"base": "a" * 40, "case_a": "b" * 40, "case_b": "c" * 40}
    count = min(probe.STAGES.index(stage) + 1, 5)
    records = []
    for index, origin in enumerate(probe.STAGES[:count]):
        run_id, nonce = 500 + index, str(index + 1) * 32
        head = fixtures["case_a" if origin.startswith("A") else "case_b"]
        qid = probe.stage_qid("e" * 32, origin, run_id, head, nonce)
        outcome = (
            None
            if origin == "B2" and stage != "B3"
            else ("failure" if origin in {"A2", "B2"} else "success")
        )
        records.append(
            {
                "stage": origin,
                "run_id": run_id,
                "nonce": nonce,
                "qualification_id": qid,
                "check": {
                    "id": 800 + index,
                    "name": probe.CHECK_NAME,
                    "head_sha": head,
                    "external_id": qid,
                    "details_url": f"https://github.com/{probe.REPOSITORY}/actions/runs/{run_id}",
                    "status": "in_progress" if outcome is None else "completed",
                    "conclusion": outcome,
                    "completed_at": None if outcome is None else "2026-11-01T00:00:00Z",
                    "app": {"id": probe.APP_ID},
                },
            }
        )
    index = probe.STAGES.index(stage)
    return {
        "success": True,
        "schema_version": 1,
        "scope": probe.SCOPE,
        "experiment_id": "e" * 32,
        "stage": stage,
        "launcher": "a" * 40,
        "authority": "d" * 40,
        "writer_run_id": 500 + index,
        "writer_nonce": str(index + 1) * 32,
        "fixtures": fixtures,
        "records": records,
    }


class ContractTests(unittest.TestCase):
    def test_refs_are_derived_without_accepting_a_target_path(self):
        refs = probe.experiment_refs("e" * 32)
        self.assertEqual(
            refs["target"],
            "refs/heads/validation/check-enforcement-" + "e" * 32 + "-target",
        )
        self.assertEqual(set(refs), {"target", "case_a", "case_b"})
        for bad in ["", "0" * 32, "../main", True, "e" * 31, "E" * 32]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe.experiment_refs(bad)

    def test_each_stage_has_a_strict_complete_report(self):
        for stage in probe.STAGES:
            value = report_fixture(stage)
            self.assertEqual(probe.validate_report(value), value)

    def test_b3_keeps_the_origin_identity_and_uses_a_new_writer(self):
        report = report_fixture("B3")
        self.assertNotEqual(report["writer_run_id"], report["records"][-1]["run_id"])
        self.assertEqual(report["records"][-1]["stage"], "B2")
        wrong = copy.deepcopy(report)
        wrong["records"][-1]["run_id"] = report["writer_run_id"]
        with self.assertRaises(ValueError):
            probe.validate_report(wrong)

    def test_wrong_scope_extra_fields_and_boolean_ids_fail(self):
        for mutate in [
            lambda r: r.update(scope="native qualification"),
            lambda r: r.update(extra=True),
            lambda r: r.update(writer_run_id=True),
            lambda r: r.update(stage="C1"),
        ]:
            value = report_fixture("A1")
            mutate(value)
            with self.assertRaises(ValueError):
                probe.validate_report(value)

    def test_case_order_identity_and_status_are_not_inferred(self):
        for mutate in [
            lambda r: r["records"].reverse(),
            lambda r: r["records"][1]["check"].update(conclusion="success"),
            lambda r: r["records"][1]["check"].update(id=800),
            lambda r: r["records"][1]["check"].update(head_sha="c" * 40),
            lambda r: r["fixtures"].update(base="d" * 40),
        ]:
            value = report_fixture("A2")
            mutate(value)
            with self.assertRaises(ValueError):
                probe.validate_report(value)

    def test_pending_is_not_a_success_and_b3_requires_failure(self):
        pending = report_fixture("B2")
        self.assertIsNone(pending["records"][-1]["check"]["conclusion"])
        pending["records"][-1]["check"].update(
            status="completed",
            conclusion="success",
            completed_at="2026-11-01T00:00:00Z",
        )
        with self.assertRaises(ValueError):
            probe.validate_report(pending)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.server, self.writes, self.saved = {}, [], []
        self.fixtures = report_fixture("A1")["fixtures"]
        self.policy = SimpleNamespace(
            repository=probe.REPOSITORY,
            repository_id=probe.REPOSITORY_ID,
            app_id=probe.APP_ID,
            check_name=probe.CHECK_NAME,
        )

    def send(self, method, url, token, body=None):
        resource = url.split("/repos/" + probe.REPOSITORY + "/")[1]
        if method == "POST":
            check_id = 800 + len(self.server)
            self.server[check_id] = dict(
                body,
                id=check_id,
                app={"id": probe.APP_ID},
                conclusion=None,
                completed_at=None,
            )
        else:
            check_id = int(resource.split("/")[-1])
            if method == "PATCH":
                self.server[check_id].update(body, completed_at="2026-11-01T00:00:00Z")
        if method != "GET":
            self.writes.append((method, copy.deepcopy(body)))
        return copy.deepcopy(self.server[check_id])

    def run_stage(self, stage, previous=None, fence=lambda: None):
        index = probe.STAGES.index(stage)
        run_id, nonce = 500 + index, str(index + 1) * 32
        qid = probe.stage_qid(
            "e" * 32, stage, run_id, probe.stage_head(stage, self.fixtures), nonce
        )
        root = Path(self.temp.name)
        admit = CheckJournal(
            root / f"{stage}-admit.json",
            qualification_id=qid,
            run_id=run_id,
            nonce=nonce,
            phase="admit",
        )
        report = (
            None
            if stage == "B2"
            else CheckJournal(
                root / f"{stage}-report.json",
                qualification_id=qid,
                run_id=run_id,
                nonce=nonce,
                phase="report",
            )
        )
        client = probe.ValidationReporter(
            self.policy, "fixture", run_id, fence, journal=admit
        )

        def inventory(path, page=1, per_page=100, **kwargs):
            head = path.split("/")[1]
            values = [
                copy.deepcopy(value)
                for value in self.server.values()
                if value["head_sha"] == head
            ]
            return {
                "total_count": len(values),
                "check_runs": values[(page - 1) * per_page : page * per_page],
            }

        with (
            patch("ci.native.checks._request", side_effect=self.send),
            patch.object(client.reader, "get", side_effect=inventory),
        ):
            return probe.exercise(
                client,
                stage=stage,
                experiment_id="e" * 32,
                writer_run_id=run_id,
                writer_nonce=nonce,
                launcher="a" * 40,
                authority="d" * 40,
                fixtures=self.fixtures,
                report_journal=report,
                previous=previous,
                save=lambda value: self.saved.append(copy.deepcopy(value)),
            )

    def test_fresh_stages_keep_history_and_use_validation_output(self):
        previous = None
        for stage in probe.CREATION_STAGES:
            previous = self.run_stage(stage, previous)
            self.assertTrue(previous["success"])
        self.assertEqual(len(self.server), 5)
        self.assertEqual([w[0] for w in self.writes], ["POST", "PATCH"] * 4 + ["POST"])
        self.assertTrue(
            all(w[1]["output"]["title"] == "Validation only" for w in self.writes)
        )
        self.assertEqual(self.server[804]["status"], "in_progress")

    def test_fence_failure_stops_before_mutation(self):
        def fail():
            raise ValueError("changed authority")

        with self.assertRaises(CheckWriteError):
            self.run_stage("A1", fence=fail)
        self.assertEqual(self.writes, [])
        self.assertFalse(self.saved[-1]["success"])

    def test_changed_prior_record_stops_before_new_creation(self):
        first = self.run_stage("A1")
        self.server[800]["conclusion"] = "failure"
        self.writes.clear()
        with self.assertRaises(ValueError):
            self.run_stage("A2", first)
        self.assertEqual(self.writes, [])

    def test_ambiguous_create_keeps_pending_journal_and_never_completes(self):
        original = self.send

        def uncertain(method, url, token, body=None):
            value = original(method, url, token, body)
            if method == "POST":
                raise OSError("untrusted transport details")
            return value

        with (
            patch.object(self, "send", side_effect=uncertain),
            self.assertRaises(CheckWriteError),
        ):
            self.run_stage("A1")
        self.assertEqual([entry[0] for entry in self.writes], ["POST"])
        journal = json.loads((Path(self.temp.name) / "A1-admit.json").read_text())
        self.assertEqual(journal["writes"][0]["status"], "pending")
        self.assertFalse(self.saved[-1]["success"])
        self.assertNotIn("transport details", json.dumps(self.saved))

    def test_unknown_history_blocks_before_another_create(self):
        first = self.run_stage("A1")
        extra = copy.deepcopy(self.server[800])
        extra.update(
            id=900,
            external_id="f" * 64,
            details_url=f"https://github.com/{probe.REPOSITORY}/actions/runs/900",
        )
        self.server[900] = extra
        self.writes.clear()
        with self.assertRaises(ValueError):
            self.run_stage("A2", first)
        self.assertEqual(self.writes, [])

    def test_production_reporter_output_is_unchanged(self):
        qid = probe.stage_qid("e" * 32, "A1", 500, "b" * 40, "1" * 32)
        journal = CheckJournal(
            Path(self.temp.name) / "ordinary.json",
            qualification_id=qid,
            run_id=500,
            nonce="1" * 32,
            phase="admit",
        )
        client = CheckReporter(
            self.policy, "fixture", 500, lambda: None, journal=journal
        )
        with (
            patch("ci.native.checks._request", side_effect=self.send),
            patch.object(
                client.reader, "get", return_value={"total_count": 0, "check_runs": []}
            ),
        ):
            client.begin("b" * 40, probe.CHECK_NAME, qid)
        self.assertEqual(
            self.writes[0][1]["output"]["title"], "Native qualification pending"
        )


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(
            prefix="enforcement-keys-", dir="/tmp/opencode"
        )
        cls.root = Path(cls.temp.name)
        cls.keys, cls.public = cls.root / "keys", cls.root / "public"
        cls.keys.mkdir(mode=0o700)
        cls.public.mkdir(mode=0o700)
        cls.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        cls.env.update(
            GNUPGHOME=str(cls.keys),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        cls.addClassCleanup(cls.cleanup_keys)
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Fixture <fixture@example.invalid>",
                "ed25519",
                "sign",
                "0",
            ],
            env=cls.env,
            capture_output=True,
            check=True,
            timeout=30,
        )
        listing = subprocess.check_output(
            ["gpg", "--with-colons", "--list-keys"], env=cls.env, stderr=subprocess.PIPE
        ).decode()
        cls.fingerprint = next(
            line.split(":")[9]
            for line in listing.splitlines()
            if line.startswith("fpr:")
        )
        public = subprocess.check_output(["gpg", "--export"], env=cls.env)
        subprocess.run(
            ["gpg", "--batch", "--homedir", str(cls.public), "--import"],
            input=public,
            env=cls.env,
            capture_output=True,
            check=True,
            timeout=30,
        )

    @classmethod
    def cleanup_keys(cls):
        for directory in [cls.keys, cls.public]:
            subprocess.run(
                ["gpgconf", "--homedir", str(directory), "--kill", "gpg-agent"],
                env=cls.env,
                capture_output=True,
                check=False,
                timeout=15,
            )
        cls.temp.cleanup()

    def setUp(self):
        self.temp_repo = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.temp_repo.cleanup)
        self.repo = Path(self.temp_repo.name)
        self.git("init", "-q")
        empty = self.git("mktree", data=b"")
        self.base = self.git("commit-tree", empty, data=b"Base\n")
        self.a = self.commit("A", self.base)
        self.b = self.commit("B", self.a)
        self.fixtures = {"base": self.base, "case_a": self.a, "case_b": self.b}

    def git(self, *args, data=None):
        return (
            subprocess.check_output(
                ["git", *args],
                input=data,
                cwd=self.repo,
                env=self.env,
                stderr=subprocess.PIPE,
            )
            .decode()
            .strip()
        )

    def commit(self, case, parent, *, signed=True, marker=None, extra=False):
        marker = marker or {
            "schema_version": 1,
            "scope": probe.SCOPE,
            "experiment_id": "e" * 32,
            "case": case,
            "parent": parent,
        }
        blob = self.git(
            "hash-object", "-w", "--stdin", data=json.dumps(marker).encode()
        )
        entries = f"100644 blob {blob}\t{probe.MARKER}\n"
        if extra:
            entries += f"100644 blob {blob}\textra.json\n"
        tree = self.git("mktree", data=entries.encode())
        args = ["commit-tree", tree, "-p", parent]
        if signed:
            args += ["-S" + self.fingerprint]
        return self.git(
            *args, data=b"Fixture\n\nSigned-off-by: Fixture <fixture@example.invalid>\n"
        )

    def verify(self, fixtures=None, **kwargs):
        return probe.verify_fixtures(
            self.repo,
            fixtures or self.fixtures,
            "e" * 32,
            launcher=self.base,
            authority="d" * 40,
            keyring=self.public,
            fingerprint=self.fingerprint,
            signoff="Signed-off-by: Fixture <fixture@example.invalid>",
            **kwargs,
        )

    def test_real_signed_fixture_graph_is_verified(self):
        self.assertEqual(self.verify(), self.fixtures)

    def test_unsigned_extra_files_and_wrong_marker_are_rejected(self):
        for bad in [
            self.commit("A", self.base, signed=False),
            self.commit("A", self.base, extra=True),
            self.commit("B", self.base),
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.verify(dict(self.fixtures, case_a=bad))

    def test_second_fixture_must_descend_from_first(self):
        bad = self.commit("B", self.base)
        with self.assertRaises(ValueError):
            self.verify(dict(self.fixtures, case_b=bad))


if __name__ == "__main__":
    unittest.main()
