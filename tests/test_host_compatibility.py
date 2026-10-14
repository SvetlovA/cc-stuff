"""Exercise session isolation and hook behavior without launching partners."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'partner-agents/skills/partner/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'scripts'))
from partner_agents import state
from partner_agents.briefing import build_seed
from partner_agents.commands import hooks, messaging, session
from partner_agents.providers.launch import _probed_tui, build_tui
from partner_agents.transcript import append_msg
from validate_plugins import validate


class HostCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.git').mkdir()
        self.sd = self.root / '.partner'
        self.previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.previous)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def roster(self, host, sid):
        key = 'CODEX_THREAD_ID' if host == 'codex' else 'CLAUDE_CODE_SESSION_ID'
        os.environ[key] = sid
        entry = {'id': 'p1', 'provider': host, 'kind': 'session', 'status': 'running'}
        state.bind_session(entry, host)
        roster = {'self': 'p1', 'baton': 'p1', 'partners': {
            'p1': entry, 'p2': {'id': 'p2', 'provider': 'claude',
                               'kind': 'tab', 'status': 'running'}}}
        state.save_roster(self.sd, roster)
        return roster

    def test_manifest_and_registry_consistency(self):
        self.assertEqual(validate(ROOT), (2, 4))

    def test_binding_and_unrelated_hook_isolation(self):
        for host in ('claude', 'codex'):
            with self.subTest(host=host):
                roster = self.roster(host, host + '-owner')
                payload = {'cwd': str(self.root), 'session_id': host + '-owner'}
                if host == 'codex':
                    payload['turn_id'] = 'turn-1'
                self.assertIsNotNone(hooks._hook_context(payload))
                payload['session_id'] = host + '-other'
                self.assertIsNone(hooks._hook_context(payload))
                self.assertTrue(state.is_bound_session(roster['partners']['p1']))
                self.assertFalse(state.is_bound_session(roster['partners']['p1'],
                                                       host + '-owner', 'other'))

    def test_legacy_claude_roster_still_matches(self):
        entry = {'cc_session': 'old-claude'}
        self.assertTrue(state.is_bound_session(entry, 'old-claude'))
        self.assertFalse(state.is_bound_session(entry, 'other'))
        self.assertFalse(state.is_bound_session(entry, 'old-claude', 'codex'))

    def test_tab_spawn_preserves_starting_session_binding(self):
        before = self.roster('claude', 'claude-owner')
        os.environ['PARTNER_ID'] = 'p2'
        os.environ['CODEX_THREAD_ID'] = 'codex-peer'
        args = argparse.Namespace(fresh=False, name='p3', replace=False,
                                  provider='codex', model=None, effort=None,
                                  cmd=None, force=False, quiet=True, json=True)
        with patch.object(session, 'cmd_init') as init, \
                patch.object(session, 'validate', return_value={
                    'problems': ['test stops before launch'], 'cautions': []}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(session.cmd_spawn(args), 1)
        init.assert_not_called()
        self.assertEqual(state.load_roster(self.sd), before)

    def test_unknown_session_does_not_accept_identified_hooks(self):
        entry = {'provider': 'codex'}
        state.bind_session(entry, 'codex')
        self.assertFalse(state.is_bound_session(entry, 'unrelated', 'codex'))

    def test_init_detects_codex_and_explicit_provider_wins(self):
        os.environ['CODEX_THREAD_ID'] = 'codex-owner'
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'claude-owner'
        args = argparse.Namespace(json=True, me_provider=None, me_model=None,
                                  me_effort=None, me_auto=None, me_name=None, context=None)
        with patch.object(session, 'ensure_waker'), patch.object(session, 'adopt_own_tab'), \
                contextlib.redirect_stdout(io.StringIO()):
            session.cmd_init(args)
            entry = state.load_roster(self.sd)['partners']['p1']
            self.assertEqual((entry['provider'], entry['session_id']), ('codex', 'codex-owner'))
            args.me_provider = 'claude'
            session.cmd_init(args)
            entry = state.load_roster(self.sd)['partners']['p1']
            self.assertEqual((entry['provider'], entry['session_id']), ('claude', 'claude-owner'))

    def test_hook_prompt_opens_only_owning_turn(self):
        self.roster('codex', 'owner')
        for sid, expected in [('other', False), ('owner', True)]:
            payload = {'cwd': str(self.root), 'session_id': sid,
                       'turn_id': 'turn', 'prompt': 'Keep working'}
            with patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))):
                hooks.cmd_hook_prompt(None)
            self.assertEqual((self.sd / 'p1/turn').exists(), expected)

    def test_stop_blocks_for_unanswered_message_in_both_hosts(self):
        for host in ('claude', 'codex'):
            with self.subTest(host=host):
                roster = self.roster(host, host + '-owner')
                roster['baton'] = 'p2'
                state.save_roster(self.sd, roster)
                append_msg(self.sd, 'p2', 'p1', 'Please review the change.', 'p2')
                payload = {'cwd': str(self.root), 'session_id': host + '-owner'}
                if host == 'codex':
                    payload['turn_id'] = 'turn'
                output = io.StringIO()
                with patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
                        contextlib.redirect_stdout(output):
                    hooks.cmd_hook_stop(None)
                decision = json.loads(output.getvalue())
                self.assertEqual(decision['decision'], 'block')
                self.assertIn('Please review the change.', decision['reason'])
                self.assertTrue(decision['reason'].startswith('[automated partner hook continuation'))
                (self.sd / 'p1/.stop-nag').unlink(missing_ok=True)

    def test_stop_closes_turn_when_listener_is_running(self):
        roster = self.roster('codex', 'owner')
        turn = self.sd / 'p1/turn'
        turn.parent.mkdir(parents=True, exist_ok=True)
        turn.write_text('123')
        payload = {'session_id': 'owner', 'turn_id': 'turn'}
        output = io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
                patch.object(hooks, 'listening_soon', return_value=True), \
                contextlib.redirect_stdout(output):
            hooks.cmd_hook_stop(None)
        self.assertEqual(json.loads(output.getvalue()), {})
        self.assertFalse(turn.exists())

    def test_briefings_use_host_native_wait_tools(self):
        for host in ('claude', 'codex'):
            with self.subTest(host=host):
                roster = self.roster(host, host + '-owner')
                seed = build_seed('p1', roster, self.root, self.sd,
                                  SCRIPTS / 'partner.py', False, 'session')
                if host == 'codex':
                    self.assertIn('write_stdin', seed)
                    self.assertNotIn('run_in_background', seed)
                    self.assertNotIn('inside Claude Code', seed)
                else:
                    self.assertIn('run_in_background: true', seed)
                    self.assertNotIn('write_stdin', seed)

    def test_hook_entrypoint_resolves_both_plugin_root_variables(self):
        env = dict(os.environ, PATH=os.defpath)
        for key in ('PLUGIN_ROOT', 'CLAUDE_PLUGIN_ROOT'):
            with self.subTest(key=key):
                result = subprocess.run(['bash', str(ROOT / 'partner-agents/hooks/hook.sh'),
                                         'hook-stop'], input='{}', text=True,
                                        capture_output=True,
                                        env={**env, key: str(ROOT / 'partner-agents')})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})

    def test_polled_codex_wait_declares_idle_then_reopens_for_work(self):
        self.roster('codex', 'owner')
        turn = self.sd / 'p1/turn'
        turn.parent.mkdir(parents=True, exist_ok=True)
        turn.write_text('123')
        args = argparse.Namespace(who=None, timeout=0, poll=0.001, json=True)
        with patch.object(messaging, 'ensure_waker'), \
                patch.object(messaging, 'adopt_own_tab'), \
                patch.object(messaging, 'sleeps_through_pause', return_value=False), \
                patch.object(messaging, 'idle_verdict', return_value=(False, 'test')), \
                patch.object(messaging, 'silence_notice', return_value=''), \
                contextlib.redirect_stdout(io.StringIO()):
            messaging.cmd_wait(args)
            self.assertFalse(turn.exists())
            append_msg(self.sd, 'p2', 'p1', 'New work', 'p1')
            messaging.cmd_wait(args)
            self.assertTrue(turn.exists())

    def test_claude_background_wait_preserves_open_turn(self):
        self.roster('claude', 'owner')
        turn = self.sd / 'p1/turn'
        turn.parent.mkdir(parents=True, exist_ok=True)
        turn.write_text('123')
        args = argparse.Namespace(who=None, timeout=0, poll=0.001, json=True)
        with patch.object(messaging, 'ensure_waker'), \
                patch.object(messaging, 'adopt_own_tab'), \
                patch.object(messaging, 'sleeps_through_pause', return_value=False), \
                patch.object(messaging, 'idle_verdict', return_value=(False, 'test')), \
                patch.object(messaging, 'silence_notice', return_value=''), \
                contextlib.redirect_stdout(io.StringIO()):
            messaging.cmd_wait(args)
        self.assertTrue(turn.exists())

    def test_partners_launch_in_auto_mode_by_default(self):
        cases = {'claude': ['--permission-mode', 'auto'],
                 'codex': ['--approve-for-me'],
                 'gemini': ['--approval-mode', 'yolo']}
        for provider, flags in cases.items():
            with self.subTest(provider=provider):
                argv = build_tui({'provider': provider}, 'go')
                self.assertEqual(argv[1:1 + len(flags)], flags)

    def test_probed_cli_auto_never_uses_bypass_flag(self):
        probe = {'bin': 'mycli', 'name': 'mycli', 'prompt_flag': None,
                 'auto': {'edits': ['--auto-edit'], 'full': ['--yolo']}}
        self.assertEqual(_probed_tui(probe, {'prompt': 'go', 'model': '',
                                             'effort': '', 'auto': 'auto'}),
                         ['mycli', '--auto-edit', 'go'])


if __name__ == '__main__':
    unittest.main()
