"""Typing wake-ups on behalf of agents that cannot.

Waking an agent means running its terminal's own CLI -- `orca terminal send`.
An agent in a sandbox cannot: Codex under `-s workspace-write` is not even
allowed to read Orca's runtime file, so every nudge it attempted failed. A
partner that went deaf stayed deaf for as long as the only one noticing was
sandboxed, and a pause lifted from a sandboxed tab woke nobody.

So typing is delegated. An agent whose own attempt fails drops a request in
<id>/wake.json, and one waker process per session performs it. Any agent may
start the waker; it proves it can drive the terminal before taking the job, so
one started from inside a sandbox just exits and leaves the job to one that
can. Which agent asks, and which one started it, does not matter -- that is
what keeps the partners equal.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .state import SCRIPT
from .timing import WAKER_RELAUNCH, WAKER_STALE
from .util import nag_throttled


def waker_alive(sd: Path) -> bool:
    try:
        beat = json.loads((sd / "waker.json").read_text(encoding="utf-8"))["beat"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return time.time() - float(beat) < WAKER_STALE


def request_wake(sd: Path, pid: str, why: str) -> None:
    try:
        (sd / pid).mkdir(parents=True, exist_ok=True)
        (sd / pid / "wake.json").write_text(
            json.dumps({"why": why, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def ensure_waker(sd: Path) -> None:
    """Start the waker if none is running. Cheap enough to call on every `wait`.

    Detached, so it outlives the command that started it -- a background
    `wait` that returns must not take the session's only waker with it.
    """
    if waker_alive(sd) or nag_throttled(sd / ".waker-launch", WAKER_RELAUNCH):
        return
    env = {k: v for k, v in os.environ.items() if k != "PARTNER_ID"}
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL, "close_fds": True,
                "cwd": str(sd.parent), "env": env}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.CREATE_NO_WINDOW)
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, str(SCRIPT), "waker"], **kw)
    except OSError:
        pass
