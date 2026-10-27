"""PreToolUse hook for ScheduleWakeup and CronCreate: raise too-short intervals to `tickwise next`.

Reads the hook JSON on stdin. A ScheduleWakeup delay below the advised interval is rewritten to it. A cron
expression that fires more often than the advised interval is refused. Fails open (exit 0, no output) on any
error. Kill switch: TICKWISE_OFF=1.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

from . import core


def cron_min_gap(expr: str) -> int | None:
    """Smallest gap in seconds between firings of a 5-field cron expression, judged from the minute and hour fields.
    None when the expression cannot be judged."""
    f = expr.split()
    if len(f) < 2:
        return None
    minute, hour = f[0], f[1]
    m = re.fullmatch(r"\*/(\d+)", minute)
    if minute == "*":
        mins = list(range(60))
    elif m and int(m.group(1)) > 0:
        mins = list(range(0, 60, int(m.group(1))))
    elif all(x.isdigit() for x in minute.split(",")):
        mins = sorted(int(x) for x in minute.split(","))
    else:
        return None
    gaps = [b - a for a, b in zip(mins, mins[1:])] + [mins[0] + 60 - mins[-1]]
    gap = min(gaps) * 60
    h = re.fullmatch(r"\*/(\d+)", hour)
    if len(mins) == 1 and h and int(h.group(1)) > 0:
        gap = int(h.group(1)) * 3600
    return gap


def advised() -> tuple[int, str]:
    cfg = core.load_config()
    used, left = core.read_usage()
    return core.next_interval(cfg, used, left, save=False)


def handle(data: dict) -> dict | None:
    name, ti = data.get("tool_name"), data.get("tool_input") or {}
    if name == "ScheduleWakeup":
        if ti.get("stop"):
            return None
        secs, why = advised()
        if int(ti.get("delaySeconds") or 0) < secs:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "allow",
                "permissionDecisionReason": f"tickwise: delay raised to {secs} s ({why})",
                "updatedInput": {**ti, "delaySeconds": secs}}}
        return None
    if name == "CronCreate":
        expr = ti.get("cron") or ti.get("schedule") or ""
        gap = cron_min_gap(expr)
        secs, why = advised()
        if gap is not None and gap < secs:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"tickwise: cron '{expr}' fires every {gap} s, advised minimum is {secs} s ({why}); "
                                            "use a wakeup or an event monitor instead."}}
    return None


def main() -> int:
    import os
    if os.environ.get("TICKWISE_OFF"):
        return 0
    try:
        out = handle(json.load(sys.stdin))
        if out:
            print(json.dumps(out))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return 0   # fail open: a broken hook must never block the agent
    return 0
