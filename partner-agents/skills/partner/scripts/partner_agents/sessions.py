"""Sessions: archiving the current arrangement and bringing one back.

A session is one arrangement of agents plus the transcript they produced.
Starting a new one never destroys the old: it is moved under sessions/ whole,
so it can be brought back with its agents and its argument intact.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .briefing import boot_prompt, write_briefing
from .providers.launch import build_tui
from .state import baton_of, load_roster
from .terminals.tabs import open_tab, write_runner
from .transcript import tail_msgs
from .util import utcnow


def sessions_dir(sd: Path) -> Path:
    return sd / "sessions"


def agent_dirs(sd: Path) -> list[Path]:
    """Per-agent directories in the live session, skipping sessions/ itself."""
    return [d for d in sd.iterdir()
            if d.is_dir() and d.name != "sessions" and (d / "seed.md").exists()]


def session_label(sd: Path, roster: dict) -> str:
    """A human-recognisable name: the first real thing anybody said."""
    for m in tail_msgs(sd, 200):
        if m["from"] != "system":
            line = " ".join(m["body"].split())
            return (line[:70] + "...") if len(line) > 70 else line
    ids = ", ".join(roster.get("partners", {}))
    return f"no discussion ({ids})" if ids else "empty"


def session_has_substance(sd: Path, roster: dict) -> bool:
    """Whether the live session is one anybody would want back.

    A roster holding only this session -- no spawned partners -- with nothing
    said beyond system notices is not a session that happened. Archiving it
    just files an empty transcript away and forces a needless re-init on the
    next spawn. A bare `/partner` from a cold session leans on this: `--fresh`
    then archives nothing and simply adds the first partner.
    """
    partners = roster.get("partners", {})
    if any((p.get("kind") or "tab") != "session" for p in partners.values()):
        return True
    return any(m["from"] != "system" for m in tail_msgs(sd, 1000))


def archive_current(sd: Path, label: str | None = None) -> dict | None:
    """Move the live session under sessions/ and leave the slate clean."""
    roster = load_roster(sd)
    chat = sd / "chat.md"
    if not roster["partners"] and not chat.exists():
        return None
    if not session_has_substance(sd, roster):
        return None

    sid = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = sessions_dir(sd) / sid
    n = 2
    while dest.exists():
        dest = sessions_dir(sd) / f"{sid}-{n}"
        n += 1
    dest.mkdir(parents=True)

    msgs = tail_msgs(sd, 100000)
    meta = {
        "id": dest.name,
        "archived": utcnow(),
        "label": label or session_label(sd, roster),
        "messages": len(msgs),
        "agents": {pid: {k: p.get(k) for k in
                         ("provider", "model", "effort", "auto", "cmd", "kind")}
                   for pid, p in roster["partners"].items()},
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    for item in [sd / "roster.json", chat]:
        if item.exists():
            shutil.move(str(item), str(dest / item.name))
    for item in [sd / "paused.json", sd / "activity"]:
        # A pause belongs to the arrangement it paused, not to the next one.
        try:
            item.unlink()
        except OSError:
            pass
    for d in agent_dirs(sd):
        shutil.move(str(d), str(dest / d.name))
    return meta


def read_sessions(sd: Path) -> list[dict]:
    root = sessions_dir(sd)
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        f = d / "meta.json"
        if not f.exists():
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def relaunch(sd: Path, pid: str, roster: dict, root: Path, script: Path,
             no_tab: bool = False,
             terminal: str | None = None) -> tuple[str, Path]:
    """Start an agent from its roster entry rather than from CLI arguments.

    Resuming has to rebuild agents it did not create, so the launch path takes
    a roster entry as its input; `spawn` fills one in and calls the same code.
    """
    entry = roster["partners"][pid]
    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    seed_f = write_briefing(sd, pid, roster, root, script, None,
                            kind=entry.get("kind") or "tab")
    boot = boot_prompt(seed_f, baton_of(roster))
    argv = build_tui(entry, boot)
    runner = write_runner(pdir, argv, root)
    tab = {} if no_tab else open_tab(f"partner:{pid}", runner, root, terminal)
    label = tab.get("label", "")
    entry["tab"] = label
    entry["tab_kind"] = tab.get("kind", "")
    entry["tab_handle"] = tab.get("handle", "")
    entry["tab_title"] = tab.get("title", f"partner:{pid}")
    entry["runner"] = str(runner)
    entry["status"] = "running"
    return label, runner
