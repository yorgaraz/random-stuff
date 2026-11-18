#!/usr/bin/env bash
# Fake `claude` for fleet.test.sh. Logs argv + cwd + the CLAUDE* variables it sees to $FAKE_DIR/claude.log and
# plays the parts fleet needs, with fixture files under $FAKE_DIR/claude (= FLEET_CLAUDE_HOME):
#   --bg --resume <sid> -n <name> --model <m> [--effort e]   start a "job": job row + live session file
#   respawn <job>                                            replace the job's process (new pid)
#   stop <job>                                               end the process, remove its session file, row state "stopped" (or "done" if it was done); FAKE_STOP_FAIL
#   rm <job>                                                 remove the job row (refused while its process lives)
#   attach <job>                                             next mode of $FAKE_DIR/attach.seq: respawn | detach | stop
# Job states written are the real ones (checked against real jobs/*/state.json): working, done, blocked, stopped. A freshly
# started job is "working" (FAKE_JOB_STATE overrides); `stop` leaves "stopped". Session files carry procStart (field 22 of
# /proc/<pid>/stat) and pidDomain ($FLEET_PIDDOMAIN) like the real ones.
# Failure switches (env): FAKE_BG_FAIL, FAKE_BG_RENAME, FAKE_BG_NEWID, FAKE_BG_NOROW, FAKE_BG_NOPROC, FAKE_RESPAWN_FAIL
H="$FAKE_DIR/claude"
vars=$(env | grep -c '^CLAUDE')
printf 'claude\tcwd=%s\tclaudevars=%s\targc=%s\t%s\n' "$PWD" "$vars" "$#" "$*" >> "$FAKE_DIR/claude.log"

spawn() { # spawn <job> <sid> <name> <dir> : kills the old process of the job, starts a new one, writes the session file
  local job=$1 sid=$2 name=$3 dir=$4 f pid
  for f in "$H"/sessions/*.json; do
    [[ -e $f ]] || continue
    [[ $(jq -r '.jobId // empty' "$f") == "$job" ]] && { kill -9 "$(jq -r .pid "$f")" 2>/dev/null; rm -f "$f"; }
  done
  setsid sleep 600 >/dev/null 2>&1 & pid=$!
  LAST_PID=$pid
  echo "$pid" >> "$FAKE_DIR/pids"
  [[ -n ${FAKE_BG_NOPROC:-} ]] && { kill -9 "$pid"; return; }
  start=$(sed 's/.*) //' "/proc/$pid/stat" | awk '{print $20}')
  sock="$H/sock-$pid"
  [[ -n ${FAKE_NOSOCK:-} ]] || : > "$sock"   # the messaging socket (a plain file here); FAKE_NOSOCK: it never appears
  jq -n --argjson pid "$pid" --arg sid "$sid" --arg cwd "$dir" --arg name "$name" --arg job "$job" --arg start "$start" --arg dom "${FLEET_PIDDOMAIN:-}" --arg sock "$sock" \
    '{pid:$pid,sessionId:$sid,cwd:$cwd,kind:"bg",name:$name,jobId:$job,status:"idle",procStart:$start,messagingSocketPath:$sock} + (if $dom != "" then {pidDomain:$dom} else {} end)' > "$H/sessions/$pid.json"
}

# FAKE_REQUIRE_CONV: as the real daemon, a start whose session id has no conversation file (projects/*/<id>.jsonl) looks fine
# at first and settles as crashed shortly after: `bg settled <job> (crashed): source session <id> not found`
maybe_crash() { # maybe_crash <job> <sid>
  if [[ -z ${FAKE_CRASH_ALWAYS:-} ]]; then
    [[ -n ${FAKE_REQUIRE_CONV:-} ]] || return 0
    compgen -G "$H/projects/*/$2.jsonl" >/dev/null && return 0
  fi
  local pid=$LAST_PID f=$H/sessions/$LAST_PID.json
  ( sleep "${FAKE_CRASH_DELAY:-0.6}"; kill -9 "$pid" 2>/dev/null; rm -f "$f"
    printf '[%s] [bg] bg settled %s (crashed): source session %s not found\n' "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" "$1" "$2" >> "$H/daemon.log" ) >/dev/null 2>&1 &
}

