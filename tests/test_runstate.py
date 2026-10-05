#!/usr/bin/env python3
"""tests/test_runstate.py - migite.runstate, the run manifest (run.json).

A run writes run.json at every phase boundary so an interrupted run can resume
from where it stopped. These tests pin what resume depends on: sets merge
without losing anything, a phase status change stamps its timestamps, next_phase
and the run status follow the phases, bash can eval the shell export back, a
crash mid-write never leaves a truncated file, anything malformed or foreign is
refused, and `find` picks the run an invocation means.
Run: python3 -m unittest tests/test_runstate.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import runstate  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "migite.runstate", *args],
                          capture_output=True, text=True, env=env)


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.file = self.dir / "run.json"


class InitTest(TmpDirTest):
    def test_init_writes_an_envelope_with_every_phase_pending(self):
        self.assertEqual(run_cli("init", "--file", str(self.file)).returncode, 0)
        m = json.loads(self.file.read_text())
        self.assertEqual((m["schema_version"], m["tool"]), (1, "migite-run"))
        self.assertEqual(list(m["phases"]), list(runstate.PHASES))
        self.assertTrue(all(p["status"] == "pending" for p in m["phases"].values()))
        self.assertEqual((m["status"], m["next_phase"]), ("in_progress", "plan"))
        self.assertTrue(m["generated_at"] and m["updated_at"])

    def test_init_applies_strings_json_values_and_list_appends(self):
        run_cli("init", "--file", str(self.file), "--set", "args.task=Add the thing",
                "--set-json", "args.staged=true", "--add", "args.attach=/a.md", "--add", "args.attach=/b c.md")
        args = json.loads(self.file.read_text())["args"]
        self.assertEqual(args["task"], "Add the thing")
        self.assertIs(args["staged"], True)
        self.assertEqual(args["attach"], ["/a.md", "/b c.md"])

    def test_a_number_given_with_set_stays_a_string(self):
        run_cli("init", "--file", str(self.file), "--set", "args.task=123")
        self.assertEqual(json.loads(self.file.read_text())["args"]["task"], "123")


class UpdateTest(TmpDirTest):
    def setUp(self):
        super().setUp()
        run_cli("init", "--file", str(self.file), "--set", "args.task=t", "--set", "repo.name=api")

    def load(self):
        return json.loads(self.file.read_text())

    def update(self, *sets):
        return run_cli("update", "--file", str(self.file), *sets)

    def test_an_update_merges_and_keeps_every_other_key(self):
        self.update("--set", "repo.branch=bb-1", "--set", "layout.extra.deep=x")
        m = self.load()
        self.assertEqual((m["repo"]["name"], m["repo"]["branch"]), ("api", "bb-1"))
        self.assertEqual(m["args"]["task"], "t")
        self.assertEqual(m["layout"]["extra"], {"deep": "x"})

    def test_running_stamps_started_at_and_done_stamps_completed_at(self):
        self.update("--set", "phases.plan.status=running")
        plan = self.load()["phases"]["plan"]
        self.assertIn("started_at", plan)
        self.assertNotIn("completed_at", plan)
        self.update("--set", "phases.plan.status=done")
        plan = self.load()["phases"]["plan"]
        self.assertIn("completed_at", plan)

    def test_running_again_clears_a_stale_completed_at(self):
        self.update("--set", "phases.plan.status=done")
        self.update("--set", "phases.plan.status=running")
        self.assertNotIn("completed_at", self.load()["phases"]["plan"])

    def test_an_unchanged_status_keeps_its_timestamps(self):
        with mock.patch.object(runstate, "_now", return_value="T1"):
            m = runstate.apply(runstate.new_manifest(), [("phases.plan.status", "running", False)])
        with mock.patch.object(runstate, "_now", return_value="T2"):
            m = runstate.apply(m, [("phases.plan.status", "running", False), ("phases.plan.gate_attempts", 2, False)])
        self.assertEqual((m["phases"]["plan"]["started_at"], m["updated_at"]), ("T1", "T2"))

    def test_next_phase_follows_the_first_unfinished_phase(self):
        self.update("--set", "phases.plan.status=done", "--set", "phases.tdd.status=skipped")
        self.assertEqual(self.load()["next_phase"], "implement")

    def test_a_run_with_every_phase_finished_is_complete(self):
        sets = [x for p in runstate.PHASES for x in ("--set", f"phases.{p}.status=done")]
        self.update(*sets)
        m = self.load()
        self.assertEqual((m["next_phase"], m["status"]), ("done", "complete"))
        self.update("--set", "phases.deliver.status=running")
        self.assertEqual(self.load()["status"], "in_progress")

    def test_failed_stays_until_a_resume_resets_it(self):
        self.update("--set", "status=failed")
        self.update("--set", "phases.plan.status=running")
        self.assertEqual(self.load()["status"], "failed")
        self.update("--set", "status=in_progress")
        self.assertEqual(self.load()["status"], "in_progress")

    def test_an_unknown_phase_or_status_is_refused_and_the_file_kept(self):
        before = self.file.read_text()
        for bad in ("phases.lint.status=done", "phases.plan.status=finished", "status=paused"):
            r = self.update("--set", bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("runstate:", r.stderr)
        self.assertEqual(self.file.read_text(), before)

    def test_keys_migite_owns_cannot_be_set(self):
        for key in ("schema_version=2", "tool=other", "next_phase=deliver", "updated_at=x"):
            self.assertEqual(self.update("--set", key).returncode, 2, key)

    def test_a_malformed_set_or_json_value_is_refused(self):
        self.assertEqual(self.update("--set", "no-equals-sign").returncode, 2)
        self.assertEqual(self.update("--set-json", "args.staged=yes please").returncode, 2)


class MalformedTest(TmpDirTest):
    def test_update_and_export_refuse_a_missing_file(self):
        self.assertEqual(run_cli("update", "--file", str(self.file), "--set", "args.task=x").returncode, 2)
        self.assertEqual(run_cli("export", "--shell", "--file", str(self.file)).returncode, 2)
        self.assertFalse(self.file.exists())

    def test_truncated_json_is_refused_and_never_overwritten(self):
        self.file.write_text('{"schema_version": 1, "tool": "migite-ru')
        r = run_cli("update", "--file", str(self.file), "--set", "args.task=x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(self.file.read_text(), '{"schema_version": 1, "tool": "migite-ru')

    def test_another_tools_envelope_is_refused(self):
        self.file.write_text(json.dumps({"schema_version": 1, "tool": "migite-review", "phases": {}, "args": {}}))
        self.assertIn("not a migite run manifest", run_cli("export", "--shell", "--file", str(self.file)).stderr)

    def test_a_newer_schema_is_refused(self):
        m = runstate.new_manifest()
        m["schema_version"] = 2
        self.file.write_text(json.dumps(m))
        self.assertIn("schema_version 2", run_cli("export", "--shell", "--file", str(self.file)).stderr)


class AtomicWriteTest(TmpDirTest):
    def test_a_crash_mid_write_leaves_the_old_manifest_and_no_temp_file(self):
        runstate.save(self.file, runstate.new_manifest())
        before = self.file.read_text()
        m = runstate.apply(runstate.new_manifest(), [("args.task", "new", False)])
        with mock.patch.object(runstate.os, "replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                runstate.save(self.file, m)
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["run.json"])

    def test_a_crash_while_serialising_leaves_no_file_at_all(self):
        with mock.patch.object(runstate.json, "dumps", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                runstate.save(self.file, runstate.new_manifest())
        self.assertEqual(list(self.dir.iterdir()), [])


class ShellExportTest(TmpDirTest):
    def test_bash_evals_the_export_back_to_the_same_values(self):
        task = "Fix the \"quoted\" $HOME thing; it's `odd`\nsecond line"
        run_cli("init", "--file", str(self.file), "--set", f"args.task={task}",
                "--add", "args.attach=/x y.md", "--add", "args.attach=/z.md", "--set-json", "args.staged=true")
        script = ('eval "$(python -m migite.runstate export --shell --file "$1")"; '
                  'printf "%s\\0" "$MANIFEST_ARGS_TASK" "${#MANIFEST_ARGS_ATTACH[@]}" "${MANIFEST_ARGS_ATTACH[0]}" '
                  '"$MANIFEST_ARGS_STAGED" "$MANIFEST_PHASES_TDD_DECIDED" "$MANIFEST_PHASES_PLAN_STATUS" '
                  '"${#MANIFEST_PHASES_IMPLEMENT_STAGE_LABELS[@]}"')
        env = dict(os.environ, PYTHONPATH=str(ROOT), PATH=f"{Path(sys.executable).parent}:{os.environ['PATH']}")
        out = subprocess.run(["bash", "-c", script, "_", str(self.file)], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split("\0")[:-1], [task, "2", "/x y.md", "true", "", "pending", "0"])

    def test_names_are_prefixed_upper_case_paths(self):
        lines = runstate.shell_lines(runstate.new_manifest(), prefix="X_")
        self.assertIn("X_REPO_BASE_BRANCH=''", lines)
        self.assertIn("X_PHASES_IMPLEMENT_STAGE_LABELS=()", lines)
        self.assertIn("X_ARGS_STAGED=false", lines)


class FindTest(TmpDirTest):
    def write(self, root: str, task_dir: str, run_dir: str, updated: str, **kw) -> Path:
        m = runstate.new_manifest()
        for key, value in kw.items():
            if key == "status":
                m["status"] = value
            else:
                m["args"][key] = value
        m["updated_at"] = updated
        path = self.dir / root / task_dir / run_dir / "run.json"
        runstate.save(path, m)
        return path

    def find(self, **kw):
        roots = [str(self.dir / "scratch"), str(self.dir / "vault")]
        return runstate.find(roots, **kw)

    def test_the_jira_key_wins_over_the_task_text(self):
        a = self.write("scratch", "bb-1", "00-build", "2026-01-01", jira_ticket="BB-1", task="BB-1")
        self.write("scratch", "bb-2", "00-build", "2026-01-02", jira_ticket="BB-2", task="BB-1")
        self.assertEqual(self.find(jira="BB-1", task="BB-1"), a)

    def test_the_task_text_finds_a_run_whose_title_renamed_its_folder(self):
        a = self.write("scratch", "add-a-widget-to-the-page", "00-build", "2026-01-01", task="Add thing")
        self.assertEqual(self.find(task="Add thing"), a)
        self.assertIsNone(self.find(task="Add other thing"))

    def test_the_intake_file_is_matched_when_there_is_no_key(self):
        a = self.write("scratch", "x", "00-build", "2026-01-01", intake="/work/intake.md")
        self.assertEqual(self.find(intake="/work/intake.md"), a)

    def test_an_audit_run_must_match_its_report(self):
        self.write("scratch", "audit-remediation", "00-build", "2026-01-01", task="audit-remediation", audit="/old.md")
        self.assertIsNone(self.find(task="audit-remediation", audit="/new.md"))
        self.assertIsNotNone(self.find(task="audit-remediation", audit="/old.md"))

    def test_the_newest_match_wins(self):
        self.write("scratch", "t", "00-build", "2026-01-01T00:00:00+00:00", task="t")
        b = self.write("scratch", "t", "01-amend-x", "2026-01-02T00:00:00+00:00", task="t")
        self.assertEqual(self.find(task="t"), b)

    def test_builds_only_ignores_amend_runs(self):
        a = self.write("scratch", "t", "00-build", "2026-01-01", task="t")
        self.write("scratch", "t", "01-amend-x", "2026-01-02", task="t")
        self.assertEqual(self.find(task="t", builds_only=True), a)

    def test_unfinished_skips_complete_runs(self):
        self.write("scratch", "t", "00-build", "2026-01-02", task="t", status="complete")
        b = self.write("scratch", "u", "00-build", "2026-01-01", task="u")
        self.assertEqual(self.find(unfinished=True), b)
        self.assertIsNone(self.find(task="t", unfinished=True))

    def test_the_scratchpad_copy_wins_over_its_vault_mirror(self):
        a = self.write("scratch", "t", "00-build", "2026-01-01", task="t")
        self.write("vault", "t", "00-build", "2026-01-09", task="t")
        self.assertEqual(self.find(task="t"), a)

    def test_the_vault_copy_counts_when_the_scratchpad_lost_it(self):
        b = self.write("vault", "t", "00-build", "2026-01-01", task="t")
        self.assertEqual(self.find(task="t"), b)

    def test_unreadable_manifests_and_non_run_folders_are_skipped(self):
        bad = self.dir / "scratch" / "t" / "00-build" / "run.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{nope")
        self.write("scratch", "t", "notes", "2026-01-01", task="t")
        b = self.write("vault", "t", "00-build", "2026-01-01", task="t")
        self.assertEqual(self.find(task="t"), b)

    def test_the_cli_prints_the_path_or_exits_1(self):
        a = self.write("scratch", "t", "00-build", "2026-01-01", task="t")
        r = run_cli("find", "--root", str(self.dir / "scratch"), "--task", "t")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(a)))
        self.assertEqual(run_cli("find", "--root", str(self.dir / "scratch"), "--task", "nope").returncode, 1)
        self.assertEqual(run_cli("find", "--root", str(self.dir / "missing")).returncode, 1)


if __name__ == "__main__":
    unittest.main()
