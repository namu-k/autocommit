import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.env = dict(HOME=str(self.base), PATH="/usr/bin:/bin", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL="/dev/null", LANG="C.UTF-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.snapshot = self.base / "snapshot.json"
        self.output = self.base / "output.json"

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env,
                                       stderr=subprocess.DEVNULL).decode().strip()

    def helper(self, mode):
        return subprocess.run(["python3", str(SOURCE / "bin/autocommit-proposal.py"),
                               mode, str(self.snapshot), str(self.output)],
                              cwd=self.repo, env=self.env, capture_output=True, text=True)

    def prepare(self):
        result = self.helper("prepare")
        self.assertIn(result.returncode, (0, 3), result.stderr)
        return json.loads(result.stdout)

    def proposal(self, *files):
        self.output.write_text(json.dumps({"commits": [{"message": "feat: add files", "files": list(files)}]}))

    def test_first_commit_literal_paths_and_preserve_other_staged_files(self):
        for name in ("a", "[literal]", "other"):
            (self.repo / name).write_text(name)
        self.git("add", "other")
        self.prepare()
        self.proposal("a", "[literal]")
        result = self.helper("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.git("ls-tree", "--name-only", "HEAD").splitlines()), {"a", "[literal]"})
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other")

    def test_all_commits_validated_before_any_git_write(self):
        (self.repo / "a").write_text("a")
        self.prepare()
        valid = {"message": "feat: add a", "files": ["a"]}
        for files in (["../outside"], ["."], ["a"], ["--amend"]):
            with self.subTest(files=files):
                self.output.write_text(json.dumps({"commits": [valid, {"message": "fix: bad", "files": files}]}))
                self.assertNotEqual(self.helper("apply").returncode, 0)
                self.assertEqual(self.git("ls-files"), "")

    def test_invalid_json_and_repository_change_do_not_commit(self):
        (self.repo / "a").write_text("a")
        self.prepare()
        self.output.write_text('git commit -m replacement --amend')
        self.assertNotEqual(self.helper("apply").returncode, 0)
        self.proposal("a")
        (self.repo / "a").write_text("changed")
        self.assertNotEqual(self.helper("apply").returncode, 0)
        self.assertEqual(self.git("ls-files"), "")

    def test_secrets_symlinks_binary_and_large_files_are_not_model_input(self):
        for name in (".env", "secret.pem", "a"):
            (self.repo / name).write_text("private" if name != "a" else "safe")
        (self.repo / "linked").symlink_to(".env")
        (self.repo / "binary").write_bytes(b"\0private")
        (self.repo / "large").write_bytes(b"x" * 32001)
        data = self.prepare()
        self.assertEqual([entry["file"] for entry in data["changes"]], ["a"])
        self.assertNotIn("private", json.dumps(data))

    def test_multiple_commits_deletion_and_empty_proposal(self):
        for name in ("a", "b"):
            (self.repo / name).write_text(name)
        self.prepare()
        self.output.write_text(json.dumps({"commits": [
            {"message": "feat: add a", "files": ["a"]},
            {"message": "feat: add b", "files": ["b"]}]}))
        self.assertEqual(self.helper("apply").returncode, 0)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "2")
        (self.repo / "a").unlink()
        self.prepare()
        self.proposal("a")
        self.assertEqual(self.helper("apply").returncode, 0)
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD"), "b")
        self.prepare()
        self.output.write_text('{"commits":[]}')
        self.assertEqual(self.helper("apply").returncode, 0)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "3")

    def test_agent_cli_failure_and_invalid_proposal_leave_head_unchanged(self):
        (self.repo / "a").write_text("a")
        shim = self.base / "shim"
        shim.mkdir()
        cli = shim / "claude"
        env = dict(self.env, AC_AGENT="claude", PATH=f"{shim}:/usr/bin:/bin")
        for body in ('echo "not json"; exit 0', 'echo \'{"commits":[]}\'; exit 7',
                     'echo \'{"commits":[{"message":"feat: add a","files":["a"]}]}\'; exit 0'):
            cli.write_text("#!/bin/sh\n" + body + "\n")
            cli.chmod(0o755)
            result = subprocess.run([str(SOURCE / "bin/autocommit-agent"), "-c"],
                                    cwd=self.repo, env=env, capture_output=True, text=True)
            if 'feat: add a' in body:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.git("log", "-1", "--format=%s"), "feat: add a")
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.git("ls-files"), "")

    def test_partially_staged_work_is_excluded(self):
        (self.repo / "a").write_text("staged")
        self.git("add", "a")
        (self.repo / "a").write_text("unstaged")
        self.assertEqual(self.prepare()["changes"], [])
        self.assertEqual(self.git("show", ":a"), "staged")

    def test_clean_repository_skips_model_call(self):
        shim = self.base / "shim"
        shim.mkdir()
        called = self.base / "called"
        cli = shim / "claude"
        cli.write_text(f'#!/bin/sh\ntouch "{called}"\nexit 97\n')
        cli.chmod(0o755)
        env = dict(self.env, AC_AGENT="claude", PATH=f"{shim}:/usr/bin:/bin")
        result = subprocess.run([str(SOURCE / "bin/autocommit-agent"), "-c"],
                                cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(called.exists())

    def test_timeout_also_bounds_local_commit_hooks(self):
        (self.repo / "a").write_text("a")
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexec sleep 30\n")
        hook.chmod(0o755)
        shim = self.base / "shim"
        shim.mkdir()
        cli = shim / "claude"
        cli.write_text('#!/bin/sh\necho \'{"commits":[{"message":"feat: add a","files":["a"]}]}\'\n')
        cli.chmod(0o755)
        env = dict(self.env, AC_AGENT="claude", HOC_TIMEOUT="1000", PATH=f"{shim}:/usr/bin:/bin")
        result = subprocess.run([str(SOURCE / "bin/autocommit-agent"), "-c"],
                                cwd=self.repo, env=env, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(self.git("ls-files"), "a")
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=self.repo,
                              env=self.env, capture_output=True)
        self.assertNotEqual(head.returncode, 0)
