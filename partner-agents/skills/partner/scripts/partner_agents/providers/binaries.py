"""Where agent CLIs get installed, and finding one by name."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..state import cache_dir


# --------------------------------------------------------------------------
# where CLIs get installed

def _user_bin_dirs() -> list[Path]:
    r"""The per-user directories package managers drop CLI entry points into.

    PATH alone is not enough. A CLI installed by a vendor installer often lands
    somewhere PATH only picks up in a login shell -- codex under
    %LOCALAPPDATA%\Programs\OpenAI\Codex\bin is a real example -- and an agent
    inherits whatever environment the harness happened to have.
    """
    home = Path.home()
    d = [home / ".local" / "bin", home / "bin", home / ".bun" / "bin",
         home / ".cargo" / "bin", home / "go" / "bin", home / ".deno" / "bin",
         home / ".npm-global" / "bin", home / ".volta" / "bin",
         home / ".yarn" / "bin", home / ".pixi" / "bin"]
    if os.name == "nt":
        appdata, local = os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")
        if appdata:
            d += [Path(appdata) / "npm", Path(appdata) / "Python" / "Scripts"]
        if local:
            lp = Path(local)
            d += [lp / "Microsoft" / "WinGet" / "Links", lp / "pnpm",
                  lp / "Yarn" / "bin"]
            progs = lp / "Programs"
            if progs.is_dir():
                # Vendor installers bury the entry point a level or two down.
                for pat in ("*/bin", "*/*/bin", "*/*"):
                    try:
                        d += [p for p in progs.glob(pat) if p.is_dir()]
                    except OSError:
                        pass
    else:
        d += [Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
              home / ".local" / "share" / "pnpm"]
    return d


def _exe_exts() -> tuple[str, ...]:
    if os.name != "nt":
        return ("",)
    raw = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PS1")
    return tuple(e.lower() for e in raw.split(os.pathsep) if e)


def search_dirs(skip_system: bool = True) -> list[Path]:
    r"""Every directory worth looking in, deduplicated, user dirs first.

    `skip_system` drops the operating system's own bin directories. Nobody
    installs an agent CLI into C:\Windows\System32 or /usr/sbin, and scanning
    them costs thousands of pointless probes -- plus a listing full of
    gpg-agent and MBR2GPT, which is worse than useless when the output is
    meant to be a short list of things to spawn a partner in.
    """
    sysish = ("\\windows\\", "system32", "syswow64", "/usr/bin", "/bin/",
              "/sbin", "/usr/sbin", "\\program files")
    out, seen = [], set()
    cands = [str(p) for p in _user_bin_dirs()]
    cands += (os.environ.get("PATH") or "").split(os.pathsep)
    for raw in cands:
        if not raw.strip():
            continue
        try:
            p = Path(raw).expanduser()
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if key in seen or not p.is_dir():
            continue
        if skip_system and any(s in key + os.sep for s in sysish):
            continue
        seen.add(key)
        out.append(p)
    return out


# --------------------------------------------------------------------------
# reading a CLI's own help

def find_binary(name: str) -> str | None:
    """Resolve a CLI name to a path, PATH or not.

    PATH is not the boundary of what is installed. Codex ships into
    %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin, which a login shell picks up
    and an agent's inherited environment may not -- and the scan looks there
    regardless. Anything the scan resolved is remembered, so naming it later
    works even though `shutil.which` cannot see it.
    """
    if os.sep in name or "/" in name:
        return name if Path(name).exists() else None
    hit = shutil.which(name)
    if hit:
        return hit
    try:
        seen = json.loads((cache_dir() / "clis.json").read_text(encoding="utf-8"))
        path = seen.get(name)
        return path if path and Path(path).exists() else None
    except (OSError, ValueError, AttributeError):
        return None
