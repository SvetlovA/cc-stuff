"""Opening a partner's tab, and listing the tabs that are open."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from ..terminals.detect import terminal_available, terminal_bin, terminal_order
from ..util import NL, dig_json


def term_argv(template: str, **vals) -> list[str]:
    """Split a terminal template, then substitute inside each argument.

    Substituting first and splitting after would break on every path with a
    space in it, and on Windows would let a backslash be read as an escape.
    """
    out = []
    for part in shlex.split(template):
        for k, v in vals.items():
            part = part.replace("{" + k + "}", str(v))
        if part:
            out.append(part)
    return out


def shell_run(runner: Path) -> str:
    """One string that runs the runner script through a shell -- what the
    terminals that take a command as a single argument need."""
    return f'cmd /c "{runner}"' if os.name == "nt" else f'bash "{runner}"'


def open_in(name: str, spec: dict, title: str, runner: Path, cwd: Path) -> dict:
    """Open one tab through one terminal entry. {} when it did not work.

    An entry with a `handle` is run synchronously, because the id it prints is
    the only way to type into that tab later; everything else is fire and
    forget, so a terminal that never exits does not block the spawn.
    """
    binary = terminal_bin(spec)
    if not binary:
        return {}
    vals = {"bin": binary, "run": str(runner), "cwd": str(cwd),
            "title": title, "shell": shell_run(runner)}
    argv = term_argv(spec["open"], **vals)
    if not argv:
        return {}
    label = spec.get("label") or f"{name} tab"
    handle_spec = spec.get("handle") or ""
    try:
        if handle_spec:
            r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            if r.returncode != 0:
                return {}
            handle = ""
            if handle_spec.startswith("json:"):
                try:
                    doc = json.loads(r.stdout)
                    if doc.get("ok") is False:
                        return {}
                    handle = dig_json(doc, handle_spec[5:])
                except (json.JSONDecodeError, AttributeError):
                    pass
            elif r.stdout.strip():
                handle = r.stdout.strip().splitlines()[0].strip()
            return {"label": label, "kind": name, "handle": handle,
                    "title": title}
        subprocess.Popen(argv, cwd=str(cwd),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return {}
    return {"label": label, "kind": name, "handle": "", "title": title}


def open_tab(title: str, runner: Path, cwd: Path,
             prefer: str | None = None) -> dict:
    """Open a tab in the first terminal that can take one.

    Returns {label, kind, handle, title} -- stored on the roster entry, because
    waking a silent agent later has to know which terminal owns its tab, and
    that is not recoverable after the fact.
    """
    # Discovery takes part in the *choice*, not just as a last resort: a user
    # sitting in a terminal nothing here has heard of should get the partner
    # beside them, not in whichever other terminal happens to be installed.
    # It costs nothing in the common case -- `discover_terminals` skips any
    # host already described, and probe results are cached per binary -- so the
    # one subprocess is paid only when the host really is unknown.
    for name, spec in terminal_order(prefer, discover=True):
        ok, _ = terminal_available(spec)
        if not ok:
            continue
        tab = open_in(name, spec, title, runner, cwd)
        if tab:
            return tab
    return {}


def write_runner(pdir: Path, argv: list[str], cwd: Path) -> Path:
    # Export PARTNER_ID into the tab's own environment, not just the wrappers'.
    # A Claude Code tab agent and its hooks then resolve identity the same way
    # `me_id()` does -- without it the Stop hook there would fall back to
    # roster["self"] and treat every tab as the session agent.
    pid = pdir.name
    if os.name == "nt":
        r = pdir / "run.cmd"
        r.write_text("@echo off\r\ncd /d \"{}\"\r\nset PARTNER_ID={}\r\n{}\r\n".format(
            cwd, pid, subprocess.list2cmdline(argv)), encoding="utf-8")
    else:
        r = pdir / "run.sh"
        r.write_text("#!/usr/bin/env bash{}cd {}{}export PARTNER_ID={}{}exec {}{}".format(
            NL, shlex.quote(str(cwd)), NL, shlex.quote(pid), NL, shlex.join(argv), NL),
            encoding="utf-8")
        r.chmod(0o755)
    return r


def tab_titles() -> set[str] | None:
    """Titles of live tabs, or None when no terminal here can say.

    Where a terminal can list its own tabs, that is better evidence than a
    heartbeat: it reports the tab itself rather than what the agent last did in
    it. Which terminals can is a property of the entry (`list` + `list_titles`),
    so one described in the user's own file corroborates liveness exactly like a
    built-in, and a terminal that cannot list simply leaves the heartbeat as the
    only evidence.
    """
    for _, spec in terminal_order():
        if not (spec.get("list") and spec.get("list_titles")):
            continue
        binary = terminal_bin(spec)
        if not binary or not terminal_available(spec)[0]:
            continue
        try:
            r = subprocess.run(term_argv(spec["list"], bin=binary, title=""),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
            if r.returncode != 0:
                continue
            doc = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
        path, _, field = (spec["list_titles"]).partition("[].")
        items = dig_json(doc, path, want=list) or []
        return {str(it.get(field, "")) for it in items if isinstance(it, dict)}
    return None
