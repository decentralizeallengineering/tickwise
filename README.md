# tickwise

Adaptive tick intervals for agents that wake themselves (a scheduled wakeup or a cron), stdlib-only Python.

## Why

A fixed short tick burns usage while nothing happens, and a fixed long tick misses work. tickwise sets the next interval from what actually moved: new git commits, changed ledger or state files, and how many ticks in a row saw no change. Progress shortens the tick toward a floor. A no-change streak doubles it up to a ceiling. High usage forces at least one hour.

## Install

    pip install .          # or run in place: python3 -m tickwise <cmd>

Python 3.9 or newer, no dependencies.

## Configure

Optional JSON at `$TICKWISE_CONFIG` (default `~/.config/tickwise/config.json`); every key is optional:

    {"floor": 900, "ceiling": 3600, "high_usage": 80, "high_usage_min": 3600, "window_hours": 5,
     "repos": ["~/work/app"], "files": ["~/work/LEDGER.md", "~/work/STATE.md"]}

| Variable | Default | Meaning |
|---|---|---|
| `TICKWISE_STATE` | `$XDG_STATE_HOME/tickwise/state.json` | streak, last interval, saved signals |
| `TICKWISE_SNAPSHOT` | pacekeeper's `usage-state.json` | status-line snapshot with the 5h meter |
| `TICKWISE_OFF` | unset | set to 1 to disable the guard hook |

## Commands

- `tickwise next [--usage P --hours-left H] [--repo DIR] [--file PATH] [--floor S] [--ceiling S] [--peek] [--why]` prints the next interval in seconds. Without `--usage` it reads the 5h meter from the pacekeeper status-line snapshot if present. Usage counts as high at `high_usage` percent, or when the burn rate would pass 100 percent before the reset.
- `tickwise digest --cmd COMMAND ARGS...` runs one token-free command and prints `CHANGED` or `NOCHANGE` by comparing a hash of its output with the last run. A failed command counts as `CHANGED`. A `CHANGED` result also counts as progress for the next `tickwise next`. An agent whose digest says `NOCHANGE` can end the tick in one call.
- `tickwise guard` is a PreToolUse hook. A `ScheduleWakeup` with a delay below `tickwise next` is rewritten to it. A `CronCreate` that fires more often than that is refused. It never blocks on its own errors.

Hook setting for Claude Code (`settings.json`):

    {"hooks": {"PreToolUse": [{"matcher": "ScheduleWakeup|CronCreate",
                               "hooks": [{"type": "command", "command": "tickwise guard"}]}]}}

## Example

    $ tickwise next --why
    900
    no change x1: double toward ceiling
    $ tickwise next
    1800
    $ tickwise digest --cmd git -C ~/work/app log -1 --format=%H
    NOCHANGE

## Tests

    python3 -m unittest discover -s tests
