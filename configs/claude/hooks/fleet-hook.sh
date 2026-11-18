#!/usr/bin/env bash
# fleet-hook.sh: Claude Code hook for fleet (configs/claude/bin/fleet). Always exits 0, never prints on error.
#   SessionStart                 -> prints the session's entry (tmux session), role, peers (name + directory) and tmux session
#                                   from the fleet registry; nothing when the session is not registered
#   Stop, UserPromptSubmit       -> records the effort the session runs with (the hook process sees $CLAUDE_EFFORT
#                                   when the session has one) and the time in seen/<session-id>: "epoch TAB effort".
#                                   A session that has no effort in its environment keeps the last recorded one.
# fleet sync reads seen/ so that a move to background (or a resume) can pass the effort the user set with /effort.
# State dir: ${FLEET_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/fleet}.
# Registry text is data: control characters are stripped and the length capped before it is printed.
DIR="${FLEET_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/fleet}"

IFS= read -r -d '' json
ev="" sid=""
[[ $json =~ \"hook_event_name\"[[:space:]]*:[[:space:]]*\"([A-Za-z]+)\" ]] && ev=${BASH_REMATCH[1]}
[[ $json =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([0-9A-Fa-f-]{8,64})\" ]] && sid=${BASH_REMATCH[1]}
[[ -n $sid ]] || exit 0

case "$ev" in
  Stop|UserPromptSubmit)
    effort=${CLAUDE_EFFORT:-}
    case "$effort" in low|medium|high|xhigh|max) ;; *) effort="" ;; esac
    if [[ -z $effort ]]; then
      # keep the last recorded value (the file may not exist yet)
      IFS=$'\t' read -r _ effort < "$DIR/seen/$sid" 2>/dev/null
      case "$effort" in low|medium|high|xhigh|max) ;; *) effort="" ;; esac
    fi
    umask 077
    mkdir -p -m 700 "$DIR/seen" 2>/dev/null || exit 0
    printf '%(%s)T\t%s\n' -1 "$effort" > "$DIR/seen/$sid" 2>/dev/null
    ;;
  SessionStart)
    [[ -r $DIR/ids/$sid && -r $DIR/registry.json ]] || exit 0
    IFS=$'\t' read -r name _ < "$DIR/ids/$sid" || exit 0
    [[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || exit 0
    jq -r --arg n "$name" '
      def c($k): (. // "") | tostring | explode
        | map(select(. >= 32 and . != 127 and (. < 128 or . > 159) and (. < 8203 or . > 8207) and (. < 8232 or . > 8238) and (. < 8294 or . > 8297)))
        | implode | .[0:$k];
      (.sessions // {}) as $s | ($s[$n] // empty) as $me
      | ($me.scope | c(32)) as $sc
      | "fleet: you are \"\($n)\", scope \"\($sc)\", role \"\($me.role | c(40))\" (registry data)."
      , ( [ $s | to_entries[] | select(.value.scope == $me.scope and .key != $n) | "\(.key | c(64)) (\(.value.dir | c(200)))" ] as $p
          | if ($p | length) > 0 then "fleet: peers in scope \($sc): " + ($p | join("; ")) else empty end )
      , ( if ($me.tmux // "") != "" then "fleet: tmux session: \($me.tmux | split(":")[0] | c(40))"
          elif ($me.kind // "") == "job" and $sc != "unsorted" then "fleet: background job; its entry (the tmux session that views it) is \"\($sc)\""
          else empty end )
    ' "$DIR/registry.json" 2>/dev/null
    ;;
esac
exit 0
