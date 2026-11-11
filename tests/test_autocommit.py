import os
import json
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "bin/autocommit"

class AutocommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.guard = self.make_guard_bin()
        # 첫 Git 호출 전에 격리 환경을 만든다. 테스트의 모든 Git·실행기 호출이 이 환경을 쓴다.
        self.env = self.isolated_env(os.environ)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "a").write_text("initial")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.conf = self.base / "repos.conf"
        self.conf.write_text(f"{self.repo} * -\n")
        self.env.update(AC_STATE=str(self.base / "state"),
                        AC_CONF=str(self.conf), AC_PUSH="0", AC_CHECKPOINT_TRANSPORT="tab",
                        AC_SCAN_ROOTS=str(self.base), AC_SCAN_IGNORE=str(self.base / "ignore"),
                        AC_AUDIT_REMIND=str(self.base / "audit-remind"))
        self.addCleanup(self.assert_no_external_tool_calls)

    def isolated_env(self, source):
        """실행 환경의 AC_*·Herdr·Git 값과 사용자 HOME·Git 전역/시스템 설정(hook·서명·템플릿)을 끊고, 가짜 도구를 먼저 찾게 한다."""
        env = {k: v for k, v in source.items() if not k.startswith(("AC_", "HERDR_", "GIT_"))}
        home = self.base / "home"
        home.mkdir(exist_ok=True)
        gitconfig = self.base / "gitconfig"
        gitconfig.write_text("[user]\n\tname = Test\n\temail = test@example.invalid\n")
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                   PATH=f"{self.guard}{os.pathsep}{source.get('PATH', '')}")
        return env

    def make_guard_bin(self):
        """systemctl·herdr는 호출만 기록하고, 실제로 부르면 안 되는 도구는 기록 후 실패한다."""
        guard = self.base / "guard-bin"
        guard.mkdir()
        self.guard_calls = self.base / "guard-calls"
        record = f'printf "%s %s\\n" "$(basename "$0")" "$*" >> {shlex.quote(str(self.guard_calls))}\n'
        (guard / "systemctl").write_text("#!/bin/sh\n" + record + "exit 0\n")
        (guard / "herdr").write_text(
            "#!/bin/sh\n" + record +
            'case "$1 $2" in\n'
            '  "agent list") echo \'{"result":{"agents":[]}}\' ;;\n'
            '  "tab list") echo \'{"result":{"tabs":[]}}\' ;;\n'
            '  *) exit 1 ;;\nesac\n')
        for tool in ("hoc", "codex", "gh"):
            (guard / tool).write_text("#!/bin/sh\n" + record + "echo \"guard: $(basename \"$0\") must not run in tests\" >&2\nexit 97\n")
        for f in guard.iterdir():
            f.chmod(0o755)
        return guard

    def assert_no_external_tool_calls(self):
        calls = self.guard_calls.read_text() if self.guard_calls.exists() else ""
        unexpected = [line for line in calls.splitlines() if line.split(" ")[0] not in ("systemctl", "herdr")]
        self.assertEqual(unexpected, [], "실제 외부 도구 대신 가드가 호출됨")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL, text=True,
                                       env=self.env).strip()

    def shell(self, body):
        result = self.shell_rc(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def shell_rc(self, body):
        prefix = SOURCE.read_text().split('\ncase "$MODE" in\n')[0]
        definitions = self.base / "functions.sh"
        definitions.write_text(prefix)
        return subprocess.run(["bash", "-c", 'source "$1"; ' + body, "test", str(definitions)],
                              env=self.env, text=True, capture_output=True)

    def test_fixture_git_ignores_user_hooks_and_signing(self):
        # 사용자 전역 설정을 흉내 낸 hook·서명 도구: 격리하지 않으면 실행되고, 격리 환경에서는 실행되지 않아야 한다.
        user = self.base / "user-home"
        hooks = user / "hooks"
        hooks.mkdir(parents=True)
        ran = self.base / "user-config-ran"
        for name in ("pre-commit", "post-commit"):
            (hooks / name).write_text(f"#!/bin/sh\necho {name} >> {shlex.quote(str(ran))}\n")
            (hooks / name).chmod(0o755)
        signer = user / "signer"
        signer.write_text(f"#!/bin/sh\necho signer >> {shlex.quote(str(ran))}\nexit 1\n")
        signer.chmod(0o755)
        (user / ".gitconfig").write_text(
            "[user]\n\tname = U\n\temail = u@example.invalid\n"
            f"[core]\n\thooksPath = {hooks}\n[commit]\n\tgpgSign = true\n[gpg]\n\tprogram = {signer}\n")
        # 대조군도 이미 격리된 환경에서 출발한다 — 호스트의 GIT_DIR·GIT_INDEX_FILE 등이 끼어들지 않게 HOME·전역 설정만 바꾼다.
        source = dict(self.env, HOME=str(user), GIT_CONFIG_GLOBAL=str(user / ".gitconfig"))

        def commit(env, name):
            work = self.base / name
            subprocess.run(["git", "init", "-q", str(work)], check=True, env=env, capture_output=True)
            (work / "f").write_text("x")
            subprocess.run(["git", "-C", str(work), "add", "f"], check=True, env=env, capture_output=True)
            return subprocess.run(["git", "-C", str(work), "commit", "-qm", "x"], env=env, capture_output=True)

        commit(source, "user-config-control")
        self.assertIn("pre-commit", ran.read_text())                            # fixture가 실제로 작동한다
        ran.unlink()
        result = commit(self.isolated_env(source), "user-config-isolated")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(ran.exists())

    def test_settle_waits_for_unchanged_fingerprint(self):
        (self.repo / "a").write_text("changed")
        self.shell(f'NOW=1000; SETTLE=10; settled "{self.repo}" && exit 1; NOW=1011; settled "{self.repo}" || exit 1; '
                   f'echo more >> "{self.repo}/a"; settled "{self.repo}" && exit 1; true')

    def test_corrupt_log_timestamps_are_replaced_and_throttling_resumes(self):
        for value in ("", "garbage", "08", "2000", "9" * 30, "1+2"):
            with self.subTest(value=value):
                output = self.shell(
                    'NOW=1000; log() { echo "$*"; }; f="$STATE/once-$(hash test)"; '
                    f'printf "%s\\n" {shlex.quote(value)} > "$f"; '
                    'once_at 10 test first; once_at 10 test duplicate; '
                    'NOW=1010; once_at 10 test later; test "$(cat "$f")" = 1010')
                self.assertEqual(output.splitlines(), ["first", "later"])

    def test_corrupt_fingerprint_timestamp_restarts_commit_wait(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        for value in ("", "garbage", "08", "2000", "9" * 30):
            with self.subTest(value=value):
                self.shell(
                    f'NOW=1000; SETTLE=10; f="$STATE/fp-$(hash {r})"; '
                    f'printf "%s %s\\n" "$(fingerprint {r})" {shlex.quote(value)} > "$f"; '
                    f'settled {r} && exit 1; test "$AGE" = 0 || exit 1; '
                    f'NOW=1010; settled {r}')

    def test_corrupt_poll_record_wakes_repo_and_resets_backoff(self):
        r = shlex.quote(str(self.repo))
        for last, delay in (("garbage", "20"), ("2000", "20"), ("1000", "bad"),
                            ("1000", "08"), ("1000", "999999999999999999999")):
            with self.subTest(last=last, delay=delay):
                self.shell(
                    f'MODE=loop; NOW=1000; INTERVAL=10; IDLE_MAX_INTERVAL=40; '
                    f'f="$STATE/poll-$(hash {r})"; rm -f "$f"; repo_due {r} policy; '
                    f'printf "%s %s %s\\n" {shlex.quote(last)} {shlex.quote(delay)} "$POLL_SIGNATURE" > "$f"; '
                    f'repo_due {r} policy || exit 1; repo_schedule {r} {r}; '
                    'read -r last delay sig < "$f"; test "$last $delay" = "1000 10"')
        self.shell(f': > "$STATE/poll-$(hash {r})"; MODE=loop; repo_due {r} policy')

    def test_empty_fail_record_restarts_cooldown_without_calling_hoc(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        self.shell(
            f'NOW=1000; SETTLE=0; CHECKPOINT_AFTER=0; f="$STATE/fail-$(hash {r})"; : > "$f"; '
            'load_agents() { AGENTS_JSON="[]"; }; run_hoc() { exit 9; }; '
            f'commit_stage {r} "*" {r}; test "$(cat "$f")" = 1000')

    def test_empty_notification_checkpoint_and_periodic_records_recover(self):
        r = shlex.quote(str(self.repo))
        output = self.shell(
            'NOW=1000; FAIL_COOLDOWN=10; CHECKPOINT_AFTER=10; SCAN_INTERVAL=10; AUDIT_INTERVAL=10; '
            'log() { echo "$*"; }; herdr() { :; }; '
            ': > "$STATE/note-$(hash test)"; note test recovered; note test duplicate; '
            f'printf "%s\\n" "$(git -C {r} rev-parse HEAD)" > "$STATE/dirty-$(hash {r})"; '
            'checkpoint_dispatch() { echo "$NOW DISPATCH"; }; '
            f'checkpoint_notice {r} main; NOW=1010; checkpoint_notice {r} main; '
            ': > "$STATE/last-scan"; : > "$STATE/last-audit"; '
            'scan() { echo SCAN; }; audit() { echo AUDIT; }; tidy() { :; }; '
            'note() { :; }; scan_tick 2>&1; audit_tick 2>&1; scan_tick; audit_tick; true')
        self.assertEqual(output.count("NOTE recovered"), 1)
        self.assertNotIn("duplicate", output)
        self.assertEqual(output.count("DISPATCH"), 1)
        self.assertIn("1010 DISPATCH", output)
        self.assertEqual(output.splitlines().count("        SCAN"), 1)
        self.assertEqual(output.splitlines().count("        AUDIT"), 1)

    def test_failed_state_write_preserves_previous_record_and_blocks_commit(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        self.shell(
            f'NOW=1000; SETTLE=0; f="$STATE/fp-$(hash {r})"; echo original > "$f"; '
            'mktemp() { ln -s /dev/full "$STATE/full-write"; echo "$STATE/full-write"; }; '
            f'settled {r} && exit 1; test "$(cat "$f")" = original || exit 1; '
            'test ! -e "$STATE/full-write"')

    def test_corrupt_timers_in_plan_preserve_state_and_wait(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        self.shell(
            f'NOW=1000; DRY=1; SETTLE=10; f="$STATE/fp-$(hash {r})"; '
            f'printf "%s\\n" "$(fingerprint {r})" > "$f"; cp "$f" "$STATE/expected"; '
            f'settled {r} && exit 1; test "$AGE" = "?" || exit 1; '
            'cmp "$f" "$STATE/expected"')

    def test_busy_agent_blocks_commit(self):
        (self.repo / "a").write_text("changed")
        agents = shlex.quote(json.dumps([dict(cwd=str(self.repo), fcwd=None, st="working", name="test")]))
        self.shell(f'load_agents() {{ AGENTS_JSON={agents}; }}; '
                   'run_hoc() { echo unexpected; exit 9; }; '
                   f'commit_stage "{self.repo}" "*" "{self.repo}"')

    def test_scan_excludes_configured_repo_and_finds_new(self):
        other = self.base / "new"
        subprocess.run(["git", "init", "-q", str(other)], check=True, env=self.env)
        output = self.shell("scan")
        self.assertIn(str(other), output)
        self.assertNotIn(str(self.repo) + "  [", output)

    def test_unknown_agents_never_call_hoc(self):
        (self.repo / "a").write_text("changed")
        self.shell('load_agents() { return 1; }; note() { :; }; run_hoc() { exit 9; }; '
                   f'commit_stage "{self.repo}" "*" "{self.repo}"; test $? = 1')

    def test_clean_backoff_caps_and_manual_checks_bypass(self):
        r = shlex.quote(str(self.repo))
        self.shell(f'MODE=loop; NOW=1000; INTERVAL=10; IDLE_MAX_INTERVAL=40; '
                   f'repo_due {r} policy; repo_schedule {r} {r}; '
                   f'NOW=1005; repo_due {r} policy && exit 1; '
                   f'NOW=1010; repo_due {r} policy || exit 1; repo_schedule {r} {r}; '
                   f'read last delay sig < "$STATE/poll-$(hash {r})"; test "$delay" = 20 || exit 1; '
                   f'NOW=1030; repo_due {r} policy; repo_schedule {r} {r}; '
                   f'NOW=1070; repo_due {r} policy; repo_schedule {r} {r}; '
                   f'read last delay sig < "$STATE/poll-$(hash {r})"; test "$delay" = 40 || exit 1; '
                   f'MODE=plan; repo_due {r} policy || exit 1; MODE=once; repo_due {r} policy')

    def test_refs_policy_and_worktrees_wake_up(self):
        r = shlex.quote(str(self.repo))
        self.shell(f'MODE=loop; NOW=1000; repo_due {r} old; repo_schedule {r} {r}; '
                   f'NOW=1001; repo_due {r} old && exit 1; repo_due {r} new || exit 1; '
                   f'git -C {r} branch extra; repo_due {r} old || exit 1; '
                   f'repo_schedule {r} {r}; '
                   f'git -C {r} worktree add -q "{self.base}/linked" extra; '
                   f'repo_due {r} old || exit 1')

    def test_file_edit_wakes_backed_off_repo(self):
        r = shlex.quote(str(self.repo))
        self.shell(f'MODE=loop; NOW=1000; INTERVAL=10; IDLE_MAX_INTERVAL=40; '
                   f'for t in 1000 1010 1030 1070; do NOW=$t; repo_due {r} policy; repo_schedule {r} {r}; done; '
                   f'NOW=1071; repo_due {r} policy && exit 1; '
                   f'echo change >> {r}/a; repo_due {r} policy || exit 1; '
                   f'git -C {r} checkout -q a; touch {r}/untracked; repo_due {r} policy')

    def test_dirty_returns_to_base_interval(self):
        r = shlex.quote(str(self.repo))
        self.shell(f'MODE=loop; NOW=1000; INTERVAL=10; repo_due {r} policy; repo_schedule {r} {r}; '
                   f'NOW=1010; repo_due {r} policy; repo_schedule {r} {r}; '
                   f'echo change >> {r}/a; NOW=1030; repo_due {r} policy; repo_schedule {r} {r}; '
                   f'read last delay sig < "$STATE/poll-$(hash {r})"; test "$delay" = 10')

    def test_checkpoint_falls_back_to_note_once_per_head_and_resets_after_commit(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        output = self.shell(f'NOW=1000; CHECKPOINT_AFTER=10; '
                            'note() { echo "$*"; }; checkpoint_dispatch() { echo dispatch; return 1; }; '
                            f'checkpoint_notice {r} main; NOW=1009; checkpoint_notice {r} main; '
                            f'NOW=1010; checkpoint_notice {r} main; NOW=5000; checkpoint_notice {r} main; '
                            f'git -C {r} add a; git -C {r} commit -qm next; '
                            f'NOW=5001; checkpoint_notice {r} main; echo more >> {r}/a; '
                            f'NOW=5011; checkpoint_notice {r} main')
        self.assertEqual(output.count("dispatch"), 2)
        self.assertEqual(output.count("중간 커밋 점검"), 2)

    def test_checkpoint_spawns_read_only_status_agent_in_agent_workspace(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        agents = shlex.quote(json.dumps([dict(cwd=str(self.repo), foreground_cwd=None, agent_status="working",
                                              name="worker", workspace_id="wX")]))
        output = self.shell(
            'herdr() { printf "%s\\n" "$*" >> "$STATE/calls"; case "$1 $2" in '
            f'"agent list") printf \'{{"result":{{"agents":%s}}}}\' {agents} ;; '
            '"tab create") echo \'{"result":{"tab":{"tab_id":"wX:t9"},"root_pane":{"pane_id":"wX:p9"}}}\' ;; esac; }; '
            'note() { echo "NOTE $*"; }; NOW=1000; CHECKPOINT_AFTER=10; '
            f'checkpoint_notice {r} main {r}; NOW=1010; checkpoint_notice {r} main {r}; '
            f'NOW=1020; checkpoint_notice {r} main {r}; DRY=1; rm -f "$STATE"/ckpt-*; '
            f'NOW=1030; checkpoint_notice {r} main {r}; cat "$STATE/calls"')
        self.assertNotIn("NOTE", output)
        self.assertEqual(output.count("tab create"), 1)
        self.assertIn(f"tab create --workspace wX --cwd {self.repo}", output)
        self.assertIn("--kind codex", output)
        self.assertIn("--timeout 60000 -- -c model_reasoning_effort=high -s read-only -a never", output)  # 모델 미지정 → codex 기본
        self.assertEqual(output.count("agent prompt"), 1)
        self.assertIn("worker:working", output)
        self.assertIn("현황파악만", output)

    def test_background_checkpoint_has_no_tabs_and_blocks_commit_until_complete(self):
        r = shlex.quote(str(self.repo))
        output = self.shell(
            'CHECKPOINT_TRANSPORT=background; AGENTS_JSON="[]"; '
            'load_agents() { AGENTS_JSON="[]"; }; '
            'herdr() { echo unexpected >> "$STATE/tabs"; }; '
            'note() { :; }; '
            'codex() { printf "%s\\n" "$*" > "$STATE/args"; touch "$STATE/started"; '
            'while [ ! -f "$STATE/release" ]; do sleep 0.02; done; '
            'while [ "$1" != -o ]; do shift; done; echo report > "$2"; }; '
            'timeout() { shift; "$@"; }; '
            f'h=$(git -C {r} rev-parse HEAD); checkpoint_dispatch {r} main "$h" 3 {r}; '
            'for i in {1..100}; do [ -f "$STATE/started" ] && break; sleep 0.02; done; '
            f'busy=$(busy_agent_in {r} {r}); test -n "$busy" || exit 1; '
            'touch "$STATE/release"; wait; '
            f'test -z "$(busy_agent_in {r} {r})" || exit 1; '
            'test ! -f "$STATE/tabs" || exit 1; '
            'cat "$STATE/args"; cat "$STATE"/ckpt-*.md; cat "$STATE"/ckpt-*.rc')
        self.assertIn("exec -C", output)
        self.assertIn("-s read-only", output)
        self.assertIn("approval_policy=never", output)
        self.assertIn("report", output)
        self.assertTrue(output.rstrip().endswith("0"))

    def test_background_checkpoint_usage_limit_reports_failure_without_panes(self):
        r = shlex.quote(str(self.repo))
        output = self.shell(
            'CHECKPOINT_TRANSPORT=background; load_agents() { AGENTS_JSON="[]"; }; '
            'herdr() { echo unexpected >> "$STATE/tabs"; }; note() { echo "NOTE $*" >> "$STATE/notes"; }; '
            'codex() { echo "hit your usage limit"; return 1; }; '
            'timeout() { shift; "$@"; }; '
            f'h=$(git -C {r} rev-parse HEAD); checkpoint_dispatch {r} main "$h" 3 {r}; wait; '
            'test ! -f "$STATE/tabs" || exit 1; '
            'cat "$STATE"/ckpt-*.rc; cat "$STATE/notes"')
        self.assertIn("현황 조사 실패", output)
        self.assertTrue(output.startswith("1"))

    def test_checkpoint_notes_codex_usage_limit_once(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        output = self.shell(
            'herdr() { printf "%s\\\\n" "$*" >> "$STATE/calls"; case "$1 $2" in '
            '"agent get") printf \'{"result":{"agent":{"agent_status":"%s"}}}\' "$(cat "$STATE/st")" ;; '
            '"agent read") cat "$STATE/screen" ;; '
            '"pane split") echo \'{"result":{"pane":{"pane_id":"wX:p10"}}}\' ;; esac; }; '
            'sleep() { :; }; note() { echo "NOTE $*"; }; CHECKPOINT_AFTER=10; '
            f'h=$(git -C {r} rev-parse HEAD); mark="$STATE/ckpt-$(hash {r})"; '
            f'NOW=1000; checkpoint_notice {r} main {r}; echo "$h codex ckpt-x wX:t9 wX:p9" > "$mark"; '
            'echo working > "$STATE/st"; NOW=1010; '
            f'checkpoint_notice {r} main {r}; read -r _ s1 _ < "$mark"; '
            'echo done > "$STATE/st"; echo "■ You\'ve hit your usage limit. Visit ..." > "$STATE/screen"; '
            f'NOW=1020; checkpoint_notice {r} main {r}; read -r _ s2 _ < "$mark"; '
            f'NOW=1030; checkpoint_notice {r} main {r}; '
            'echo "s1=$s1 s2=$s2"; cat "$STATE/calls"')
        self.assertIn("s1=codex s2=note", output)
        self.assertNotIn("pane split", output)
        self.assertEqual(output.count("codex 한도 초과로 현황 조사 실패"), 1)

    def test_checkpoint_followup_marks_done_without_usage_limit(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed")
        output = self.shell(
            'herdr() { case "$1 $2" in "agent get") echo \'{"result":{"agent":{"agent_status":"done"}}}\' ;; '
            '"agent read") echo "report" ;; "pane split") echo split ;; esac; }; '
            f'h=$(git -C {r} rev-parse HEAD); mark="$STATE/ckpt-$(hash {r})"; '
            'checkpoint_followup ' + r + ' main "$h" 3 ckpt-x wX:t9 wX:p9; cat "$mark"')
        self.assertIn(" done ckpt-x", output)
        self.assertNotIn("split", output)

    def test_scan_tick_waits_for_interval_and_ignore_is_respected(self):
        other = self.base / "new"
        subprocess.run(["git", "init", "-q", str(other)], check=True, env=self.env)
        self.env["AC_SCAN_INTERVAL"] = "10"
        (self.base / "ignore").write_text(str(other) + "\n")
        self.assertEqual(self.shell("scan"), "")
        (self.base / "ignore").unlink()
        output = self.shell('note() { :; }; log() { echo "$*"; }; NOW=1000; scan_tick; NOW=1001; scan_tick; NOW=1010; scan_tick')
        self.assertEqual(output.count("SCAN  "), 2)

    def test_read_only_scan_and_plan_preserve_state(self):
        state = self.base / "state"
        state.mkdir()
        marker = state / "fp-placeholder"
        marker.write_text("keep")
        before = {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in state.iterdir()}
        for mode in ("scan", "plan"):
            result = subprocess.run([str(SOURCE), mode], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        after = {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in state.iterdir()}
        self.assertEqual(before, after)
        self.env["AC_STATE"] = str(self.base / "absent-state")
        subprocess.run([str(SOURCE), "scan"], env=self.env, check=True, capture_output=True)
        self.assertFalse((self.base / "absent-state").exists())

    def test_hoc_success_and_failed_head_move_marker(self):
        fake = self.base / "hoc"
        fake.write_text('#!/usr/bin/env bash\ngit add a\ngit commit -qm "chore: checkpoint"\nexit "${FAKE_RC:-0}"\n')
        fake.chmod(0o755)
        self.env["AC_HOC"] = str(fake)
        (self.repo / "a").write_text("changed")
        r = shlex.quote(str(self.repo))
        original = self.git("rev-parse", "HEAD")
        self.shell(f'run_hoc {r} main {r}; test $? = 0')
        self.assertNotEqual(original, self.git("rev-parse", "HEAD"))
        (self.repo / "a").write_text("changed again")
        self.env["FAKE_RC"] = "1"
        self.shell('note() { :; }; ' + f'run_hoc {r} main {r}; test $? = 1; '
                   f'test -f "$STATE/nopush-$(hash "{self.repo}:main")"')

    def test_dirty_linked_worktree_prevents_backoff(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "-b", "linked", str(linked))
        (linked / "a").write_text("unfinished")
        r = shlex.quote(str(self.repo))
        self.shell(f'MODE=loop; INTERVAL=10; NOW=1000; repo_due {r} policy; '
                   f'repo_schedule {r} "$(worktrees_of {r})"; NOW=1010; repo_due {r} policy; '
                   f'repo_schedule {r} "$(worktrees_of {r})"; '
                   f'read last delay sig < "$STATE/poll-$(hash {r})"; test "$delay" = 10')

    def test_first_commit_and_unchanged_unborn_branch(self):
        self.git("update-ref", "-d", "refs/heads/main")
        self.fake_hoc('exit 0\n')
        self.assertEqual(self.run_hoc_rc(), "rc=2")
        self.assertFalse(self.inflight().exists())
        self.fake_hoc('git add a; git commit -qm "chore: initialize repository"\n')
        self.assertEqual(self.run_hoc_rc(), "rc=0")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "1")
        self.assertFalse(self.inflight().exists())

    def test_broken_head_is_not_treated_as_unborn(self):
        (self.repo / ".git/refs/heads/main").write_text("0" * 40 + "\n")
        ran = self.base / "hoc-ran"
        self.fake_hoc(f'touch {shlex.quote(str(ran))}\n')
        self.assertEqual(self.run_hoc_rc(), "rc=1")
        self.assertFalse(ran.exists())

    def test_install_backup_idempotence_and_config_preservation(self):
        home = self.base / "home"
        target = home / ".local/bin/autocommit"
        target.parent.mkdir(parents=True)
        target.write_text("previous executable")
        config = home / ".config/autocommit/repos.conf"
        config.parent.mkdir(parents=True)
        config.write_text("keep local policy")
        env = dict(self.env, HOME=str(home))
        installer = SOURCE.parents[1] / "install.sh"
        for _ in range(2):
            subprocess.run([str(installer), "--init-config"], env=env, check=True, capture_output=True)
        self.assertEqual(target.resolve(), SOURCE)
        calls = self.guard_calls.read_text().splitlines()                    # 실제 사용자 서비스 대신 가짜 systemctl만 불렸다
        self.assertIn("systemctl --user enable --now autocommit-review.timer", calls)
        backups = list(target.parent.glob("autocommit.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "previous executable")
        self.assertEqual(config.read_text(), "keep local policy")

    def test_install_rejects_different_symlink(self):
        home = self.base / "home"
        target = home / ".local/bin/autocommit"
        target.parent.mkdir(parents=True)
        old = self.base / "other"
        old.write_text("keep")
        target.symlink_to(old)
        result = subprocess.run([str(SOURCE.parents[1] / "install.sh")],
                                env=dict(self.env, HOME=str(home)), capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.resolve(), old)

    def test_discovery_notification_includes_repo_path(self):
        other = self.base / "new"
        subprocess.run(["git", "init", "-q", str(other)], check=True, env=self.env)
        output = self.shell('herdr() { printf "%s\\n" "$*" > "$STATE/delivery"; }; '
                            'NOW=100000; scan_tick; cat "$STATE/delivery"')
        self.assertIn("notification show autocommit", output)
        self.assertIn("새 저장소 1개:", output)
        self.assertIn(str(other), output)
        self.assertIn("--sound request", output)

    def test_notification_failure_is_logged(self):
        self.shell('herdr() { return 1; }; NOW=10000; note test "new repository"; '
                   'grep -q "herdr 알림 전달 실패" "$LOG"')

    def commit_range(self, files=None, delete=()):
        """임시 저장소에 커밋 하나를 더 만들고 (이전 HEAD, 새 HEAD)를 돌려준다."""
        before = self.git("rev-parse", "HEAD")
        for name, content in (files or {}).items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for name in delete:
            self.git("rm", "-q", name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "range")
        return before, self.git("rev-parse", "HEAD")

    def make_push_remote(self):
        remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, env=self.env)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-u", "origin", "main")
        self.conf.write_text(f"{self.repo} * origin\n")
        self.env.update(AC_PUSH="1", AC_REMOTE_ALLOW=".*", AC_SETTLE="10")
        return self.commit_range({"a": b"committed"})

    def remote_head(self):
        return self.git("ls-remote", "--heads", "origin", "refs/heads/main").split()[0]

    def push_shell(self, body):
        r = shlex.quote(str(self.repo))
        return self.shell('load_agents() { AGENTS_JSON="[]"; }; note() { :; }; '
                          'date() { if [ "$1" = +%s ]; then echo "$NOW"; else command date "$@"; fi; }; '
                          f'push_test() {{ push_stage {r} origin "*"; }}; ' + body)

    def test_unset_remote_allow_never_pushes(self):
        before, _ = self.make_push_remote()
        self.env["AC_REMOTE_ALLOW"] = ""
        notes = self.push_shell('note() { echo "NOTE $*"; }; NOW=1000; push_test; NOW=1020; push_test')
        self.assertEqual(self.remote_head(), before)
        self.assertIn("AC_REMOTE_ALLOW 미설정", notes)

    def test_push_waits_for_stable_head_and_restarts_after_amend(self):
        before, original = self.make_push_remote()
        self.push_shell('NOW=1000; push_test; NOW=1009; push_test')
        self.assertEqual(self.remote_head(), before)
        self.git("commit", "--amend", "-q", "-m", "corrected")
        amended = self.git("rev-parse", "HEAD")
        self.assertNotEqual(original, amended)
        self.push_shell('NOW=1010; push_test; NOW=1019; push_test')
        self.assertEqual(self.remote_head(), before)
        self.push_shell('NOW=1020; push_test')
        self.assertEqual(self.remote_head(), amended)

    def test_corrupt_push_timestamps_restart_stability_wait(self):
        before, after = self.make_push_remote()
        r = shlex.quote(str(self.repo))
        for value in ("", "garbage", "08", "2000", "9" * 30):
            with self.subTest(value=value):
                self.push_shell(
                    f'f="$STATE/push-fp-$(hash {r}:main)"; '
                    f'printf "%s %s\\n" {after} {shlex.quote(value)} > "$f"; '
                    'NOW=1000; push_test; NOW=1009; push_test')
                self.assertEqual(self.remote_head(), before)
        self.push_shell('NOW=1010; push_test')
        self.assertEqual(self.remote_head(), after)

    def test_push_transfer_timeout_is_separate_from_remote_lookup(self):
        _, after = self.make_push_remote()
        calls = self.base / "timeouts"
        self.push_shell('timeout() { printf "%s\\n" "$*" >> '
                        + shlex.quote(str(calls)) + '; shift; "$@"; }; '
                        'REMOTE_TIMEOUT=3; PUSH_TIMEOUT=900; MODE=push-only; push_test')
        lines = calls.read_text().splitlines()
        self.assertTrue(any(line.startswith("3 git ") and "ls-remote" in line for line in lines))
        self.assertTrue(any(line.startswith("900 git ") and " push " in line for line in lines))
        self.assertEqual(self.remote_head(), after)

    def test_failed_stability_write_never_pushes_even_with_zero_settle(self):
        before, _ = self.make_push_remote()
        r = shlex.quote(str(self.repo))
        self.push_shell(
            f'f="$STATE/push-fp-$(hash {r}:main)"; echo original > "$f"; '
            'mktemp() { ln -s /dev/full "$STATE/full-write"; echo "$STATE/full-write"; }; '
            'SETTLE=0; NOW=1000; push_test; test "$(cat "$f")" = original')
        self.assertEqual(self.remote_head(), before)

    def test_busy_or_unknown_agents_block_push_and_restart_wait(self):
        before, after = self.make_push_remote()
        self.push_shell('NOW=1000; push_test')
        for status in ("working", "blocked", "unknown"):
            with self.subTest(status=status):
                agents = shlex.quote(json.dumps([dict(cwd=str(self.repo), fcwd=None,
                                                      st=status, name="worker")]))
                self.push_shell(f'load_agents() {{ AGENTS_JSON={agents}; }}; '
                                'NOW=1020; push_test')
                self.assertEqual(self.remote_head(), before)
        self.push_shell('NOW=1021; push_test')
        self.assertEqual(self.remote_head(), before)
        self.push_shell('NOW=1031; push_test')
        self.assertEqual(self.remote_head(), after)

    def test_agent_lookup_failure_blocks_push(self):
        before, _ = self.make_push_remote()
        self.push_shell('NOW=1000; push_test; load_agents() { return 1; }; '
                        'NOW=1020; push_test')
        self.assertEqual(self.remote_head(), before)

    def test_tracked_edits_block_push_but_existing_untracked_files_do_not(self):
        before, after = self.make_push_remote()
        (self.repo / "human-note.hwp").write_bytes(b"preserve")
        self.push_shell('NOW=1000; push_test')
        (self.repo / "a").write_text("unfinished")
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    self.git("add", "a")
                self.push_shell('NOW=1020; push_test')
                self.assertEqual(self.remote_head(), before)
        self.git("restore", "--source=HEAD", "--staged", "--worktree", "a")
        self.push_shell('NOW=1021; push_test')
        self.assertEqual(self.remote_head(), before)
        self.push_shell('NOW=1031; push_test')
        self.assertEqual(self.remote_head(), after)
        self.assertEqual((self.repo / "human-note.hwp").read_bytes(), b"preserve")

    def test_commit_disabled_repo_pushes_committed_sha_preserving_dirty_files(self):
        _, after = self.make_push_remote()
        (self.repo / "a").write_text("staged work")
        self.git("add", "a")
        (self.repo / "a").write_text("unstaged work")
        index_before = (self.repo / ".git/index").read_bytes()
        self.conf.write_text(f"{self.repo} - origin main\n")
        self.shell('MODE=push-only; SETTLE=0; load_agents() { AGENTS_JSON="[]"; }; tick')
        self.assertEqual(self.remote_head(), after)
        self.assertEqual((self.repo / "a").read_text(), "unstaged work")
        self.assertEqual((self.repo / ".git/index").read_bytes(), index_before)
        self.commit_range({"a": b"another commit"})
        (self.repo / "a").write_text("unfinished")
        self.shell('MODE=push-only; load_agents() { AGENTS_JSON=\'[{'
                   f'"cwd":"{self.repo}","fcwd":null,"st":"working","name":"worker"'
                   '}]\'; }; tick')
        self.assertEqual(self.remote_head(), after)

    def test_head_change_during_push_checks_never_publishes_old_head(self):
        before, _ = self.make_push_remote()
        self.push_shell('NOW=1000; push_test')
        r = shlex.quote(str(self.repo))
        self.push_shell(f'unsafe_files() {{ git -C {r} commit --amend -qm corrected; }}; '
                        'NOW=1020; push_test')
        self.assertEqual(self.remote_head(), before)
        amended = self.git("rev-parse", "HEAD")
        self.push_shell('NOW=1021; push_test; NOW=1031; push_test')
        self.assertEqual(self.remote_head(), amended)

    def test_push_rechecks_agent_activity_immediately_before_publish(self):
        before, _ = self.make_push_remote()
        self.push_shell('NOW=1000; push_test')
        agents = shlex.quote(json.dumps([dict(cwd=str(self.repo), fcwd=None,
                                              st="working", name="worker")]))
        self.push_shell('echo "[]" > "$STATE/test-agents"; '
                        'load_agents() { AGENTS_JSON=$(cat "$STATE/test-agents"); }; '
                        f'unsafe_files() {{ printf "%s" {agents} > "$STATE/test-agents"; }}; '
                        'NOW=1020; push_test')
        self.assertEqual(self.remote_head(), before)

    def test_push_plan_does_not_record_stability_state(self):
        before, _ = self.make_push_remote()
        shim = self.base / "shim"
        shim.mkdir()
        herdr = shim / "herdr"
        herdr.write_text('#!/bin/sh\nprintf \'{"result":{"agents":[]}}\\n\'\n')
        herdr.chmod(0o755)
        self.env["PATH"] = f"{shim}{os.pathsep}{self.env['PATH']}"
        state = self.base / "state"
        state.mkdir()
        marker = state / "keep"
        marker.write_text("unchanged")
        snapshot = lambda: {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                            for p in state.iterdir()}
        expected = snapshot()
        result = subprocess.run([str(SOURCE), "plan"], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snapshot(), expected)
        self.assertEqual(self.remote_head(), before)

    def test_pending_push_keeps_base_polling_interval(self):
        self.make_push_remote()
        r = shlex.quote(str(self.repo))
        self.push_shell('MODE=loop; INTERVAL=5; NOW=1000; push_test; '
                        f'repo_due {r} policy; repo_schedule {r} {r}; '
                        'NOW=1005; push_test; '
                        f'repo_due {r} policy; repo_schedule {r} {r}; '
                        f'read last delay sig < "$STATE/poll-$(hash {r})"; test "$delay" = 5')

    def test_unchecked_out_branch_still_waits_for_stable_sha(self):
        before, after = self.make_push_remote()
        self.git("checkout", "-b", "other")
        self.push_shell('NOW=1000; push_test')
        self.assertEqual(self.remote_head(), before)
        self.push_shell('NOW=1010; push_test')
        self.assertEqual(self.remote_head(), after)
        self.assertEqual(self.git("branch", "--show-current"), "other")

    def test_manual_push_only_skips_delay_but_respects_busy_agents(self):
        before, after = self.make_push_remote()
        agents = shlex.quote(json.dumps([dict(cwd=str(self.repo), fcwd=None,
                                              st="working", name="worker")]))
        self.push_shell(f'MODE=push-only; load_agents() {{ AGENTS_JSON={agents}; }}; '
                        'NOW=1000; push_test')
        self.assertEqual(self.remote_head(), before)
        self.push_shell('MODE=push-only; NOW=1000; push_test')
        self.assertEqual(self.remote_head(), after)

    def test_push_stability_uses_current_time_after_slow_tick(self):
        before, after = self.make_push_remote()
        clock = 'date() { if [ "$1" = +%s ]; then cat "$STATE/test-clock"; else command date "$@"; fi; }; '
        self.push_shell(clock + 'echo 1100 > "$STATE/test-clock"; NOW=1000; push_test; '
                        'echo 1105 > "$STATE/test-clock"; NOW=2000; push_test')
        self.assertEqual(self.remote_head(), before)
        self.push_shell(clock + 'echo 1110 > "$STATE/test-clock"; NOW=2000; push_test')
        self.assertEqual(self.remote_head(), after)

    def unsafe(self, frm, to, max_mb=1):
        # 크기 기준은 production 기본값이 아니라 이 검사가 정한 작은 값으로 고정한다.
        return self.shell(f'MAX_FILE_MB={max_mb}; unsafe_files {shlex.quote(str(self.repo))} {frm} {to} || true')

    def test_unsafe_files_reports_forbidden_paths_only(self):
        frm, to = self.commit_range({"config/.env": b"TOKEN=x\n", "keys/server.pem": b"pem\n",
                                     "src/ok.txt": b"fine\n"})
        self.assertEqual(sorted(self.unsafe(frm, to).splitlines()),
                         ["forbidden: config/.env", "forbidden: keys/server.pem"])

    def test_unsafe_files_size_threshold_blocks_only_files_above_it(self):
        mib = 1024 * 1024
        frm, to = self.commit_range({"below.bin": b"x" * (mib - 1), "equal.bin": b"x" * mib,
                                     "above.bin": b"x" * (mib + 1)})
        self.assertEqual(self.unsafe(frm, to).splitlines(), ["too large (1MB): above.bin"])

    def test_unsafe_files_ignores_a_deletion_only_range(self):
        frm, to = self.commit_range(delete=["a"])
        self.assertEqual(self.unsafe(frm, to), "")

    def test_unsafe_files_blocks_when_history_lookup_fails(self):
        frm = "0" * 40  # 존재하지 않는 커밋: 이력 조회가 실패한다.
        to = self.git("rev-parse", "HEAD")
        output = self.unsafe(frm, to)
        self.assertTrue(output.startswith("scan failed: 이력 조회"), output)

    def test_unsafe_files_blocks_when_object_lookup_fails_or_returns_nothing(self):
        # 크기 확인은 ls-tree 대신 객체(cat-file) 기준으로 바뀌었다. 조회 실패·빈 결과가 보류로 이어지는 정책은 같다.
        frm, to = self.commit_range({"src/new.txt": b"new\n"})
        real_git = subprocess.check_output(["sh", "-c", "command -v git"], text=True, env=self.env).strip()
        shim = self.base / "shim"
        shim.mkdir()
        for mode, expected in (("fail", "scan failed: 객체 조회"), ("empty", "scan failed: 객체 조회")):
            with self.subTest(mode=mode):
                # 객체 조회(cat-file)만 바꾼다. 이력 조회는 실제 git으로 통과시킨다.
                outcome = "exit 1" if mode == "fail" else "exit 0"
                (shim / "git").write_text(
                    "#!/bin/sh\n"
                    f'for arg in "$@"; do [ "$arg" = cat-file ] && {outcome}; done\n'
                    f'exec {shlex.quote(real_git)} "$@"\n')
                (shim / "git").chmod(0o755)
                saved = self.env
                self.env = dict(saved, PATH=f"{shim}{os.pathsep}{saved['PATH']}")
                try:
                    output = self.unsafe(frm, to)
                finally:
                    self.env = saved
                self.assertTrue(output.startswith(expected), output)

    def test_tidy_removes_only_provably_inert_markers(self):
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("declined by hoc")
        out = self.shell(
            'NOW=100000; FAIL_COOLDOWN=3600; '
            f'echo $((NOW-4000)) > "$STATE/fail-$(hash {r})"; '
            f'echo $((NOW-10)) > "$STATE/fail-$(hash x-fresh)"; '
            f'fingerprint {r} > "$STATE/nochg-$(hash {r})"; '
            'echo stale > "$STATE/nochg-$(hash /gone/worktree)"; '
            f'echo "branch changed" > "$STATE/nopush-$(hash {r}:main)"; '
            'echo "unmapped" > "$STATE/nopush-deadbeefdeadbeef"; '
            'tidy; ls "$STATE"')
        self.assertNotIn("fail-" + self.hash(str(self.repo)), out)
        self.assertIn("fail-" + self.hash("x-fresh"), out)
        self.assertIn("nochg-" + self.hash(str(self.repo)), out)       # 아직 막고 있다
        self.assertIn("nochg-" + self.hash("/gone/worktree"), out)     # 매핑 불명은 보존
        # 설정상 push 꺼짐은 "지금 비활성"일 뿐 위반 해결이 아니다 — 설정을 되돌리면 다시 막아야 하므로 보존한다(설계 C.1)
        self.assertIn("nopush-" + self.hash(f"{self.repo}:main"), out)
        self.assertIn("nopush-deadbeefdeadbeef", out)
        (self.repo / "a").write_text("changed again")
        out = self.shell('tidy; ls "$STATE"')
        self.assertNotIn("nochg-" + self.hash(str(self.repo)), out)    # 지문이 바뀌었다

    def test_tidy_releases_nopush_only_when_real_remote_contains_branch(self):
        before, after = self.make_push_remote()
        mark = f'"$STATE/nopush-$(hash "{self.repo}:main")"'
        listing = f'tidy; test -f {mark} && echo KEPT || echo GONE'
        self.assertIn("KEPT", self.shell(f'echo x > {mark}; {listing}'))       # 내보낼 커밋이 있다
        self.git("push", "origin", "main")
        remote = self.base / "remote.git"
        subprocess.run(["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", before], check=True, env=self.env)
        self.assertIn("KEPT", self.shell(listing))                              # 추적 ref는 포함이라 말해도 실제 원격은 아니다
        subprocess.run(["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", after], check=True, env=self.env)
        self.assertIn("GONE", self.shell(listing))

    def test_tidy_keeps_nopush_when_remote_lookup_fails_after_output(self):
        # 원격 조회가 유효한 SHA를 낸 뒤 오류·timeout으로 끝나면 조회 실패다 — 부분 출력으로 마커를 지우지 않는다.
        self.make_push_remote()
        self.git("push", "-q", "origin", "main")
        mark = f'"$STATE/nopush-$(hash "{self.repo}:main")"'
        listing = f'tidy; test -f {mark} && echo KEPT || echo GONE'
        self.shell(f'echo violation > {mark}')
        real_git = subprocess.check_output(["sh", "-c", "command -v git"], text=True, env=self.env).strip()
        shim = self.base / "ls-remote-shim"
        shim.mkdir()
        for name, after in (("error after output", "exit 1"), ("timeout after output", "exec sleep 5")):
            with self.subTest(name=name):
                (shim / "git").write_text(
                    "#!/bin/sh\n"
                    f'case " $* " in *" ls-remote "*) {shlex.quote(real_git)} "$@"; {after} ;; esac\n'
                    f'exec {shlex.quote(real_git)} "$@"\n')
                (shim / "git").chmod(0o755)
                saved = self.env
                self.env = dict(saved, PATH=f"{shim}{os.pathsep}{saved['PATH']}", AC_REMOTE_TIMEOUT="1")
                try:
                    self.assertIn("KEPT", self.shell(listing))
                finally:
                    self.env = saved
        self.assertIn("GONE", self.shell(listing))                              # 정상 조회에서만 정리한다

    def review_env(self, mail_rc="0"):
        fake = self.base / "fakebin"
        fake.mkdir(exist_ok=True)
        (fake / "codex").write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                                    'cat > /dev/null; echo "fake report" > "$out"\n')
        mail = self.base / "mail"
        (fake / "mail").write_text(f'#!/bin/bash\necho "$1" > {mail}.subject; cat > {mail}.body; exit {mail_rc}\n')
        for f in fake.iterdir():
            f.chmod(0o755)
        env = dict(self.env, PATH=f"{fake}:{self.env['PATH']}", AC_REVIEW_MAIL_CMD=str(fake / "mail"))
        return env, mail

    def run_review(self, env):
        return subprocess.run(["bash", str(SOURCE), "review"], env=env, text=True, capture_output=True)

    def test_review_mails_agent_report_only_when_items_remain(self):
        env, mail = self.review_env()
        result = self.run_review(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(Path(f"{mail}.subject").exists())                     # 남은 항목 0건 — 메일 없음
        (self.repo / "junk").write_text("generated")
        self.age_nochg()
        result = self.run_review(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("남은 항목 1건", Path(f"{mail}.subject").read_text())
        body = Path(f"{mail}.body").read_text()
        self.assertIn("fake report", body)
        self.assertIn("?? junk", body)                                         # .gitignore 판단 근거가 실린다

    def test_review_fails_when_mail_fails(self):
        env, _ = self.review_env(mail_rc="1")
        (self.repo / "junk").write_text("generated")
        self.age_nochg()
        self.assertEqual(self.run_review(env).returncode, 1)

    def test_review_skips_mail_for_fresh_markers(self):
        env, mail = self.review_env()
        (self.repo / "junk").write_text("generated")
        self.age_nochg(days=0)
        self.shell('echo $(date +%s) > "$STATE/fail-deadbeefdeadbeef"')        # 매핑 불명 fail은 스스로 풀린다
        self.assertEqual(self.run_review(env).returncode, 0)
        self.assertFalse(Path(f"{mail}.subject").exists())

    def age_nochg(self, days=4):
        r = shlex.quote(str(self.repo))
        self.shell(f'f="$STATE/nochg-$(hash {r})"; fingerprint {r} > "$f"; touch -d "{days} days ago" "$f"')

    # ---- A. 점유 판정 ----
    def agents(self, *entries):
        return shlex.quote(json.dumps([dict(dict(fcwd=None, st="working", name="worker"), **e) for e in entries]))

    def test_agent_in_subdirectory_or_foreground_subdirectory_blocks_commit(self):
        (self.repo / "a").write_text("changed")
        sub = str(self.repo / "src")
        for entry in (dict(cwd=sub), dict(cwd="/elsewhere", fcwd=sub), dict(cwd=str(self.repo) + "/")):
            with self.subTest(entry=entry):
                self.shell(f'SETTLE=0; CHECKPOINT_AFTER=0; load_agents() {{ AGENTS_JSON={self.agents(entry)}; }}; '
                           'run_hoc() { echo unexpected; exit 9; }; '
                           f'commit_stage "{self.repo}" "*" "{self.repo}"')

    def test_agent_in_subdirectory_blocks_push(self):
        before, _ = self.make_push_remote()
        self.push_shell('NOW=1000; push_test')
        agents = self.agents(dict(cwd=str(self.repo / "deep/dir")))
        self.push_shell(f'load_agents() {{ AGENTS_JSON={agents}; }}; NOW=1020; push_test')
        self.assertEqual(self.remote_head(), before)

    def test_owner_is_longest_worktree_and_sibling_prefix_is_not_owner(self):
        r = str(self.repo)
        wts = shlex.quote(f"{r}\n{r}/nested")
        agents = self.agents(dict(cwd=f"{r}/nested/x", name="inner"), dict(cwd=f"{r}-other", name="sibling"))
        out = self.shell(f'AGENTS_JSON={agents}; echo "root=[$(busy_agent_in {shlex.quote(r)} {wts})]"; '
                         f'echo "nested=[$(busy_agent_in {shlex.quote(r + "/nested")} {wts})]"')
        self.assertIn("root=[]", out)
        self.assertIn(f"nested=[inner:working@{r}/nested/x]", out)

    def test_busy_agent_without_usable_location_or_unparsable_list_blocks_commit(self):
        (self.repo / "a").write_text("changed")
        for agents in (self.agents(dict(cwd=None)), self.agents(dict(cwd=17)), self.agents(dict(cwd="relative/path")),
                       shlex.quote("not json"), shlex.quote('{"cwd": 1}')):
            with self.subTest(agents=agents):
                self.shell(f'SETTLE=0; CHECKPOINT_AFTER=0; note() {{ :; }}; load_agents() {{ AGENTS_JSON={agents}; }}; '
                           'run_hoc() { echo unexpected; exit 9; }; '
                           f'commit_stage "{self.repo}" "*" "{self.repo}"')

    def test_idle_agents_in_worktree_allow_commit(self):
        (self.repo / "a").write_text("changed")
        agents = self.agents(dict(cwd=str(self.repo), st="idle"), dict(cwd=str(self.repo / "x"), st="done"),
                             dict(cwd=None, st="idle"))
        out = self.shell(f'NOW=1000; SETTLE=0; CHECKPOINT_AFTER=0; load_agents() {{ AGENTS_JSON={agents}; }}; '
                         'run_hoc() { echo CALLED; return 2; }; '
                         f'commit_stage "{self.repo}" "*" "{self.repo}"')
        self.assertIn("CALLED", out)

    def test_herdr_agent_list_shape_is_validated_at_load(self):
        for body in ('{"result":{}}', '{"result":{"agents":"x"}}', '{"result":{"agents":[1]}}', 'garbage', ''):
            with self.subTest(body=body):
                self.shell(f'herdr() {{ printf "%s" {shlex.quote(body)}; }}; load_agents && exit 1; test "$AGENTS_OK" = 0')
        out = self.shell('herdr() { echo \'{"result":{"agents":[{"cwd":5,"agent_status":"working","name":"w"}]}}\'; }; '
                         'load_agents || exit 1; echo "$AGENTS_JSON"')
        self.assertEqual(json.loads(out)[0]["cwd"], None)

    def test_workspace_lookup_failure_does_not_open_checkpoint_tab(self):
        out = self.shell_rc('herdr() { printf "%s\\n" "$*" >> "$STATE/calls"; }; '
                            'load_agents() { AGENTS_JSON="not json"; AGENTS_OK=1; }; HERDR_WORKSPACE_ID=wFallback; '
                            f'h=$(git -C {shlex.quote(str(self.repo))} rev-parse HEAD); '
                            f'checkpoint_dispatch {shlex.quote(str(self.repo))} main "$h" 3 {shlex.quote(str(self.repo))}; '
                            'echo "rc=$?"; cat "$STATE/calls" 2>/dev/null; true')
        self.assertIn("rc=1", out.stdout)
        self.assertNotIn("tab create", out.stdout)

    # ---- B. 전송 이력과 원격 목적지 ----
    def commits(self, *steps):
        """steps: ({path: bytes|None}, ...) 를 차례로 커밋한다. None은 삭제. (시작 HEAD, 끝 HEAD)"""
        before = self.git("rev-parse", "HEAD")
        for step in steps:
            for name, content in step.items():
                path = self.repo / name
                if content is None:
                    self.git("rm", "-q", "--", name)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "step")
        return before, self.git("rev-parse", "HEAD")

    def test_unsafe_files_checks_every_published_commit_not_only_endpoints(self):
        mib = 1024 * 1024
        cases = (
            ("added then deleted", [{"config/.env": b"TOKEN=x\n"}, {"config/.env": None}], ["forbidden: config/.env"]),
            ("large then shrunk", [{"big.bin": b"x" * (2 * mib)}, {"big.bin": b"x"}], ["too large (2MB): big.bin"]),
            ("unicode and spaces", [{"공백 폴더/credentials.json": b"{}"}, {"공백 폴더/credentials.json": None}],
             ["forbidden: 공백 폴더/credentials.json"]),
            ("newline in name", [{"big\nfile.bin": b"x" * (2 * mib)}, {"big\nfile.bin": None}],
             ["too large (2MB): big\\nfile.bin"]),
        )
        for name, steps, expected in cases:
            with self.subTest(name=name):
                frm, to = self.commits(*steps)
                self.assertEqual(sorted(self.unsafe(frm, to).splitlines()), expected)

    def test_unsafe_files_catches_rename_and_type_change_to_forbidden_path(self):
        self.commits({"notes.txt": b"secret\n"})
        frm, to = self.commits({"notes.txt": None, "deploy/server.key": b"secret\n"})
        self.assertEqual(self.unsafe(frm, to).splitlines(), ["forbidden: deploy/server.key"])
        (self.repo / ".netrc").symlink_to("a")
        self.git("add", ".netrc")
        self.git("commit", "-q", "-m", "link")
        frm = self.git("rev-parse", "HEAD")
        (self.repo / ".netrc").unlink()
        (self.repo / ".netrc").write_text("machine x password y\n")
        self.git("add", ".netrc")
        self.git("commit", "-q", "-m", "typechange")
        self.assertEqual(self.unsafe(frm, self.git("rev-parse", "HEAD")).splitlines(), ["forbidden: .netrc"])

    def test_unsafe_files_covers_side_branch_history_and_evil_merge(self):
        frm = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "side")
        self.commits({"id_rsa": b"key\n"}, {"id_rsa": None, "side.txt": b"ok\n"})
        self.git("checkout", "-q", "main")
        self.commits({"main.txt": b"ok\n"})
        self.git("merge", "-q", "--no-ff", "-m", "merge", "side")
        self.assertEqual(self.unsafe(frm, self.git("rev-parse", "HEAD")).splitlines(), ["forbidden: id_rsa"])
        frm = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "side2")
        self.commits({"side2.txt": b"ok\n"})
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "--no-commit", "side2")
        (self.repo / "evil.pem").write_text("pem\n")
        self.git("add", "evil.pem")
        self.git("commit", "-q", "-m", "evil merge")
        self.assertEqual(self.unsafe(frm, self.git("rev-parse", "HEAD")).splitlines(), ["forbidden: evil.pem"])

    def test_unsafe_files_passes_safe_history(self):
        frm, to = self.commits({"src/a.txt": b"a\n"}, {"src/a.txt": b"b\n", "docs/b.md": b"c\n"}, {"docs/b.md": None})
        self.assertEqual(self.unsafe(frm, to), "")

    def test_multiple_push_destinations_hold_push_and_update_no_remote(self):
        for option in ("--push", None):
            with self.subTest(option=option):
                self.tearDown_repo()
                before, _ = self.make_push_remote()
                second = self.base / "second.git"
                subprocess.run(["git", "clone", "--bare", "-q", str(self.base / "remote.git"), str(second)], check=True, env=self.env)
                if option:
                    self.git("remote", "set-url", "--add", "--push", "origin", str(self.base / "remote.git"))
                    self.git("remote", "set-url", "--add", "--push", "origin", str(second))
                else:
                    self.git("remote", "set-url", "--add", "origin", str(second))
                notes = self.push_shell('note() { echo "NOTE $*"; }; NOW=1000; push_test; NOW=1020; push_test')
                self.assertEqual(self.remote_head(), before)
                second_head = subprocess.check_output(["git", "--git-dir", str(second), "rev-parse", "refs/heads/main"],
                                                      text=True, env=self.env).strip()
                self.assertEqual(second_head, before)
                self.assertIn("push 목적지", notes)

    def tearDown_repo(self):
        """같은 테스트 안에서 저장소·원격을 처음 상태로 다시 만든다."""
        import shutil
        for name in ("repo", "remote.git", "second.git", "state"):
            shutil.rmtree(self.base / name, ignore_errors=True)
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "a").write_text("initial")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")

    # ---- autocommit-agent: 백엔드별 권한 인자 ----
    def run_agent(self, agent, **env):
        """가짜 CLI가 받은 인자·환경을 기록한다. 반환: (결과, 인자 목록)"""
        argv = self.base / f"{agent}-argv"
        fake = self.base / "agent-bin"
        fake.mkdir(exist_ok=True)
        run_env = dict(self.env, PATH=f"{fake}{os.pathsep}{self.env['PATH']}", **env)
        if agent:
            (fake / agent).write_text('#!/bin/sh\nprintf "%s\\n" "$@" "PROJECT_DISABLE=$OPENCODE_CONFIG_PROJECT_DISABLE" '
                                      f'"CONFIG=$OPENCODE_CONFIG_CONTENT" > {shlex.quote(str(argv))}\nexit 3\n')
            (fake / agent).chmod(0o755)
            run_env["AC_AGENT"] = agent
        result = subprocess.run([str(SOURCE.parent / "autocommit-agent"), "-c", "추가"], cwd=self.repo,
                                env=run_env, text=True, capture_output=True)
        return result, (argv.read_text().splitlines() if argv.exists() else None)

    def test_loop_modes_require_agent_before_starting(self):
        env = dict(self.env, HERDR_ENV="1")
        for mode in ("once", "loop", "up"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(SOURCE), mode], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("AC_AGENT", result.stderr)
        result = subprocess.run([str(SOURCE), "once"], env=dict(env, AC_AGENT="codex"), capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("AC_AGENT_UNRESTRICTED=1", result.stderr)

    def test_default_committer_resolves_through_install_symlink(self):
        link_dir = self.base / "local-bin"
        link_dir.mkdir()
        (link_dir / "autocommit").symlink_to(SOURCE)
        out = subprocess.run(["bash", "-c", 'source <(sed -n "/^HOC=/p" "$1"); echo "$HOC"', str(link_dir / "autocommit"),
                              str(link_dir / "autocommit")], env=self.env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, str(SOURCE.parent / "autocommit-agent"))

    def test_agent_requires_known_backend(self):
        self.assertEqual(self.run_agent("")[0].returncode, 2)
        self.assertEqual(self.run_agent("gemini")[0].returncode, 2)

    def test_agent_codex_needs_unrestricted_consent(self):
        result, argv = self.run_agent("codex")
        self.assertEqual((result.returncode, argv), (2, None))
        result, argv = self.run_agent("codex", AC_AGENT_UNRESTRICTED="1")
        self.assertEqual(result.returncode, 3)
        self.assertIn("danger-full-access", argv)

    def test_agent_claude_denies_push_and_propagates_rc(self):
        result, argv = self.run_agent("claude")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        denied = argv[argv.index("--disallowedTools") + 1:argv.index("--")]
        self.assertIn("Bash(git push *)", denied)

    def test_agent_opencode_ignores_project_config_and_denies_push(self):
        result, argv = self.run_agent("opencode", AC_AGENT_MODEL="p/m")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(argv[argv.index("--model") + 1], "p/m")
        self.assertIn("--standalone", argv)
        self.assertIn("PROJECT_DISABLE=1", argv)
        rules = json.loads(next(a for a in argv if a.startswith("CONFIG="))[7:])["permissions"]
        self.assertEqual(rules[0], {"action": "*", "resource": "*", "effect": "deny"})
        self.assertEqual([r["effect"] for r in rules if r["resource"] == "git push *"], ["deny"])

    def test_agent_agy_denies_writes_and_push(self):
        result, argv = self.run_agent("agy")
        self.assertEqual(result.returncode, 3)
        deny = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"]
        self.assertIn("write_file(*)", deny)
        self.assertIn("command(git push)", deny)

    # ---- C. 보류 상태와 중단 복구 ----
    def fake_hoc(self, script):
        fake = self.base / "hoc-fake"
        fake.write_text("#!/usr/bin/env bash\n" + script)
        fake.chmod(0o755)
        self.env["AC_HOC"] = str(fake)

    def inflight(self):
        return self.base / "state" / ("inflight-" + self.hash(str(self.repo)))

    def nopush(self, branch):
        return self.base / "state" / ("nopush-" + self.hash(f"{self.repo}:{branch}"))

    def run_hoc_rc(self):
        r = shlex.quote(str(self.repo))
        out = self.shell(f'note() {{ :; }}; run_hoc {r} main {r}; echo "rc=$?"')
        return out.strip().splitlines()[-1]

    def test_inflight_record_write_failure_never_starts_hoc(self):
        self.fake_hoc(f'touch {shlex.quote(str(self.base / "hoc-ran"))}\n')
        (self.repo / "a").write_text("changed")
        r = shlex.quote(str(self.repo))
        out = self.shell('note() { :; }; mktemp() { ln -s /dev/full "$STATE/full-write"; echo "$STATE/full-write"; }; '
                         f'run_hoc {r} main {r}; echo "rc=$?"')
        self.assertIn("rc=1", out)
        self.assertFalse((self.base / "hoc-ran").exists())

    def test_interrupted_hoc_keeps_inflight_record_and_holds_repo(self):
        for name, script in (("before commit", 'kill -KILL "$TEST_PID"\n'),
                             ("after head moved", 'git add a; git commit -qm wip; kill -KILL "$TEST_PID"\n')):
            with self.subTest(name=name):
                self.fake_hoc(script)
                (self.repo / "a").write_text("changed " + name)
                pre = self.git("rev-parse", "HEAD")
                r = shlex.quote(str(self.repo))
                self.shell_rc(f'export TEST_PID=$$; run_hoc {r} main {r}')
                record = self.inflight().read_text()
                self.assertIn(f"head={pre}", record)
                self.assertIn("branch=main", record)
                self.assertIn("run=", record)
                (self.repo / "a").write_text("more " + name)
                self.shell('SETTLE=0; CHECKPOINT_AFTER=0; note() { :; }; load_agents() { AGENTS_JSON="[]"; }; '
                           'run_hoc() { echo unexpected; exit 9; }; '
                           f'commit_stage {r} "*" {r}; MODE=plan; commit_stage {r} "*" {r}')
                self.assertTrue(self.inflight().exists())                    # 재시작·plan이 지우지 않는다
                self.inflight().unlink()
                self.git("checkout", "-q", "--", "a")

    def test_inflight_record_holds_push(self):
        before, _ = self.make_push_remote()
        self.inflight().parent.mkdir(exist_ok=True)
        self.inflight().write_text("run=x\n")
        self.push_shell('NOW=1000; push_test; NOW=1020; push_test; MODE=push-only; NOW=1030; push_test')
        self.assertEqual(self.remote_head(), before)

    def test_inflight_record_blocks_even_when_notification_fails(self):
        # 차단 여부가 알림(note) 성공에 의존하면 안 된다.
        before, after = self.make_push_remote()
        self.inflight().parent.mkdir(exist_ok=True)
        self.inflight().write_text("run=x\n")
        r = shlex.quote(str(self.repo))
        (self.repo / "a").write_text("changed again")
        self.shell('SETTLE=0; CHECKPOINT_AFTER=0; note() { return 1; }; load_agents() { AGENTS_JSON="[]"; }; '
                   'run_hoc() { echo unexpected; exit 9; }; '
                   f'commit_stage {r} "*" {r}')
        self.git("checkout", "-q", "--", "a")                                 # push는 깨끗한 작업 트리에서 본다 — 미커밋 변경이 차단을 가리지 않게
        self.assertEqual(self.git("status", "--porcelain"), "")
        for mode in ("loop", "push-only"):
            with self.subTest(mode=mode):
                self.push_shell(f'note() {{ return 1; }}; MODE={mode}; NOW=1000; push_test; NOW=1020; push_test')
                self.assertEqual(self.remote_head(), before)
        self.inflight().unlink()                                              # 대조: 기록이 없으면 같은 조건에서 push가 진행된다
        self.push_shell('note() { return 1; }; MODE=push-only; NOW=2000; push_test; NOW=2020; push_test')
        self.assertEqual(self.remote_head(), after)

    def test_branch_switch_marks_both_branches_and_clears_inflight(self):
        self.fake_hoc('git checkout -q -b feature; git add a; git commit -qm x\n')
        (self.repo / "a").write_text("changed")
        self.assertEqual(self.run_hoc_rc(), "rc=1")
        self.assertTrue(self.nopush("main").exists())
        self.assertTrue(self.nopush("feature").exists())
        self.assertFalse(self.inflight().exists())

    def test_detached_or_unreadable_head_after_hoc_keeps_inflight(self):
        for name, script in (("detached", "git checkout -q --detach\n"),
                             ("head lookup fails", "mv .git .git-away\n")):
            with self.subTest(name=name):
                self.fake_hoc(script)
                (self.repo / "a").write_text("changed")
                self.assertEqual(self.run_hoc_rc(), "rc=1")
                self.assertTrue(self.inflight().exists())
                if (self.repo / ".git-away").exists():
                    (self.repo / ".git-away").rename(self.repo / ".git")
                self.git("checkout", "-q", "main")
                self.inflight().unlink()

    def test_violation_marker_write_failure_keeps_inflight(self):
        state = shlex.quote(str(self.base / "state"))
        self.fake_hoc(f'git checkout -q -b feature; chmod 500 {state}\n')
        (self.repo / "a").write_text("changed")
        try:
            self.assertEqual(self.run_hoc_rc(), "rc=1")
        finally:
            (self.base / "state").chmod(0o700)
        self.assertTrue(self.inflight().exists())

    def test_verified_outcomes_clear_inflight_and_keep_existing_nopush(self):
        self.shell(f'echo "earlier violation" > "$STATE/nopush-$(hash "{self.repo}:main")"')
        for name, script, rc in (("no change", "exit 0\n", "rc=2"),
                                 ("failed without change", "exit 1\n", "rc=1"),
                                 ("committed", "git add a; git commit -qm 'chore: x'\n", "rc=0")):
            with self.subTest(name=name):
                self.fake_hoc(script)
                (self.repo / "a").write_text("changed " + name)
                self.assertEqual(self.run_hoc_rc(), rc)
                self.assertFalse(self.inflight().exists())
                self.assertEqual(self.nopush("main").read_text(), "earlier violation\n")

    def test_failed_run_with_unchanged_head_retries_after_cooldown(self):
        self.fake_hoc(f'echo x >> {shlex.quote(str(self.base / "hoc-runs"))}; exit 1\n')
        (self.repo / "a").write_text("changed")
        r = shlex.quote(str(self.repo))
        body = ('SETTLE=0; CHECKPOINT_AFTER=0; FAIL_COOLDOWN=100; note() { :; }; load_agents() { AGENTS_JSON="[]"; }; '
                f'NOW=1000; commit_stage {r} "*" {r}; NOW=1050; commit_stage {r} "*" {r}; '
                f'NOW=1200; commit_stage {r} "*" {r}')
        self.shell('date() { if [ "$1" = +%s ]; then echo "$NOW"; else command date "$@"; fi; }; ' + body)
        self.assertEqual(len((self.base / "hoc-runs").read_text().splitlines()), 2)

    def test_tidy_keeps_nopush_when_push_is_only_inactive_or_remote_unverified(self):
        before, after = self.make_push_remote()
        mark = f'"$STATE/nopush-$(hash "{self.repo}:main")"'
        kept = f'tidy; test -f {mark} && echo KEPT || echo GONE'
        self.shell(f'echo violation > {mark}')
        for conf in (f"{self.repo} * -\n", f"{self.repo} * origin other\n"):
            with self.subTest(conf=conf):
                self.conf.write_text(conf)
                self.assertIn("KEPT", self.shell(kept))
        self.conf.write_text(f"{self.repo} * origin\n")
        self.git("branch", "--unset-upstream")
        self.assertIn("KEPT", self.shell(kept))
        self.git("branch", "-u", "origin/main")
        self.push_shell('NOW=1000; push_test; NOW=1020; push_test')         # 다시 켜도 마커가 push를 막는다
        self.assertEqual(self.remote_head(), before)
        self.git("push", "-q", "origin", "main")                              # 원격이 포함하지만 목적지가 둘이면 검증 실패
        second = self.base / "second.git"
        subprocess.run(["git", "init", "--bare", "-q", str(second)], check=True, env=self.env)
        self.git("remote", "set-url", "--add", "--push", "origin", str(self.base / "remote.git"))
        self.git("remote", "set-url", "--add", "--push", "origin", str(second))
        self.assertIn("KEPT", self.shell(kept))
        self.git("config", "--unset-all", "remote.origin.pushurl")
        self.assertIn("GONE", self.shell(kept))

    def run_unblock(self, *args):
        return subprocess.run([str(SOURCE), "unblock", *args], env=self.env, capture_output=True, text=True)

    def test_unblock_resolves_configured_alias_path(self):
        link = self.base / "link"
        link.symlink_to(self.repo)
        self.conf.write_text(f"{link} * -\n")
        state = self.base / "state"
        state.mkdir()
        for arg in (str(self.repo), str(link), str(link) + "/"):
            with self.subTest(arg=arg):
                marker = state / ("nopush-" + self.hash(f"{link}:main"))
                marker.write_text("violation\n")
                result = self.run_unblock(arg, "main")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(marker.exists())

    def test_unblock_refuses_ambiguous_aliases_and_unconfigured_paths(self):
        link = self.base / "link"
        link.symlink_to(self.repo)
        self.conf.write_text(f"{self.repo} * -\n{link} * -\n")
        state = self.base / "state"
        state.mkdir()
        markers = [state / ("nopush-" + self.hash(f"{root}:main")) for root in (self.repo, link)]
        for m in markers:
            m.write_text("violation\n")
        self.assertNotEqual(self.run_unblock(str(self.repo), "main").returncode, 0)
        self.assertTrue(all(m.exists() for m in markers))
        self.assertNotEqual(self.run_unblock(str(self.base / "elsewhere"), "main").returncode, 0)

    def test_unblock_coordinates_with_inflight_record_and_loop_lock(self):
        import fcntl
        state = self.base / "state"
        state.mkdir()
        self.inflight().write_text("run=x\n")
        for branch in ("main", "feature"):
            self.nopush(branch).write_text("violation\n")
        result = self.run_unblock(str(self.repo), "main")                     # hoc 기록이 남아 있으면 브랜치 해제 거절
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.nopush("main").exists())
        fd = os.open(state / "lock", os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = self.run_unblock(str(self.repo))                         # loop·hoc가 잠금을 쥐면 거절
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(self.inflight().exists())
        finally:
            os.close(fd)
        result = self.run_unblock(str(self.repo))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.inflight().exists())
        self.assertTrue(self.nopush("main").exists())                         # 저장소 기록 해제는 브랜치 마커를 건드리지 않는다
        result = self.run_unblock(str(self.repo), "main")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.nopush("main").exists())
        self.assertTrue(self.nopush("feature").exists())                      # 다른 브랜치는 그대로

    def test_unblock_branch_waits_for_loop_lock_and_reports_delete_failure(self):
        import fcntl
        state = self.base / "state"
        state.mkdir()
        self.nopush("main").write_text("violation\n")
        fd = os.open(state / "lock", os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = self.run_unblock(str(self.repo), "main")                 # 실행 기록이 없어도 loop 잠금 중에는 거절
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(self.nopush("main").exists())
        finally:
            os.close(fd)
        # 실행기는 시작할 때 $STATE 권한을 되돌리므로, 삭제 실패는 PATH의 rm으로 만든다.
        shim = self.base / "rm-shim"
        shim.mkdir()
        (shim / "rm").write_text("#!/bin/sh\necho 'rm: cannot remove' >&2\nexit 1\n")
        (shim / "rm").chmod(0o755)
        for args, target in (((str(self.repo),), self.inflight()),
                             ((str(self.repo), "main"), self.nopush("main"))):
            with self.subTest(args=args):
                target.write_text("record\n")
                saved = self.env
                self.env = dict(saved, PATH=f"{shim}{os.pathsep}{saved['PATH']}")
                try:
                    result = self.run_unblock(*args)
                finally:
                    self.env = saved
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("해제됨", result.stdout)
                self.assertTrue(target.exists())
                if target == self.inflight():
                    target.unlink()                                           # 브랜치 해제는 실행 기록이 없어야 진행된다

    def test_audit_lists_inflight_record_with_recovery_command(self):
        state = self.base / "state"
        state.mkdir()
        self.inflight().write_text(f"run=r1\nroot={self.repo}\nbranch=main\nhead=abc\n")
        out = self.shell('audit')
        self.assertIn("[미해결 hoc 실행 기록]", out)
        self.assertIn("run=r1", out)
        self.assertIn(f"autocommit unblock {self.repo}", out)

    # ---- D. 운영 설명 ----
    def test_up_passes_environment_values_verbatim_without_logging_them(self):
        shim = self.base / "up-shim"
        shim.mkdir()
        record = self.base / "pane-command"
        (shim / "herdr").write_text(
            '#!/bin/bash\ncase "$1 $2" in\n'
            '  "agent list") echo \'{"result":{"agents":[]}}\' ;;\n'
            '  "tab create") echo \'{"result":{"tab":{"tab_id":"w:t1"},"root_pane":{"pane_id":"w:p1"}}}\' ;;\n'
            f'  "pane run") printf "%s" "$4" > {shlex.quote(str(record))} ;;\n'
            'esac\n')
        dump = self.base / "dump"
        (shim / "autocommit").write_text(f'#!/bin/bash\nprintf "%s" "$AC_WEIRD" > {shlex.quote(str(dump))}\n')
        for f in shim.iterdir():
            f.chmod(0o755)
        weird = 'a  b "c" \'d\' $(touch PWNED) `touch PWNED2`; echo injected\nsecond line \\ end'
        env = dict(self.env, PATH=f"{shim}{os.pathsep}{self.env['PATH']}", HERDR_ENV="1", AC_WEIRD=weird, AC_AGENT="claude")
        result = subprocess.run([str(SOURCE), "up"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AC_WEIRD", result.stdout)
        self.assertNotIn("touch PWNED", result.stdout)
        work = self.base / "run-here"
        work.mkdir()
        subprocess.run(["bash", "-c", record.read_text()], env=dict(self.env, PATH=env["PATH"]), cwd=work, check=True)
        self.assertEqual(dump.read_text(), weird)
        self.assertEqual(list(work.iterdir()), [])

    def test_audit_reminders_alone_do_not_raise_alert(self):
        (self.base / "audit-remind").write_text("주간 백업 확인\n원격 권한 확인\n")
        out = self.shell('NOW=100000; AUDIT_INTERVAL=10; tidy() { :; }; note() { echo "NOTE $*"; }; audit_tick 2>&1')
        self.assertNotIn("NOTE", out)
        self.assertIn("주간 백업 확인", out)
        self.shell('mkdir -p "$STATE"; echo x > "$STATE/fail-aaaa"; touch -d "5 days ago" "$STATE/fail-aaaa"')
        out = self.shell('NOW=$(date +%s); AUDIT_INTERVAL=10; rm -f "$STATE/last-audit"; tidy() { :; }; '
                         'note() { echo "NOTE $*"; }; audit_tick 2>&1')
        self.assertIn("항목 1건", out)

    def test_observation_status_does_not_rewrite_index(self):
        # 관측용 git status만 검증한다. fingerprint의 git diff HEAD는 git 2.34에서 --no-optional-locks여도
        # index를 다시 쓴다 — 지문 형식을 바꾸는 별도 결정이 필요해 이번 범위 밖으로 남긴다(설계문서 E).
        index = self.repo / ".git/index"
        r = shlex.quote(str(self.repo))
        os.utime(self.repo / "a", (1, 1))                                    # 내용은 같고 stat만 달라 status가 index를 고쳐 쓰려 한다
        snapshot = index.read_bytes()
        self.shell(f'load_agents() {{ AGENTS_JSON={self.agents(dict(cwd=str(self.repo)))}; }}; '
                   f'CHECKPOINT_AFTER=0; commit_stage {r} "*" {r}; MODE=loop; POLL_SIGNATURE=x; repo_schedule {r} {r}')
        self.assertEqual(index.read_bytes(), snapshot)

    def hash(self, value):
        import hashlib
        return hashlib.md5(value.encode()).hexdigest()[:16]

if __name__ == "__main__":
    unittest.main()
