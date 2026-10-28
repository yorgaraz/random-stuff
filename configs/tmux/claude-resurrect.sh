#!/usr/bin/env bash
# tmux-resurrect <-> Claude Code: bring every Claude pane back on its own session id.
#   save    — @resurrect-hook-post-save-layout: record which pane runs which Claude session
#   restore — @resurrect-processes inline strategy: resume the session recorded for this pane
# Source of truth is Claude Code's live registry, ~/.claude/sessions/<pid>.json
# (sessionId, cwd, tmux = "<session>:@<window_id>.%<pane_id>"), so no Claude hook is needed.
# Set CLAUDE_RESURRECT_DRY=1 to print the resume command instead of running it.

map="${XDG_DATA_HOME:-$HOME/.local/share}/tmux/resurrect/claude_sessions.tsv"
tmux=/usr/bin/tmux

save() {
  local f pid kind sid cwd pane id key
  declare -A row_of
  for f in "$HOME"/.claude/sessions/*.json; do
    IFS=$'\t' read -r pid kind sid cwd pane < <(jq -r '[.pid, .kind, .sessionId, .cwd, .tmux // ""] | @tsv' "$f" 2>/dev/null) || continue
    [[ $kind == interactive && -n $sid && $pane == *%* ]] || continue
    [[ $(cat "/proc/$pid/comm" 2>/dev/null) == claude ]] || continue # file left behind by a crash
    row_of["%${pane##*%}"]="$sid"$'\t'"$cwd"
  done
  # one row per session:window.pane, so grouped sessions get every name they are known by
  mkdir -p "$(dirname "$map")"
  while IFS=$'\t' read -r id key; do
    [[ -n ${row_of[$id]} ]] && printf '%s\t%s\n' "$key" "${row_of[$id]}"
  done < <("$tmux" list-panes -a -F $'#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}') > "$map.tmp"
  mv -f "$map.tmp" "$map"
}

restore() {
  local key sid cwd
  key="$("$tmux" display -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}')"
  IFS=$'\t' read -r _ sid cwd < <(awk -F'\t' -v k="$key" '$1 == k { print; exit }' "$map" 2>/dev/null)
  if [[ -z $sid ]]; then
    echo "claude-resurrect: no Claude session recorded for $key"
    return
  fi
  cd "$cwd" 2>/dev/null || echo "claude-resurrect: $cwd is gone, resuming from $PWD"
  ${CLAUDE_RESURRECT_DRY:+echo} exec claude --resume "$sid"
}

case "$1" in
  save) save ;;
  restore) restore ;;
  *) echo "usage: $0 save|restore" >&2; exit 2 ;;
esac
