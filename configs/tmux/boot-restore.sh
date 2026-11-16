#!/usr/bin/env bash
# boot-restore.sh: restore the last tmux-resurrect snapshot when the tmux server has just started,
# and make sure tmux-continuum auto-save runs.
#
# Why: continuum skips BOTH restore and auto-save when it sees more than one other tmux process at
# server start (two kitty windows opening at login, or any other tmux command running that second).
# 2026-11-13 03:04 boot: nothing restored, nothing saved for an hour.
# Run from .tmux.conf after tpm, with @continuum-restore 'off' (else both would restore).
#
# Usage: boot-restore.sh            restore if the server started < 20 s ago, then enable auto-save
#        boot-restore.sh --save-on  only enable auto-save (safe on a running server)
# Skip:  touch ~/tmux_no_auto_restore   (same switch continuum uses)
# Log:   ~/.local/state/tmux/boot-restore.log
# Undo a bad restore: <resurrect-dir>/last-before-boot points at the snapshot used at the last start.
set -u
plugins=~/.tmux/plugins
log=~/.local/state/tmux/boot-restore.log
mkdir -p "${log%/*}"
exec >>"$log" 2>&1
say() { echo "$(date '+%F %T') $*"; }

enable_autosave() {
  local sr
  sr=$(tmux show -gv status-right)
  [[ $sr == *continuum_save.sh* ]] && return
  # first save after one full interval, never before the restore has finished
  tmux set -gq @continuum-save-last-timestamp "$(date +%s)"
  tmux set -gq status-right "#($plugins/tmux-continuum/scripts/continuum_save.sh)$sr"
  say "auto-save was off: enabled"
}

if [ "${1:-}" != --save-on ]; then
  now=$(date +%s)
  start=$(tmux display -p '#{start_time}' 2>/dev/null)
  age=$(( now - ${start:-$now} ))   # no start time = treat as just started
  if [ -f ~/tmux_no_auto_restore ]; then
    say "no restore: ~/tmux_no_auto_restore exists"
  elif [ "$age" -ge 20 ]; then
    say "no restore: server is ${age}s old (config reload)"
  else
    dir=$(tmux show -gqv @resurrect-dir)
    dir=${dir:-$HOME/.local/share/tmux/resurrect}
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -e "$dir/last" ] && break; sleep 1; done   # /mnt/safe may mount late
    if [ -e "$dir/last" ]; then
      snap=$(readlink -f "$dir/last")
      ln -sfn "$snap" "$dir/last-before-boot"
      say "server ${age}s old, $(tmux list-clients 2>/dev/null | wc -l) client(s): restoring $snap"
      sleep 1
      "$plugins/tmux-resurrect/scripts/restore.sh"
      say "restore.sh exit $?, sessions now: $(tmux list-sessions 2>/dev/null | wc -l)"
    else
      say "no restore: $dir/last not found"
    fi
  fi
fi
enable_autosave
