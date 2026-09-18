"""Model ids this machine knows about, and what their names imply.

Every id is found locally -- asked of the CLI, read from its help or bundle,
or taken from the user's config for it -- and labelled with where it came
from. Nothing is recited from a list here, because such a list goes stale
invisibly.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..providers.binaries import find_binary
from ..providers.probe import probe_cli
from ..providers.resolve import resolve_provider
from ..util import run_command


# --------------------------------------------------------------------------
# models

# One shape, not one list per vendor. A model id is a lowercase family token
# followed by segments, at least one of which carries a version number --
# gpt-5.1, claude-opus-5, gemini-3-pro, llama3:8b, deepseek-v3, qwen2.5-coder.
# Anchoring on the shape rather than on a vendor prefix is what lets a model
# released after this file was written be found at all.
MODEL_ID_RE = re.compile(
    r"(?<![\w/.-])[a-z][a-z0-9]{1,19}(?:[-.][a-z0-9]+){1,5}(?::[a-z0-9.\-]+)?",
    re.I)

# Segments that mean tooling, packaging or plumbing named after a provider --
# claude-code, gemini-cli, gpt-4-config -- rather than a model.
NON_MODEL_SEGMENTS = {
    "code", "desktop", "plugins", "statusline", "setup", "cli", "agent", "md",
    "config", "settings", "json", "yaml", "yml", "toml", "exe", "dll", "cmd",
    "sh", "ps1", "node", "npm", "npx", "win32", "x64", "x86", "arm64", "utf",
    "sha256", "sha1", "md5", "http", "https", "www", "com", "org", "io",
    "log", "tmp", "cache", "bin", "lib", "src", "dist", "build", "py", "js",
    "ts", "css", "html", "svg", "png", "ttf", "woff", "woff2", "map", "lock",
}

# Prefixes and shapes that mean "this is a secret", not a model. Credential
# files are skipped outright (see _config_paths), but a token can turn up in a
# log line or a config comment too, and everything found here is printed into a
# transcript that every partner reads.
SECRET_RE = re.compile(r"^(sk|pk|rk|ghp|gho|ghs|github_pat|xox[abposr]|"
                       r"api|key|token|bearer|secret|aki)[-_]", re.I)


def _looks_like_model(raw: str) -> str | None:
    mid = raw.lower().strip(".,;:)]}\"'`")
    mid = re.sub(r"\.(cmd|sh|ps1|exe|json|md|ya?ml|toml|js|py|txt|lock)$", "", mid)
    if not 5 <= len(mid) <= 60:
        return None
    if not re.fullmatch(r"[a-z0-9._:-]+", mid) or SECRET_RE.match(mid):
        return None
    segs = re.split(r"[-.:]", mid)
    # No vendor names a model with a 20-character segment; every secret does.
    if any(len(s) > 20 for s in segs):
        return None
    if len(segs) < 2 or not segs[0] or not segs[0][0].isalpha():
        return None
    if re.fullmatch(r"v?\d[\d.]*", segs[0]):
        return None
    if any(s in NON_MODEL_SEGMENTS for s in segs):
        return None
    # Session ids, cache keys and UUIDs have exactly the shape of a model id --
    # a word, hyphens, digits -- and config directories are full of them. The
    # tell is a long run of pure hex, which no vendor has yet used to name
    # something a human is expected to type.
    if any(len(s) >= 8 and all(c in "0123456789abcdef" for c in s) for s in segs):
        return None
    # A version number somewhere past the family token is the load-bearing
    # rule: without it every hyphenated word in a help page is a "model".
    if not any(any(ch.isdigit() for ch in s) for s in segs[1:]):
        return None
    return mid


def _ids_in(text: str) -> set[str]:
    out = set()
    for m in MODEL_ID_RE.finditer(text):
        mid = _looks_like_model(m.group(0))
        if mid:
            out.add(mid)
    return out


def _config_paths(name: str) -> list[Path]:
    """Where a CLI called <name> conventionally keeps its settings."""
    home = Path.home()
    pats = ["config.toml", "config.json", "config.yaml", "config.yml",
            "settings.json", "config", "*.json", "*.toml"]
    out: list[Path] = []
    for base in (home / f".{name}", home / ".config" / name):
        if base.is_dir():
            for pat in pats:
                try:
                    out += [p for p in base.glob(pat) if p.is_file()]
                except OSError:
                    pass
    # Never open a credential store. Whatever is found here is printed into a
    # shared transcript, so a file whose whole purpose is holding a secret is
    # not somewhere to go looking for model names.
    out = [f for f in out if not re.search(
        r"credential|secret|token|auth|\bkeys?\b|password|cookie", f.name, re.I)]
    for f in (home / f".{name}.json", home / f".{name}rc",
              home / f".{name}" / "settings.local.json"):
        if f.is_file():
            out.append(f)
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq[:12]


def _model_keyed_values(text: str) -> set[str]:
    """Values sitting under a key whose name contains "model".

    The strongest signal available offline: the user has already told the CLI
    which model to use, so whatever is written there certainly exists.
    """
    out = set()
    try:
        data = json.loads(text)

        def walk(node, key=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, str(k))
            elif isinstance(node, list):
                for v in node:
                    walk(v, key)
            elif isinstance(node, str) and "model" in key.lower():
                mid = _looks_like_model(node)
                if mid:
                    out.add(mid)
        walk(data)
        return out
    except ValueError:
        pass
    for m in re.finditer(r"(?im)^[ \t]*[\"']?([\w.\-]*model[\w.\-]*)[\"']?"
                         r"\s*[:=]\s*[\"']?([^\"'\s,#\]]+)", text):
        mid = _looks_like_model(m.group(2))
        if mid:
            out.add(mid)
    return out


def _bundle_ids(binary: str, cap_mb: int = 48, deadline: float = 0.0) -> set[str]:
    """Model ids embedded in the CLI's own program files.

    Where a CLI ships its model list compiled in -- most of them do -- this is
    the closest thing available to asking it. Read in chunks against a byte and
    time budget, because these bundles run to hundreds of megabytes.
    """
    try:
        real = Path(binary).resolve()
    except OSError:
        return set()
    roots = [real.parent]
    parts = real.parts
    if "node_modules" in parts:
        i = len(parts) - 1 - parts[::-1].index("node_modules")
        roots.append(Path(*parts[: i + 2]))
    # Program text only -- never the compiled executable. Scanned as bytes, a
    # native binary yields hundreds of strings with the shape of a model id and
    # the meaning of none ("a8q.1", "about-seh1"), which buries the handful of
    # real ones. A CLI shipped as JavaScript is where this actually pays off.
    files: list[Path] = []
    for r in roots:
        for pat in ("*.js", "*.mjs", "*.cjs", "*.json"):
            try:
                files += [f for f in r.glob(pat) if f.is_file()]
            except OSError:
                pass
    budget = cap_mb * 1024 * 1024
    out: set[str] = set()
    for f in files[:16]:
        if budget <= 0 or (deadline and time.time() > deadline):
            break
        try:
            with f.open("rb") as fh:
                tail = ""
                while budget > 0:
                    chunk = fh.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    budget -= len(chunk)
                    text = tail + chunk.decode("utf-8", "replace")
                    out |= _ids_in(text)
                    tail = text[-200:]
                    if deadline and time.time() > deadline:
                        break
        except OSError:
            continue
    return out


def discover_models(provider: str, binary: str | None = None,
                    deep: bool = False, budget: float = 25.0) -> list[dict]:
    """Model ids this machine can actually name, with where each came from.

    Ordered newest-looking first. The source matters: a `models` subcommand or
    a key the user configured is evidence; a string in a help page or a bundle
    is a lead. None of it is checked against the vendor -- that is what the web
    is for, and the skill goes there when this comes back thin.
    """
    if binary is None:
        spec = resolve_provider(provider)
        binary = spec.get("bin") and find_binary(spec["bin"])
    deadline = time.time() + budget
    # id -> (confidence rank, where it came from). Rank 0 is the CLI or the
    # user naming a model outright; rank 1 is a string that merely has the
    # shape of one, found in a help page or a bundle. Both are worth showing
    # and they are not worth showing as if they were the same thing.
    hits: dict[str, tuple[int, str]] = {}

    def add(ids, source: str, rank: int = 1) -> None:
        for i in ids:
            if rank < hits.get(i, (9, ""))[0]:
                hits[i] = (rank, source)

    if binary:
        pr = probe_cli(provider, binary)
        if pr.get("list_cmd"):
            # Short leash. "models" in a help page is not proof of a `models`
            # subcommand: on a CLI that has no such command, this launches the
            # agent itself, which then sits waiting for input until the timeout.
            rc, txt = run_command(pr["list_cmd"], 6)
            if rc == 0:
                add(_ids_in(txt), "the CLI's own model list", rank=0)
        add(_ids_in(pr.get("help") or ""), "the CLI's help")

    # Config files split into two treatments by size. Walking JSON for keys
    # named "model" is cheap and precise, so every file gets it; the broad
    # regex over the whole text is neither, so it is spent only on the small
    # files and within a byte budget. A 4 MB .claude.json scanned both ways
    # took twenty seconds and returned session ids.
    loose_budget = 4_000_000
    for f in _config_paths(provider):
        if time.time() > deadline:
            break
        try:
            size = f.stat().st_size
            if size > 8_000_000:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        add(_model_keyed_values(text), f"a model setting in your {f.name}", rank=0)
        if size <= 512_000 and loose_budget > 0:
            loose_budget -= size
            add(_ids_in(text), f"text in your {f.name}")

    # The bundle scan is the expensive one, so it runs when the cheap sources
    # came back thin, or when it was asked for.
    # Only scan a bundle that belongs to this provider. Under a name from the
    # user's providers.json the binary is whatever their template wraps, and
    # reporting that CLI's ids as this provider's would be a plain lie.
    own = binary and Path(binary).stem.lower() == provider.lower()
    if own and (deep or len(hits) < 3) and time.time() < deadline:
        add(_bundle_ids(binary, deadline=deadline), "the CLI's own bundle")

    return [{"id": mid, "source": src, "confidence":
             "named" if rank == 0 else "mentioned", **tier_of(mid)}
            for mid, (rank, src) in sorted(
                hits.items(), key=lambda kv: (kv[1][0], model_rank(kv[0])))]


# What a model's *name* implies about arguing with it. Shapes, not ids, so a
# model released next month is described correctly the first time it appears.
# Advisory: it reports what each vendor's naming convention has meant so far.
TIER_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"(?:^|[-_.])(opus|ultra|max|large|xl|heavy)(?:$|[-_.\d])", re.I),
     "deep", "high",
     "top of its family: deepest reasoning, strongest challenger, slowest"),
    (re.compile(r"(?:^|[-_.])(haiku|flash|mini|lite|nano|small|tiny|instant|"
                r"fast|air|\d+b)(?:$|[-_.\d])", re.I),
     "fast", "low",
     "fast and cheap; concedes too easily to be much of an opponent"),
    (re.compile(r"(?:^|[-_.])(codex|coder|code)(?:$|[-_.\d])", re.I),
     "code", "high",
     "code-tuned: sharper on diffs than on open design questions"),
    (re.compile(r"(?:^|[-_.])(think|thinking|reason|reasoning|r1|o[1-9])(?:$|[-_.\d])",
                re.I),
     "reasoning", "high",
     "reasoning-tuned: slow, and hard to talk out of a position"),
    (re.compile(r"(?:^|[-_.])(sonnet|pro|medium|standard|turbo|plus)(?:$|[-_.\d])",
                re.I),
     "balanced", "high",
     "the balanced tier: fast enough to argue with in real time"),
]


def tier_of(mid: str) -> dict:
    for rx, tier, effort, note in TIER_RULES:
        if rx.search(mid):
            return {"tier": tier, "effort": effort, "note": note}
    return {"tier": "general", "effort": "high",
            "note": "no tier signal in the name -- try it and see"}


def model_rank(mid: str) -> tuple:
    """Newest-looking first: version numbers in order, then any date stamp.

    In order, not the largest -- claude-opus-4-7 has a 7 in it and is older
    than claude-opus-5. The leading number is the generation; the rest are
    point releases within it.
    """
    nums = [-float(n) for n in
            re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", mid) if len(n) < 5]
    date = max([int(d) for d in re.findall(r"\b(20\d{6})\b", mid)] or [0])
    return (tuple(nums), -date, mid)
