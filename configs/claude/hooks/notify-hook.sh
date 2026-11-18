#!/usr/bin/env bash
# notify-hook.sh: Claude Code hook for the notification lanes (sender: notify-user).
#   Notification                          -> notify-user needs <name> <message>
#   UserPromptSubmit, PostToolUse         -> notify-user clear <name>
# <name> = the name of the tmux window this session runs in (found by walking up from $PPID to a process whose
# terminal is a tmux pane); when there is no pane (a fleet background job): the name fleet gave it
# (${FLEET_STATE_DIR:-~/.local/state/fleet}/ids/<session_id>); else the basename of the hook's cwd.
# Never fails the hook: no output, exit 0 always. The clear path runs on every tool call, so it exits at once
# (no forks) while nobody is waiting.
DIR="${NOTIFY_USER_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/notify-user}"
BIN="${NOTIFY_USER_BIN:-$(command -v notify-user || echo "$HOME/.local/bin/notify-user")}"
TMUX_BIN="${NOTIFY_USER_TMUX:-/usr/bin/tmux}"   # the override is for tests

IFS= read -r -d '' json
ev=""
[[ $json =~ \"hook_event_name\"[[:space:]]*:[[:space:]]*\"([A-Za-z]+)\" ]] && ev=${BASH_REMATCH[1]}
case "$ev" in
  Notification) ;;
  UserPromptSubmit|PostToolUse)
    # fast no-op: nobody waiting, nothing to clear
    shopt -s nullglob
    waiting=("$DIR/needs"/s-*)
    (( ${#waiting[@]} )) || exit 0 ;;
  *) exit 0 ;;
esac

# the tty of the nearest ancestor that has one and is a pane in tmux (pure bash, reads /proc)
find_tty() {
  local p=$PPID stat rest nr major minor i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
    [[ $p =~ ^[0-9]+$ && $p -gt 1 ]] || return 1
    stat=$(< "/proc/$p/stat") 2>/dev/null || return 1
    rest=${stat##*) }
    set -- $rest              # state ppid pgrp session tty_nr ...
    nr=${5:-0}
    if (( nr > 0 )); then
      major=$(( (nr >> 8) & 0xfff )); minor=$(( (nr & 0xff) | ((nr >> 12) & 0xfff00) ))
      (( major == 136 )) && { printf '/dev/pts/%d' "$minor"; return 0; }
    fi
    p=$2
  done
  return 1
}

field() { # field <jq path>: one value from the hook JSON, empty on any problem
  printf '%s' "$json" | jq -r "$1 // empty" 2>/dev/null
}

name="" target=""
tty=${NOTIFY_HOOK_TTY:-$(find_tty)}
if [[ -n $tty ]]; then
  while IFS=$'\t' read -r t tg wn; do
    [[ $t == "$tty" && $tg != rcterm-* ]] && { name=$wn; target=$tg; break; }
  done < <("$TMUX_BIN" list-panes -a -F $'#{pane_tty}\t#{session_name}:#{window_index}\t#{window_name}' 2>/dev/null)
fi
# Only when the walk found no pane (a background job has none): the name fleet gave the session, from
# ids/<session-id> = "name TAB scope TAB job-id". A pane session keeps its window name and its jump target.
if [[ -z $name ]]; then
  FLEET_DIR="${FLEET_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/fleet}"
  if [[ $json =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([0-9A-Fa-f-]{8,64})\" && -r $FLEET_DIR/ids/${BASH_REMATCH[1]} ]]; then
    IFS=$'\t' read -r fname _ < "$FLEET_DIR/ids/${BASH_REMATCH[1]}"
    [[ $fname =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] && name=$fname
  fi
fi
if [[ -z $name ]]; then
  cwd=$(field .cwd)
  name=${cwd##*/}
fi
[[ -n $name ]] || exit 0

case "$ev" in
  Notification)
    msg=$(field .message)
    NOTIFY_USER_TARGET=$target "$BIN" needs "$name" "${msg:-waiting for you}" >/dev/null 2>&1 ;;
  *) "$BIN" clear "$name" >/dev/null 2>&1 ;;
esac
exit 0
