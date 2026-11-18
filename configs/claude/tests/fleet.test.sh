#!/usr/bin/env bash
# Tests for fleet (configs/claude/bin/fleet), its hook and the two edits it made (notify-hook.sh name lookup,
# tmux/claude-resurrect.sh guard). Plain bash entry point, no arguments, exit 0 = pass. Everything runs against a
# fake `claude`, a fake `tmux`, a fake notify-user and fixture Claude data dirs under a temp dir: no real session,
# job or tmux pane is touched (the real state dir is never used). The checks live in fleet_test.py.
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
exec python3 "$HERE/fleet_test.py" "$@"
