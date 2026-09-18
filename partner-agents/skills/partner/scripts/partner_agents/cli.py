"""The argument parser: every subcommand, its flags, and the function it runs."""
from __future__ import annotations

import argparse

from .commands.discovery import (
    cmd_check, cmd_models, cmd_probe, cmd_providers, cmd_terminals,
)
from .commands.hooks import cmd_hook_prompt, cmd_hook_stop
from .commands.messaging import (
    cmd_baton, cmd_claim, cmd_kickoff, cmd_nudge, cmd_pending, cmd_read, cmd_send,
    cmd_wait,
)
from .commands.session import (
    cmd_archive, cmd_init, cmd_resume, cmd_sessions, cmd_snapshot, cmd_spawn, cmd_stop,
)
from .commands.status import cmd_idle, cmd_list, cmd_state
from .providers.recipes import AUTO_LEVELS
from .timing import IDLE_PAUSE, LIVE_WINDOW, WAIT_POLL, WAIT_TIMEOUT


def main() -> int:
    ap = argparse.ArgumentParser(prog="partner.py",
                                 description="Equal AI partners on one repo.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="register yourself as a partner")
    i.add_argument("--me-name", default=None, help="your own id (default p1)")
    i.add_argument("--me-provider", default=None, help="the CLI you are running in")
    i.add_argument("--me-model", default=None, help="your own model id")
    i.add_argument("--me-effort", default=None,
                   choices=["low", "medium", "high", "max"])
    i.add_argument("--me-auto", default=None, choices=list(AUTO_LEVELS))
    i.add_argument("--context", default=None, help="handoff text, or path to a file")
    i.set_defaults(fn=cmd_init)

    pv = sub.add_parser("providers", help="agent CLIs found on this machine")
    pv.add_argument("--deep", action="store_true",
                    help="also probe unrecognised binaries in the user bin "
                         "dirs and keep whatever behaves like an agent CLI")
    pv.set_defaults(fn=cmd_providers)

    md = sub.add_parser("models", help="models found on this machine, and the "
                                       "effort that suits each")
    md.add_argument("--provider", default=None,
                    help="just this one; any CLI name, not only a known one")
    md.add_argument("--no-probe", action="store_true",
                    help="do not run or read the CLI at all")
    md.add_argument("--deep", action="store_true",
                    help="always scan the CLI's own bundle, not only when the "
                         "cheap sources came back thin")
    md.set_defaults(fn=cmd_models)

    pb = sub.add_parser("probe", help="what a CLI's --help says about driving it")
    pb.add_argument("--provider", required=True, help="CLI name or path")
    pb.add_argument("--refresh", action="store_true", help="ignore the cache")
    pb.set_defaults(fn=cmd_probe)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("snapshot").set_defaults(fn=cmd_snapshot)

    sp = sub.add_parser("spawn", help="start an agent in a new terminal tab")
    sp.add_argument("--provider", required=True)
    sp.add_argument("--model", default=None)
    sp.add_argument("--effort", default=None, choices=["low", "medium", "high", "max"])
    sp.add_argument("--auto", default="edits", choices=list(AUTO_LEVELS),
                    help="permission prompting: ask | edits (default) | full")
    sp.add_argument("--name", default=None, help="agent id (default p2, p3, ...)")
    sp.add_argument("--cmd", default=None, help="command template for --provider custom")
    sp.add_argument("--context", default=None, help="handoff text, or path to a file")
    sp.add_argument("--no-tab", action="store_true")
    sp.add_argument("--terminal", default=None,
                    help="open the tab in this terminal (see `terminals`); "
                         "default is whatever detection picks")
    sp.add_argument("--replace", action="store_true")
    sp.add_argument("--force", action="store_true",
                    help="use a model the known list has not caught up with")
    sp.add_argument("--me-provider", default=None,
                    help="the CLI you yourself are running in")
    sp.add_argument("--me-model", default=None, help="your own model id")
    sp.add_argument("--me-effort", default=None,
                    choices=["low", "medium", "high", "max"])
    sp.add_argument("--me-auto", default=None, choices=list(AUTO_LEVELS))
    sp.add_argument("--fresh", action="store_true",
                    help="archive the current session and start a new one")
    sp.set_defaults(fn=cmd_spawn)

    ar = sub.add_parser("archive", help="file the current session away")
    ar.add_argument("--label", default=None, help="a name you will recognise later")
    ar.set_defaults(fn=cmd_archive)

    sub.add_parser("sessions", help="list the current and archived sessions"
                   ).set_defaults(fn=cmd_sessions)

    stt = sub.add_parser("state", help="who is actually alive, and what to do next")
    stt.add_argument("--window", type=int, default=LIVE_WINDOW,
                     help="seconds since an agent last acted before it counts as stale")
    stt.set_defaults(fn=cmd_state)

    rs = sub.add_parser("resume", help="restart the agents of a session")
    rs.add_argument("--session", default=None,
                    help="archived session id; omit to continue the current one")
    rs.add_argument("--no-tab", action="store_true")
    rs.add_argument("--terminal", default=None, help="see `terminals`")
    rs.set_defaults(fn=cmd_resume)

    ck = sub.add_parser("check", help="validate a config without starting anything")
    ck.add_argument("--provider", required=True)
    ck.add_argument("--model", default=None)
    ck.add_argument("--effort", default=None)
    ck.add_argument("--cmd", default=None)
    ck.add_argument("--force", action="store_true")
    ck.set_defaults(fn=cmd_check)

    w = sub.add_parser("wait", help="block until somebody addresses you")
    w.add_argument("--for", dest="who", default=None,
                   help="override identity (normally set by your wrapper)")
    w.add_argument("--timeout", type=int, default=WAIT_TIMEOUT)
    w.add_argument("--poll", type=float, default=WAIT_POLL)
    w.set_defaults(fn=cmd_wait)

    s = sub.add_parser("send")
    s.add_argument("--from", dest="sender", default=None,
                   help="sender id (default: whoever is running this)")
    s.add_argument("--to", default="@all")
    s.add_argument("--text", default=None)
    s.add_argument("--file", default=None)
    s.add_argument("--wait", type=int, default=0, help="block N seconds for replies")
    s.add_argument("--expect", type=int, default=0, help="how many repliers to wait for")
    s.add_argument("--no-nudge", action="store_true",
                   help="do not wake recipients that stopped listening")
    s.set_defaults(fn=cmd_send)

    ko = sub.add_parser("kickoff", help="open the session: the human's first "
                                        "instruction, or an orientation pass")
    ko.add_argument("--from", dest="sender", default=None)
    ko.add_argument("--text", default=None,
                    help="what the human wants worked on; omit for orientation")
    ko.add_argument("--file", default=None, help="read the instruction from a file")
    ko.add_argument("--wait", type=int, default=0, help="block N seconds for replies")
    ko.add_argument("--expect", type=int, default=0)
    ko.set_defaults(fn=cmd_kickoff)

    ng = sub.add_parser("nudge", help="wake agents that stopped looping by "
                                      "typing into their tabs")
    ng.add_argument("--id", default=None, help="one agent; omit for everyone silent")
    ng.add_argument("--all", action="store_true", help="every agent but you")
    ng.add_argument("--text", default=None, help="why they are being woken")
    ng.set_defaults(fn=cmd_nudge)

    idl = sub.add_parser("idle", help="whether every agent has finished, and "
                                      "when the loops pause")
    idl.add_argument("--after", type=int, default=None,
                     help=f"seconds every agent must be finished before the "
                          f"loops pause (default {IDLE_PAUSE}; 0 turns it off)")
    idl.set_defaults(fn=cmd_idle)

    r = sub.add_parser("read")
    r.add_argument("--for", dest="who", default=None,
                   help="reader id (default: whoever is running this)")
    r.add_argument("--peek", action="store_true", help="do not advance the cursor")
    r.set_defaults(fn=cmd_read)

    b = sub.add_parser("baton")
    b.add_argument("--to", default=None)
    b.set_defaults(fn=cmd_baton)

    cl = sub.add_parser("claim", help="take the baton: the human just told YOU to act")
    cl.add_argument("--for", dest="who", default=None)
    cl.add_argument("--force", action="store_true",
                    help="claim within the launch grace period anyway -- the "
                         "human really did just type an instruction")
    cl.set_defaults(fn=cmd_claim)

    st = sub.add_parser("stop")
    st.add_argument("--id", default=None)
    st.add_argument("--all", action="store_true")
    st.set_defaults(fn=cmd_stop)

    tm = sub.add_parser("terminals", help="what can open a partner's tab here")
    tm.add_argument("--prefer", default=None,
                    help="rank this entry first, as --terminal would")
    tm.add_argument("--no-probe", action="store_true",
                    help="do not probe the terminal hosting this session")
    tm.set_defaults(fn=cmd_terminals)

    pd = sub.add_parser("pending", help="messages you still owe a reply to")
    pd.add_argument("--for", dest="who", default=None)
    pd.set_defaults(fn=cmd_pending)

    sub.add_parser("hook-stop", help="internal: Stop-hook backstop"
                   ).set_defaults(fn=cmd_hook_stop)
    sub.add_parser("hook-prompt", help="internal: UserPromptSubmit hook"
                   ).set_defaults(fn=cmd_hook_prompt)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
