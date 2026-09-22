#!/usr/bin/env python3
"""tests/test_migite_paths.py — unittest coverage for migite_paths' fixture-backed
detection logic (detect_base_branch, detect_org), runnable via `python3 -m unittest`
independently of the --self-test CLI flag (which also covers resolve_run_dir,
slugify, and extract_ticket_key)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migite_paths import detect_base_branch, detect_org  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(repo):
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "migite tests")


class DetectBaseBranchTest(unittest.TestCase):
    """origin/HEAD is authoritative when set; otherwise falls back to
    main/master/develop, then a hardcoded "main" — that last fallback was a
    real 2026-08-05 bug when it silently returned empty instead."""

    def test_origin_head_set_wins(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            self.assertEqual(detect_base_branch(repo), "main")

    def test_no_origin_head_falls_back_to_local_master(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
            _git(repo, "branch", "-m", "master")
            self.assertEqual(detect_base_branch(repo), "master")

    def test_main_takes_priority_over_master(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
            _git(repo, "branch", "-m", "master")
            _git(repo, "checkout", "-q", "-b", "main")
            self.assertEqual(detect_base_branch(repo), "main")

    def test_no_branches_falls_back_to_hardcoded_main(self):
        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            self.assertEqual(detect_base_branch(repo), "main")


class DetectOrgTest(unittest.TestCase):
    """MIGITE_ORG wins if set; otherwise the repo's parent directory name,
    falling back to "Personal" when there's no meaningful parent."""

    def setUp(self):
        self._prev_org = os.environ.pop("MIGITE_ORG", None)

    def tearDown(self):
        if self._prev_org is None:
            os.environ.pop("MIGITE_ORG", None)
        else:
            os.environ["MIGITE_ORG"] = self._prev_org

    def test_parent_directory_name(self):
        self.assertEqual(detect_org("/Users/x/Code/Acme-client/foo"), "Acme-client")

    def test_env_override_wins_over_parent_dir(self):
        os.environ["MIGITE_ORG"] = "Freelance"
        self.assertEqual(detect_org("/Users/x/Code/Acme-client/foo"), "Freelance")

    def test_no_meaningful_parent_falls_back_to_personal(self):
        self.assertEqual(detect_org("/"), "Personal")


if __name__ == "__main__":
    unittest.main()
