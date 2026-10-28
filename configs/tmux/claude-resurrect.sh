#!/usr/bin/env bash
# tmux-resurrect <-> Claude Code: bring every Claude pane back on its own session id.
#   save    — @resurrect-hook-post-save-layout: drop rcterm-* helper sessions from the layout,
#             then record which pane runs which Claude session
#   restore — @resurrect-processes inline strategy: resume the session recorded for this pane
# Source of truth is Claude Code's live registry, ~/.claude/sessions/<pid>.json
# (sessionId, cwd, tmux = "<session>:@<window_id>.%<pane_id>"), so no Claude hook is needed.
# Set CLAUDE_RESURRECT_DRY=1 to print the resume command instead of running it.

tmux=/usr/bin/tmux
dir="$("$tmux" show -gqv @resurrect-dir)"
map="${dir:-${XDG_DATA_HOME:-$HOME/.local/share}/tmux/resurrect}/claude_sessions.tsv"

# rcterm-* are the in-game terminal's helper sessions: never restore them.
# A helper that is the group original hands its windows to a non-helper member of the group.
drop_helpers() {
  awk -F'\t' -v OFS='\t' '
    function helper(s) { return s ~ /^rcterm-/ }
    function rehome(s) {
      if (helper(s) && (s in orig)) s = orig[s]
      return (s in base) ? base[s] : s
    }
    NR == FNR {
      if ($1 == "grouped_session") {
        orig[$2] = $3
        if (helper($3) && !helper($2) && !($3 in base)) base[$3] = $2
      }
      next
    }
    $1 == "grouped_session" {
      if (helper($2) || (($3 in base) && base[$3] == $2)) next
      $3 = rehome($3)
    }
    ($1 == "pane" || $1 == "window") && ($2 in base) { $2 = base[$2] }
    $1 == "state" { $2 = rehome($2); $3 = rehome($3) }
    { print }
  ' "$1" "$1" > "$1.tmp" && mv -f "$1.tmp" "$1"
}

save() {
  [[ -f $1 ]] && drop_helpers "$1"
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
  save) save "$2" ;;
  restore) restore ;;
  *) echo "usage: $0 save|restore" >&2; exit 2 ;;
esac
