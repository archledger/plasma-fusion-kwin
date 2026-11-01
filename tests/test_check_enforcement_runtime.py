# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the real runtime authorization and pre-write fences offline."""

import contextlib
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_check_enforcement_probe as fixtures

from ci.native.checks import CheckWriteError
from tools import probe_check_enforcement as probe


class RuntimeHarness:
    """Fake external reads/transport, retaining the verifier, worker and writer."""

    def __init__(self, root, stage):
        previous_stage = "B2" if stage == "B3" else "A1"
        report, current, self.archive, self.values = (
            fixtures.PredecessorTests().fixture(previous_stage)
        )
        self.env = fixtures.RuntimeTests().environment(root)
        self.env.update(
            GITHUB_RUN_ID=str(current["id"]),
            ENFORCEMENT_STAGE=stage,
            ENFORCEMENT_PREDECESSOR_RUN=str(report["writer_run_id"])
            if stage == "B3"
            else "",
        )
        self.current_resource = f"actions/runs/{current['id']}"
        self.refs = {
            "refs/heads/main": "a" * 40,
            "refs/heads/native-gate-coordination": "d" * 40,
        }
        self.refs.update(
            dict(
                zip(
                    probe.experiment_refs("e" * 32).values(),
                    ["b" * 40 if stage == "B3" else "a" * 40, "b" * 40, "c" * 40],
                )
            )
        )
        self.server = {
            record["check"]["id"]: dict(
                copy.deepcopy(record["check"]),
                output={"title": "Validation only", "summary": probe.SCOPE},
            )
            for record in (report["records"] if stage == "B3" else [])
        }
        self.writes = []
        self.minted = False
        self.after_mint = lambda: None
        self.api = SimpleNamespace(repository=probe.REPOSITORY, get=self.get)

    def get(self, resource, **kwargs):
        if resource.startswith("commits/"):
            head = resource.split("/")[1]
            checks = [v for v in self.server.values() if v["head_sha"] == head]
            return {"total_count": len(checks), "check_runs": copy.deepcopy(checks)}
        return copy.deepcopy(self.values[resource])

    def mint(self, policy, key, run_id, fence, *, journal):
        self.minted = True
        self.after_mint()
        client = probe.ValidationReporter(
            policy, "fixture", run_id, fence, journal=journal
        )
        client.reader = self.api
        return client

    def send(self, method, url, token, body=None):
        resource = url.split("/repos/" + probe.REPOSITORY + "/")[1]
        if method == "POST":
            check_id = 900
            self.server[check_id] = dict(
                copy.deepcopy(body),
                id=check_id,
                app={"id": probe.APP_ID},
                conclusion=None,
                completed_at=None,
            )
        else:
            check_id = int(resource.split("/")[-1])
            if method == "PATCH":
                self.server[check_id].update(
                    copy.deepcopy(body), completed_at="2026-11-01T00:00:00Z"
                )
        if method != "GET":
            self.writes.append(method)
        return copy.deepcopy(self.server[check_id])


@contextlib.contextmanager
def runtime(stage):
    with tempfile.TemporaryDirectory() as root, contextlib.ExitStack() as stack:
        harness = RuntimeHarness(root, stage)
        replacements = [
            patch.dict(probe.os.environ, harness.env, clear=True),
            patch.object(probe, "ProofAPI", return_value=harness.api),
            patch.object(probe, "git", side_effect=["a" * 40, ""]),
            patch.object(
                probe,
                "read_refs",
                side_effect=lambda *args: copy.deepcopy(harness.refs),
            ),
            patch.object(probe, "load_keys", return_value=Path(root)),
            patch.object(probe, "public_git"),
            patch.object(probe, "verify_fixtures"),
            patch.object(probe, "download_artifact", return_value=harness.archive),
            patch.object(
                probe.uuid, "uuid4", return_value=SimpleNamespace(hex="6" * 32)
            ),
            patch.object(
                probe.ValidationReporter, "from_key", side_effect=harness.mint
            ),
            patch("ci.native.checks._request", side_effect=harness.send),
        ]
        for replacement in replacements:
            stack.enter_context(replacement)
        yield harness


class RuntimeBoundaryTests(unittest.TestCase):
    def test_real_runtime_positive_controls_reach_creation_and_cleanup(self):
        for stage, writes in [("A1", ["POST", "PATCH"]), ("B3", ["PATCH"])]:
            with self.subTest(stage=stage), runtime(stage) as harness:
                result = probe.run_worker()
                self.assertTrue(result["success"])
                self.assertTrue(harness.minted)
                self.assertEqual(harness.writes, writes)

    def test_actor_mismatch_refused_before_token_mint(self):
        with runtime("A1") as harness:
            harness.values[harness.current_resource]["triggering_actor"]["id"] = 11
            with self.assertRaisesRegex(ValueError, "workflow identity differs"):
                probe.run_worker()
            self.assertFalse(harness.minted)
            self.assertEqual(harness.writes, [])

    def test_insufficient_permission_refused_before_token_mint(self):
        with runtime("A1") as harness:
            harness.values["collaborators/fixture/permission"]["permission"] = "read"
            with self.assertRaisesRegex(ValueError, "actor is not authorized"):
                probe.run_worker()
            self.assertFalse(harness.minted)
            self.assertEqual(harness.writes, [])

    def assert_prewrite_refusal(self, change):
        for stage in ["A1", "B3"]:
            with self.subTest(stage=stage), runtime(stage) as harness:
                before = copy.deepcopy(harness.server)
                harness.after_mint = lambda: change(harness)
                with self.assertRaises(CheckWriteError):
                    probe.run_worker()
                self.assertTrue(harness.minted)
                self.assertEqual(harness.writes, [])
                self.assertEqual(harness.server, before)

    def test_revoked_permission_blocks_real_prewrite_fence(self):
        self.assert_prewrite_refusal(
            lambda h: h.values["collaborators/fixture/permission"].update(
                permission="read"
            )
        )

    def test_actor_drift_blocks_real_prewrite_fence(self):
        self.assert_prewrite_refusal(
            lambda h: h.values[h.current_resource]["triggering_actor"].update(id=11)
        )

    def test_target_drift_blocks_real_prewrite_fence(self):
        self.assert_prewrite_refusal(
            lambda h: h.refs.update(
                {probe.experiment_refs("e" * 32)["target"]: "f" * 40}
            )
        )

    def test_authority_drift_blocks_real_prewrite_fence(self):
        self.assert_prewrite_refusal(
            lambda h: h.refs.update({"refs/heads/native-gate-coordination": "f" * 40})
        )


if __name__ == "__main__":
    unittest.main()
