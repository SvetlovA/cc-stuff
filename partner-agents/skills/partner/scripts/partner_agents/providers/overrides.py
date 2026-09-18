"""CLIs the user described, kept between sessions.

~/.claude/partner-providers.json (or .partner/providers.json for one repo)
adds a CLI or corrects a recipe, and a name from it then works as a plain
`--provider`.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..state import state_dir


# --------------------------------------------------------------------------
# CLIs the user described, kept between sessions

OVERRIDE_FILES = [
    Path.home() / ".claude" / "partner-providers.json",
    Path.home() / ".partner" / "providers.json",
]


def load_overrides() -> dict:
    """CLIs the user described once and wants to keep.

    Optional in every sense -- `--provider custom --cmd '...'` still works ad
    hoc and needs no file at all. This exists so a CLI used often does not have
    to be retyped, and so a repo can pin one for everybody working in it.
    Repo-level entries win over home-level ones.

    Each entry: {"cmd": "<template containing {prompt}>", "efforts": [...],
                 "install": "...", "note": "...", "wait_note": "..."}

    `wait_note` is how a CLI whose command-runtime cap this plugin has never
    seen still briefs its partners correctly -- it replaces the generic wait
    guidance for that CLI.
    """
    files = list(OVERRIDE_FILES)
    try:
        files.append(state_dir() / "providers.json")
    except OSError:
        pass
    out: dict = {}
    for f in files:
        try:
            if f.is_file():
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict) and v.get("cmd"):
                            out[k] = {**v, "source": str(f)}
        except (OSError, ValueError):
            continue
    return out
