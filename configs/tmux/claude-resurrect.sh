#!/usr/bin/env bash
# tmux-resurrect <-> Claude Code: bring every Claude pane back on its own session id.
#   save    — @resurrect-hook-post-save-layout: drop rcterm-* helper sessions from the layout,
#             then record which pane runs which Claude session
#   restore — @resurrect-processes inline strategy: resume the session recorded for this pane
# Source of truth is Claude Code's live registry, ~/.claude/sessions/<pid>.json
# (sessionId, cwd, tmux = "<session>:@<window_id>.%<pane_id>"), so no Claude hook is needed.
# A session that fleet (configs/claude/bin/fleet) runs as a background job is NOT resumed here: that would start a
# second copy of it. The pane gets `fleet view <name>` instead. Without `fleet` on PATH nothing changes.
# Set CLAUDE_RESURRECT_DRY=1 to print the resume command instead of running it.

tmux=${CLAUDE_RESURRECT_TMUX:-/usr/bin/tmux} # the override is for tests
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

# A fleet entry that was disabled (`fleet stash --disable`) must not come back with the restore: its session, window
# and pane lines (and a client / grouped-session line that names it) are dropped from the snapshot. The names come
# from fleet's own state (entries.json, only safe names); nothing is killed at boot. A name that is a live tmux session at
# save time is never dropped (see the loop).
drop_disabled() {
  local f=${FLEET_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/fleet}/entries.json names
  [[ -r $f ]] || return 0
  # a disabled entry has no tmux session by design: a name that exists as a tmux session NOW is a new session of the user's
  # that reuses it (fleet list shows NAME CLASH), and it must keep its windows and Claude panes in the snapshot
  local n keep=()
  while IFS= read -r n; do
    [[ -n $n ]] && ! "$tmux" has-session -t "=$n" 2>/dev/null && keep+=("$n")
  done < <(jq -r '.disabled // {} | keys[] | select(test("^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"))' "$f" 2>/dev/null)
  names=$(IFS=,; echo "${keep[*]}")
  [[ -n $names ]] || return 0
  awk -F'\t' -v OFS='\t' -v names="$names" '
    BEGIN { n = split(names, a, ","); for (i = 1; i <= n; i++) d[a[i]] = 1 }
    ($1 == "pane" || $1 == "window") && ($2 in d) { next }
    ($1 == "grouped_session" || $1 == "state") && (($2 in d) || ($3 in d)) { next }
    { print }
  ' "$1" > "$1.tmp" && mv -f "$1.tmp" "$1"
}

# Panes that only VIEW a background job (`claude attach <job>`, e.g. opened by claude-resume) write no session
# file with a tmux field: find them by process tree. Prints "<pane_id>\t<session id>\t<cwd>".
attach_panes() {
  local procs id ppid job sid cwd
  procs=$(ps -eo pid=,ppid=,args= 2>/dev/null)
  while IFS=$'\t' read -r id ppid; do
    job=$(awk -v root="$ppid" '
      { p = $1; par[p] = $2; $1 = $2 = ""; sub(/^ +/, ""); cmd[p] = $0 }
      END {
        for (p in cmd) if (cmd[p] ~ /^([^ ]*\/)?claude attach [0-9a-f]+$/) {
          q = p; n = 0
          while ((q in par) && q != root && n++ < 20) q = par[q]
          if (q == root) { k = split(cmd[p], a, " "); print a[k]; exit }
        }
      }' <<<"$procs")
    [[ $job =~ ^[0-9a-f]{8}$ ]] || continue
    IFS=$'\t' read -r sid cwd < <(jq -r '[.sessionId // "", .cwd // ""] | @tsv' "$HOME/.claude/jobs/$job/state.json" 2>/dev/null) || continue
    [[ $sid =~ ^[0-9a-f-]{36}$ ]] && printf '%s\t%s\t%s\n' "$id" "$sid" "$cwd"
  done < <("$tmux" list-panes -a -F $'#{pane_id}\t#{pane_pid}')
}

save() {
  [[ -f $1 ]] && { drop_helpers "$1"; drop_disabled "$1"; }
  local f pid kind sid cwd pane id key
  declare -A row_of
  for f in "$HOME"/.claude/sessions/*.json; do
    IFS=$'\t' read -r pid kind sid cwd pane < <(jq -r '[.pid, .kind, .sessionId, .cwd, .tmux // ""] | @tsv' "$f" 2>/dev/null) || continue
    [[ $kind == interactive && -n $sid && $pane == *%* ]] || continue
    [[ $(cat "/proc/$pid/comm" 2>/dev/null) == claude ]] || continue # file left behind by a crash
    row_of["%${pane##*%}"]="$sid"$'\t'"$cwd"
  done
  while IFS=$'\t' read -r id sid cwd; do
    [[ -z ${row_of[$id]} ]] && row_of[$id]="$sid"$'\t'"$cwd"
  done < <(attach_panes)
  # one row per session:window.pane, so grouped sessions get every name they are known by
  mkdir -p "$(dirname "$map")"
  while IFS=$'\t' read -r id key; do
    [[ -n ${row_of[$id]} ]] && printf '%s\t%s\n' "$key" "${row_of[$id]}"
  done < <("$tmux" list-panes -a -F $'#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}') > "$map.tmp"
  mv -f "$map.tmp" "$map"
}

restore() {
  local key sid cwd fname
  key="$("$tmux" display -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}')"
  IFS=$'\t' read -r _ sid cwd < <(awk -F'\t' -v k="$key" '$1 == k { print; exit }' "$map" 2>/dev/null)
  if [[ -z $sid ]]; then
    echo "claude-resurrect: no Claude session recorded for $key"
    return
  fi
  if command -v fleet >/dev/null 2>&1 && fname=$(fleet owns "$sid" 2>/dev/null) && [[ $fname =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
    ${CLAUDE_RESURRECT_DRY:+echo} exec fleet view "$fname" --shell
    return
  fi
  cd "$cwd" 2>/dev/null || echo "claude-resurrect: $cwd is gone, resuming from $PWD"
  # claude-resume never starts a second copy: it attaches to the background job that holds the conversation,
  # exits 10 when the conversation runs nowhere and has no job (then resume it here, as before), else explains.
  if command -v claude-resume >/dev/null 2>&1; then
    if [[ -n $CLAUDE_RESURRECT_DRY ]]; then
      echo "claude-resume --restore $sid"
      return
    fi
    local rc=0
    claude-resume --restore "$sid" || rc=$?
    case $rc in
      10) ;;                 # runs nowhere, no background job: resume it in this pane
      0) return ;;           # attached (and later detached)
      *) echo "claude-resurrect: claude-resume exit $rc for $sid; left a shell here"; return ;;
    esac
  fi
  ${CLAUDE_RESURRECT_DRY:+echo} exec claude --resume "$sid"
}

case "$1" in
  save) save "$2" ;;
  restore) restore ;;
  *) echo "usage: $0 save|restore" >&2; exit 2 ;;
esac
