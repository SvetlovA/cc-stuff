"""Small helpers with no knowledge of partners: time, files, subprocesses, output."""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


NL = chr(10)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def clip_lines(text: str, limit: int, what: str) -> str:
    rows = text.splitlines()
    if len(rows) <= limit:
        return text
    return NL.join(rows[:limit]) + f"{NL}... (+{len(rows) - limit} more {what})"


def run_command(argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 1, ""


def dig_json(data, dotted: str, want: type = str):
    """Walk a dotted path through parsed JSON. An empty path is the document."""
    for key in filter(None, dotted.split(".")):
        if not isinstance(data, dict):
            return want()
        data = data.get(key)
    return data if isinstance(data, want) else want()


def nag_throttled(marker: Path, window: int) -> bool:
    """True when `marker` was stamped within `window` seconds.

    The Stop hook uses this for warnings that are not tied to a new message, so
    an agent that cannot act on one is told at a bounded rate rather than being
    walled in by a block it can never clear.
    """
    now = time.time()
    try:
        if marker.exists() and now - float(
                marker.read_text(encoding="utf-8").strip()) < window:
            return True
    except (OSError, ValueError):
        pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now), encoding="utf-8")
    except OSError:
        pass
    return False


def age_seconds(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds()


def read_float(f: Path) -> float | None:
    try:
        return float(f.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_float(f: Path, value: float) -> None:
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str(value), encoding="utf-8")
    except OSError:
        pass


def unlink_quietly(f: Path) -> None:
    try:
        f.unlink()
    except OSError:
        pass


def emit(args, data: dict, human: str) -> None:
    if getattr(args, "quiet", False):
        return
    print(json.dumps(data, indent=2) if getattr(args, "json", False) else human)
