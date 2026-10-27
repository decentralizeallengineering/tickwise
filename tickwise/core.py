"""Interval policy, progress signals and the state file.

The policy is a pure function of (previous interval, streak, progress, usage),
so it can be tested without a clock, a git repo or a status line.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULTS = {
    "floor": 900,            # seconds: shortest tick
    "ceiling": 3600,         # seconds: longest tick
    "high_usage": 80.0,      # percent of the usage window that forces a long tick
    "high_usage_min": 3600,  # seconds: tick length forced at high usage
    "window_hours": 5.0,     # length of the usage window the percent refers to
    "repos": [],             # git work trees; a new commit is progress
    "files": [],             # ledger or state files (globs allowed); a change is progress
}


def state_dir() -> Path:
    env = os.environ.get("TICKWISE_STATE_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "tickwise"


def state_path() -> Path:
    return Path(os.environ.get("TICKWISE_STATE") or state_dir() / "state.json")


def config_path() -> Path:
    return Path(os.environ.get("TICKWISE_CONFIG")
                or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tickwise" / "config.json")


def snapshot_path() -> Path:
    """Status-line snapshot written by pacekeeper (same default location)."""
    env = os.environ.get("TICKWISE_SNAPSHOT") or os.environ.get("PACEKEEPER_SNAPSHOT")
    if env:
        return Path(env)
    base = os.environ.get("PACEKEEPER_STATE_DIR")
    if base:
        return Path(base) / "usage-state.json"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "pacekeeper" / "usage-state.json"


def load_json(path, default):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(load_json(config_path(), {}))
    return cfg


def read_usage(now: float | None = None) -> tuple[float | None, float | None]:
    """(percent used, hours to reset) of the 5h meter from the snapshot, else (None, None)."""
    snap = load_json(snapshot_path(), {})
    v = (snap.get("rate_limits") or {}).get("five_hour") or {}
    try:
        used = float(v["used_percentage"])
    except (KeyError, TypeError, ValueError):
        return None, None
    try:
        left = (float(v["resets_at"]) - (now if now is not None else time.time())) / 3600.0
    except (KeyError, TypeError, ValueError):
        left = None
    if left is not None and left < 0:
        return None, None  # window already rolled over: the reading is old
    return used, left


def repo_head(repo: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], check=False, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def file_stamp(pattern: str) -> str | None:
    """Fingerprint of every file matching a path or glob: names, sizes, mtimes."""
    paths = sorted(glob.glob(os.path.expanduser(pattern)))
    if not paths:
        return None
    parts = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        parts.append(f"{p}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest() if parts else None


def collect_signals(repos, files) -> dict:
    return {"repos": {r: repo_head(r) for r in repos}, "files": {f: file_stamp(f) for f in files}}


def progress_between(old: dict, new: dict) -> list[str]:
    """Names of watched things that moved since the saved signals. An unseen item is a baseline, not progress."""
    moved = []
    for kind in ("repos", "files"):
        for k, v in new.get(kind, {}).items():
            if k in old.get(kind, {}) and v != old[kind][k] and v is not None:
                moved.append(k)
    return moved


def high_usage(used, hours_left, cfg) -> str | None:
    """Reason text when usage is high, else None. Also high when the current burn rate would pass 100% before the reset."""
    if used is None:
        return None
    if used >= cfg["high_usage"]:
        return f'usage {used:.0f}% >= {cfg["high_usage"]:.0f}%'
    if hours_left is not None:
        elapsed = cfg["window_hours"] - hours_left
        if elapsed >= 0.25 and used / elapsed * cfg["window_hours"] >= 100.0:
            return f"usage {used:.0f}% with {hours_left:.1f} h to reset projects past 100%"
    return None


def decide(last: int, streak: int, progress: bool, cfg: dict, used=None, hours_left=None) -> tuple[int, int, str]:
    """(interval seconds, new streak, reason). Progress halves toward the floor; no change doubles toward the ceiling."""
    floor, ceiling = int(cfg["floor"]), max(int(cfg["ceiling"]), int(cfg["floor"]))
    if progress:
        streak, secs, why = 0, max(floor, int(last) // 2), "progress: shorten toward floor"
    else:
        streak += 1
        secs, why = min(ceiling, floor * (2 ** min(streak - 1, 30))), f"no change x{streak}: double toward ceiling"
    hi = high_usage(used, hours_left, cfg)
    if hi and secs < cfg["high_usage_min"]:
        secs, why = int(cfg["high_usage_min"]), f'high usage forces {cfg["high_usage_min"]} s ({hi})'
    return int(secs), streak, why


def next_interval(cfg: dict, used=None, hours_left=None, save: bool = True) -> tuple[int, str]:
    """Compute the next interval, comparing current signals with the saved ones and updating the state file."""
    st = load_json(state_path(), {})
    sig = collect_signals(cfg["repos"], cfg["files"])
    moved = progress_between(st.get("signals", {}), sig)
    progress = bool(moved) or bool(st.get("pending_progress"))
    last = int(st.get("last", cfg["floor"]))
    secs, streak, why = decide(last, int(st.get("streak", 0)), progress, cfg, used, hours_left)
    if moved:
        why += " (" + ", ".join(moved) + ")"
    if save:
        save_json(state_path(), {**st, "signals": sig, "streak": streak, "last": secs, "pending_progress": False,
                                 "updated": int(time.time())})
    return secs, why


def digest(cmd: list[str], timeout: float = 120.0, save: bool = True) -> bool:
    """Run the digest command; True when its output differs from the last run (or it failed, or this is the first run)."""
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
        h = hashlib.sha1(b"%d\0" % r.returncode + r.stdout).hexdigest()
    except (OSError, subprocess.SubprocessError):
        h = None
    key = hashlib.sha1("\0".join(cmd).encode()).hexdigest()[:12]
    st = load_json(state_path(), {})
    changed = h is None or st.get("digests", {}).get(key) != h
    if save:
        st.setdefault("digests", {})[key] = h
        if changed:
            st["pending_progress"] = True
        save_json(state_path(), st)
    return changed
