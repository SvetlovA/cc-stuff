"""Commands that start, stop, archive and resume agents and sessions."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time

from ..briefing import boot_prompt, write_briefing
from ..idle import mark_active
from ..providers.launch import build_tui
from ..providers.validate import validate
from ..sessions import (
    archive_current, read_sessions, relaunch, session_label, sessions_dir,
)
from ..state import (
    SCRIPT, baton_of, git_exclude, load_roster, me_id, repo_root, repo_snapshot,
    save_roster, state_dir, write_wrappers,
)
from ..terminals.tabs import open_tab, write_runner
from ..transcript import append_msg, set_floor, tail_msgs
from ..util import NL, emit, unlink_quietly, utcnow, write_float


def cmd_snapshot(args) -> int:
    snap = repo_snapshot(repo_root()) or "(not a git repository)"
    emit(args, {"snapshot": snap}, snap)
    return 0


def cmd_init(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    sd.mkdir(parents=True, exist_ok=True)
    chat = sd / "chat.md"
    if not chat.exists():
        chat.write_text(
            "# Partner debate transcript" + NL * 2
            + "Shared by every agent working on this repo. "
            + "Append only; never rewrite history." + NL * 2,
            encoding="utf-8")
    roster = load_roster(sd)
    me = roster.get("self") or getattr(args, "me_name", None) or "p1"
    fresh = me not in roster["partners"]
    if fresh:
        # Exactly the shape spawn produces. The agent that starts the session is
        # a participant like any other, not a stub with the others hanging off
        # it -- so it carries the same fields and gets the same briefing.
        roster["partners"][me] = {
            "id": me,
            "provider": getattr(args, "me_provider", None) or "claude",
            "model": getattr(args, "me_model", None),
            "effort": getattr(args, "me_effort", None),
            "auto": getattr(args, "me_auto", None) or "edits",
            "cmd": None, "kind": "session",
            "status": "running", "started": utcnow(),
            "tab": "this session",
        }
    roster["self"] = me
    if roster.get("baton") not in roster["partners"]:
        roster["baton"] = me
    save_roster(sd, roster)

    write_wrappers(sd, SCRIPT)            # shared, for humans
    write_wrappers(sd, SCRIPT, me)        # this agent's own
    if fresh:
        write_briefing(sd, me, roster, root, SCRIPT,
                       getattr(args, "context", None), kind="session")
        set_floor(sd, me)          # owes nothing for what was said before it
    git_exclude(root)
    emit(args, {"state_dir": str(sd), "repo_root": str(root), "self": me},
         f"initialized {sd} (you are {me}; briefing at {sd / me / 'seed.md'})")
    return 0


def cmd_spawn(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    if getattr(args, "fresh", False):
        # A new arrangement starts clean, but nothing is thrown away: the old
        # session moves under sessions/ and can be resumed with its agents.
        archived = archive_current(sd)
        if archived and not getattr(args, "quiet", False):
            print(f"archived previous session as {archived['id']} "
                  f"({len(archived['agents'])} agents, "
                  f"{archived['messages']} messages)")
    # Register the caller as part of spawning, so "a partner is running" is one
    # command rather than two. Splitting them left sessions that registered
    # themselves, stopped, and never created anybody.
    cmd_init(argparse.Namespace(
        json=False, quiet=True,
        me_provider=args.me_provider, me_model=args.me_model,
        me_effort=args.me_effort, me_auto=args.me_auto, me_name=None,
        context=None))

    roster = load_roster(sd)
    pid = args.name or f"p{len(roster['partners']) + 1}"
    if pid in roster["partners"] and not args.replace:
        emit(args, {"error": "exists"}, f"agent {pid!r} already exists; pass --replace")
        return 1

    # Validate before anything is written or a tab is opened. A bad flag caught
    # here is one message; caught at launch it is a tab that flashes an error
    # and vanishes.
    v = validate(args.provider, args.model, args.effort, args.cmd, args.force)
    if v["problems"]:
        emit(args, {"error": "invalid", **v},
             f"cannot start {pid}:" + NL
             + NL.join(f"  - {p}" for p in v["problems"]))
        return 1
    if v["cautions"] and not getattr(args, "quiet", False):
        # Said, not enforced. Each one is this script failing to recognise
        # something rather than knowing it is wrong -- which is exactly the
        # kind of thing to put in front of the user instead of acting on.
        print(f"starting {pid} anyway, but note:")
        for c in v["cautions"]:
            print(f"  - {c}")

    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    entry = {"id": pid, "provider": args.provider, "model": args.model,
             "effort": args.effort, "cmd": args.cmd, "auto": args.auto,
             "kind": "tab", "status": "running", "started": utcnow()}
    roster["partners"][pid] = entry

    # Same writer `init` uses for the agent that started the session. Briefing
    # them through different code is how peers quietly stop being peers.
    seed_f = write_briefing(sd, pid, roster, root, SCRIPT,
                            args.context, kind="tab")

    # The starting prompt only points at the seed. Keeping it short avoids
    # pushing a multi-kilobyte argument through a terminal command line.
    boot = boot_prompt(seed_f, baton_of(roster))

    argv = build_tui(entry, boot)
    runner = write_runner(pdir, argv, root)
    tab = ({} if args.no_tab
           else open_tab(f"partner:{pid}", runner, root,
                         getattr(args, "terminal", None)))
    label = tab.get("label", "")
    entry["tab"] = label
    # Kept so a silent agent can be woken later by typing into its own tab.
    entry["tab_kind"] = tab.get("kind", "")
    entry["tab_handle"] = tab.get("handle", "")
    entry["tab_title"] = tab.get("title", f"partner:{pid}")
    entry["runner"] = str(runner)
    save_roster(sd, roster)
    # A new voice is work for everyone already here. The newcomer itself is
    # skipped by the wake-up: it is still booting.
    mark_active(sd, roster, me_id(roster), f"spawned {pid}")

    # Tell the others somebody arrived. Without this a new agent is invisible
    # until it happens to speak, and the existing ones cannot address it.
    spec_bits = args.provider + (f"/{args.model}" if args.model else "")
    append_msg(sd, "system", "@all",
               f"**{pid}** ({spec_bits}) joined, started by **{me_id(roster)}**. "
               f"It has been briefed on the work so far. Address it as `{pid}`.",
               baton_of(roster))

    # Start the newcomer's cursor at the end of the transcript. Everything said
    # so far is already in its handoff.md; leaving the cursor at zero makes its
    # very first `wait` return the whole backlog at once, which it then tries to
    # answer instead of blocking for the question actually being put to it --
    # and usually ends its turn without looping back. `resume` does the same
    # thing for the same reason.
    chat_now = sd / "chat.md"
    (pdir / "cursor").write_text(
        str(chat_now.stat().st_size if chat_now.exists() else 0),
        encoding="utf-8")
    # Same offset as a floor: everything above it is history this agent was
    # briefed on, not mail it owes an answer to. Without it, "unanswered" would
    # mean the whole transcript for an agent that has not spoken yet.
    set_floor(sd, pid)

    manual = (f'cmd /c "{runner}"' if os.name == "nt" else f'bash "{runner}"')
    spec = (f"{args.provider}"
            f"{'/' + args.model if args.model else ''}"
            f"{'/' + args.effort if args.effort else ''}"
            f", auto={args.auto}")
    if label:
        msg = f"{pid} ({spec}) started in {label}"
    elif args.no_tab:
        msg = f"{pid} ({spec}) registered. Start it with:{NL}  {manual}"
    else:
        msg = (f"{pid} ({spec}) registered, but no terminal could be opened."
               f"{NL}Open a tab yourself and run:{NL}  {manual}")
    live = [k for k, v in roster["partners"].items() if v.get("status") == "running"]
    if label:
        msg += (f"{NL}{len(live)} agents now running: {', '.join(live)}. "
                f"You are {me_id(roster)}, and you hold the baton.")
    emit(args, {**entry, "manual_command": manual, "seed": str(seed_f),
                "running": live}, msg)
    return 0


def cmd_archive(args) -> int:
    sd = state_dir()
    meta = archive_current(sd, args.label)
    if not meta:
        emit(args, {"archived": None}, "nothing to archive")
        return 0
    emit(args, meta,
         f"archived as {meta['id']} "
         f"({len(meta['agents'])} agents, {meta['messages']} messages)"
         f"{NL}  {meta['label']}")
    return 0


def cmd_sessions(args) -> int:
    sd = state_dir()
    past = read_sessions(sd)
    roster = load_roster(sd)
    live = {"id": "current", "agents": roster.get("partners", {}),
            "messages": len(tail_msgs(sd, 100000)),
            "label": session_label(sd, roster) if roster.get("partners") else "empty"}
    if args.json:
        print(json.dumps({"current": live, "archived": past}, indent=2))
        return 0
    print(f"current   {len(live['agents'])} agents, {live['messages']} messages"
          f"   {live['label']}")
    if not past:
        print("no archived sessions")
        return 0
    for m in past:
        ids = ", ".join(m.get("agents", {})) or "-"
        print(f"{m['id']}   {len(m.get('agents', {}))} agents, "
              f"{m.get('messages', 0)} messages   [{ids}]")
        print(f"{'':17} {m.get('label', '')}")
    return 0


def cmd_resume(args) -> int:
    """Bring a session back, or restart the agents of the current one.

    Either way every agent is recreated and re-briefed from the transcript, so
    the argument continues where it stopped instead of starting over.
    """
    root = repo_root()
    sd = state_dir(root)
    script = SCRIPT

    if args.session:
        src = sessions_dir(sd) / args.session
        if not src.exists():
            known = ", ".join(m["id"] for m in read_sessions(sd)) or "none"
            emit(args, {"error": "unknown"},
                 f"no session '{args.session}'. Archived: {known}")
            return 1
        archive_current(sd)                       # never lose what was live
        for item in src.iterdir():
            if item.name == "meta.json":
                continue
            target = sd / item.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.copytree(str(item), str(target)) if item.is_dir() \
                else shutil.copy2(str(item), str(target))

    roster = load_roster(sd)
    if not roster["partners"]:
        emit(args, {"error": "empty"},
             "nothing to resume -- start one with: partner.py spawn --provider ...")
        return 1

    # Whoever is running this adopts the session-kind entry; a restored roster
    # would otherwise still name the agent that created it.
    me = next((p for p, v in roster["partners"].items()
               if (v.get("kind") or ("session" if v.get("tab") == "this session"
                                     else "tab")) == "session"), None)
    if me:
        roster["self"] = me
        roster["partners"][me]["kind"] = "session"
        roster["partners"][me]["status"] = "running"
        roster["partners"][me]["tab"] = "this session"
        write_wrappers(sd, script, me)
        write_briefing(sd, me, roster, root, script, None, kind="session")

    # Every tab is relaunched below, so there is nobody to type a wake-up into;
    # the stamp keeps the fresh agents from pausing before they have spoken.
    unlink_quietly(sd / "paused.json")
    write_float(sd / "activity", time.time())
    for pid in roster["partners"]:
        # Markers from before the relaunch describe processes that are gone.
        for name in ("turn", "idle_since", "lastwait"):
            unlink_quietly(sd / pid / name)

    # History belongs in the briefing, not the inbox: an agent that finds 200
    # old messages waiting will try to answer all of them.
    end = (sd / "chat.md").stat().st_size if (sd / "chat.md").exists() else 0
    started = []
    for pid, entry in roster["partners"].items():
        if pid == me:
            continue
        entry.setdefault("kind", "tab")
        label, _ = relaunch(sd, pid, roster, root, script, args.no_tab,
                            getattr(args, "terminal", None))
        started.append(f"{pid} ({entry.get('provider')}"
                       f"{'/' + entry['model'] if entry.get('model') else ''})"
                       f"{' -> ' + label if label else ''}")
        (sd / pid / "cursor").write_text(str(end), encoding="utf-8")
        # A resumed agent owes nothing for the transcript it was re-briefed on.
        set_floor(sd, pid)

    write_wrappers(sd, script)
    save_roster(sd, roster)
    what = f"session {args.session}" if args.session else "the current session"
    append_msg(sd, "system", "@all",
               f"{what.capitalize()} resumed by **{me or me_id(roster)}**. "
               f"Everyone has been re-briefed from the transcript above -- pick up "
               f"where we stopped rather than starting over.", baton_of(roster))
    emit(args, {"resumed": args.session or "current", "started": started,
                "self": me},
         f"resumed {what}" + NL + NL.join(f"  {x}" for x in started))
    return 0


def cmd_stop(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    me = me_id(roster)
    if args.all:
        # --all means every agent you spawned. This session has no tab to close
        # and stopping it would leave the transcript with nobody reading it.
        targets = [k for k in roster["partners"] if k != me]
    else:
        targets = [args.id] if args.id else []
    if not targets:
        emit(args, {"error": "target"}, "pass --id <id> or --all")
        return 1
    for t in targets:
        if t in roster["partners"]:
            roster["partners"][t]["status"] = "stopped"
    if roster.get("baton") in targets:
        roster["baton"] = me
    save_roster(sd, roster)
    append_msg(sd, "system", "@all",
               f"Stopped: {', '.join(targets)}. Say goodbye and stop looping.",
               baton_of(roster))
    emit(args, {"stopped": targets},
         f"stopped {', '.join(targets)} -- they exit at their next `wait`; "
         f"close their tabs when they do")
    return 0
