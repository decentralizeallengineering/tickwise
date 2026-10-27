"""tickwise command line: next, digest, guard."""
from __future__ import annotations

import argparse
import sys

from . import core, guard


def cmd_next(a) -> int:
    cfg = core.load_config()
    for k in ("floor", "ceiling"):
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    cfg["repos"] = list(cfg["repos"]) + a.repo
    cfg["files"] = list(cfg["files"]) + a.file
    used, left = a.usage, a.hours_left
    if used is None:
        used, snap_left = core.read_usage()
        left = left if left is not None else snap_left
    secs, why = core.next_interval(cfg, used, left, save=not a.peek)
    print(secs)
    if a.why:
        print(why, file=sys.stderr)
    return 0


def cmd_digest(a) -> int:
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        print("tickwise digest: --cmd needs a command", file=sys.stderr)
        return 2
    print("CHANGED" if core.digest(cmd, a.timeout, save=not a.peek) else "NOCHANGE")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tickwise", description="Adaptive tick intervals for self-waking agents.")
    sub = p.add_subparsers(dest="cmd_name", required=True)
    n = sub.add_parser("next", help="print the next interval in seconds")
    n.add_argument("--usage", type=float, help="percent of the usage window used (default: pacekeeper snapshot)")
    n.add_argument("--hours-left", type=float, help="hours until the usage window resets")
    n.add_argument("--floor", type=int, help="shortest interval, seconds")
    n.add_argument("--ceiling", type=int, help="longest interval, seconds")
    n.add_argument("--repo", action="append", default=[], help="git work tree to watch (repeatable)")
    n.add_argument("--file", action="append", default=[], help="ledger or state file or glob to watch (repeatable)")
    n.add_argument("--peek", action="store_true", help="do not update the state file")
    n.add_argument("--why", action="store_true", help="print the reason on stderr")
    n.set_defaults(fn=cmd_next)
    d = sub.add_parser("digest", help="run a token-free digest command; print CHANGED or NOCHANGE")
    d.add_argument("--timeout", type=float, default=120.0)
    d.add_argument("--peek", action="store_true", help="do not update the state file")
    d.add_argument("--cmd", nargs=argparse.REMAINDER, required=True, help="the command and its arguments (last option)")
    d.set_defaults(fn=cmd_digest)
    g = sub.add_parser("guard", help="PreToolUse hook: raise too-short ScheduleWakeup and CronCreate intervals")
    g.set_defaults(fn=lambda a: guard.main())
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
