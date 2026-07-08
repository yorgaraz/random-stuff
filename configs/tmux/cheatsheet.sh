#!/usr/bin/env bash
# tmux cheatsheet viewer — opened from the status-bar "?" or the :cheatsheet command.
# Renders cheatsheet.md (kept alongside this script) in a pager, preferring glow.

self="$(readlink -f "${BASH_SOURCE[0]:-$0}")"
dir="$(dirname "$self")"
md="$dir/cheatsheet.md"

if command -v glow >/dev/null 2>&1; then
  exec glow -p "$md"
elif command -v bat >/dev/null 2>&1; then
  exec bat --paging=always --style=plain -l markdown "$md"
else
  exec less -R -- "$md"
fi