case "$1" in
  --bg)
    [[ -n ${FAKE_BG_FAIL:-} ]] && { echo "fake: bg failed" >&2; exit 1; }
    [[ -n ${FAKE_BG_SLEEP:-} ]] && sleep "$FAKE_BG_SLEEP"
    sid="" name="" model="" effort="" perm=""
    shift
    while (( $# )); do
      case "$1" in --resume) sid=$2; shift ;; -n) name=$2; shift ;; --model) model=$2; shift ;; --effort) effort=$2; shift ;; --permission-mode) perm=$2; shift ;; esac
      shift
    done
    [[ -n ${FAKE_UNTRUSTED:-} ]] && { echo "Workspace not trusted. Run \`claude\` in $PWD once and accept the trust prompt, then retry." >&2; exit 1; }
    [[ -z $sid ]] && sid=$(cat /proc/sys/kernel/random/uuid)   # a first start (no --resume): a new session
    [[ -n ${FAKE_BG_NEWID:-} ]] && sid="11111111-2222-3333-4444-555555555555"
    [[ -n ${FAKE_BG_RENAME:-} ]] && name="renamed-by-fake"
    job=${sid:0:8}
    if [[ -z ${FAKE_BG_NOROW:-} ]]; then
      mkdir -p "$H/jobs/$job"
      jq -n --arg sid "$sid" --arg name "$name" --arg cwd "$PWD" --arg model "${FAKE_BG_MODEL:-$model}" --arg effort "$effort" --arg perm "$perm" --arg st "${FAKE_JOB_STATE:-working}" \
        '{state:$st,name:$name,sessionId:$sid,cwd:$cwd,createdAt:"2026-11-16T10:00:00Z",respawnFlags:(["--model",$model]+(if $effort!="" then ["--effort",$effort] else [] end)+(if $perm!="" then ["--permission-mode",$perm] else [] end))}' > "$H/jobs/$job/state.json"
    fi
    spawn "$job" "$sid" "$name" "$PWD"
    maybe_crash "$job" "$sid"
    echo "backgrounded $job"
    ;;
  stop)
    [[ -n ${FAKE_STOP_FAIL:-} ]] && { echo "fake: stop failed" >&2; exit 1; }
    job=$2
    [[ -n ${FAKE_STOP_FAIL_JOB:-} && $job == "$FAKE_STOP_FAIL_JOB" ]] && { echo "fake: stop failed for $job" >&2; exit 1; }
    for f in "$H"/sessions/*.json; do
      [[ -e $f ]] || continue
      [[ $(jq -r '.jobId // empty' "$f") == "$job" ]] && { kill -9 "$(jq -r .pid "$f")" 2>/dev/null; rm -f "$f"; }
    done
    # as the real one (checked live): a stop of a working or blocked job leaves the state "stopped", a stop of an idle job
    # leaves "done"; the session file is removed and the daemon logs `settled <job> (killed)`
    [[ -f $H/jobs/$job/state.json ]] && { jq 'if .state == "done" then . else .state="stopped" end | .needs=null' "$H/jobs/$job/state.json" > "$H/jobs/$job/s.tmp" && mv "$H/jobs/$job/s.tmp" "$H/jobs/$job/state.json"; }
    echo "[2026-11-16T10:00:00.000Z] [bg] bg settled $job (killed)" >> "$H/daemon.log"
    echo "stopped $job"
    ;;
  rm)
    job=$2
    for f in "$H"/sessions/*.json; do
      [[ -e $f ]] || continue
      if [[ $(jq -r '.jobId // empty' "$f") == "$job" ]]; then
        pid=$(jq -r .pid "$f"); [[ -d /proc/$pid ]] && { echo "fake: not safe: still running" >&2; exit 1; }
      fi
    done
    rm -rf "$H/jobs/$job"; echo "removed $job"
    ;;
  respawn)
    [[ -n ${FAKE_RESPAWN_FAIL:-} ]] && { echo "fake: respawn failed" >&2; exit 1; }
    job=$2
    [[ -f $H/jobs/$job/state.json ]] || { echo "no such job" >&2; exit 1; }
    spawn "$job" "$(jq -r .sessionId "$H/jobs/$job/state.json")" "$(jq -r .name "$H/jobs/$job/state.json")" "$(jq -r .cwd "$H/jobs/$job/state.json")"
    maybe_crash "$job" "$(jq -r .sessionId "$H/jobs/$job/state.json")"
    echo "respawned $job"
    ;;
  attach)
    job=$2
    mode=$(head -n1 "$FAKE_DIR/attach.seq" 2>/dev/null); sed -i 1d "$FAKE_DIR/attach.seq" 2>/dev/null
    echo "attach $job mode=${mode:-detach}" >> "$FAKE_DIR/attach.log"
    if [[ $mode == respawn ]]; then
      spawn "$job" "$(jq -r .sessionId "$H/jobs/$job/state.json")" "$(jq -r .name "$H/jobs/$job/state.json")" "$(jq -r .cwd "$H/jobs/$job/state.json")"
    fi
    if [[ $mode == stop ]]; then kill -STOP $$; exit 0; fi   # Ctrl+Z inside the real client: it stops itself
    echo "Session $job has exited"
    ;;
  *) ;;
esac
exit 0
