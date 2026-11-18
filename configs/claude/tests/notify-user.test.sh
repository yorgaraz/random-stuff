#!/usr/bin/env bash
# Tests for the notification lanes: notify-user, notify-hook.sh, mako/fullscreen-watch.
# Plain bash, no arguments, exit 0 = pass. Everything runs against stub notify-send / makoctl / tmux / hyprctl /
# sound players on a temp PATH and a temp state dir: nothing real is sent.
set -u
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
NY="$ROOT/configs/claude/bin/notify-user"
HOOK="$ROOT/configs/claude/hooks/notify-hook.sh"
WATCH="$ROOT/configs/mako/fullscreen-watch"

T=$(mktemp -d "${TMPDIR:-/tmp}/notify-user-test.XXXXXX")
BG=()
cleanup() { local p; for p in "${BG[@]:-}"; do [[ -n $p ]] && kill "$p" 2>/dev/null; done; rm -rf "$T"; }
trap cleanup EXIT

STUBS="$T/stubs"; mkdir -p "$STUBS" "$T/run"
export NOTIFY_USER_DIR="$T/state" XDG_RUNTIME_DIR="$T/run" FULLSCREEN_WATCH_PIDFILE="$T/run/watch.pid"
export HYPRLAND_INSTANCE_SIGNATURE=test-sig DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
export LOG="$T/log" MAKO_MODES="$T/mako-modes" NOTIFY_USER_TMUX="$STUBS/tmux"
export PANES="$T/panes" HYPR_MONITORS="$T/monitors.json" HYPR_CLIENTS="$T/clients.json"
export FULLSCREEN_IGNORE_FILE="$T/ignore" FULLSCREEN_WATCH_GENTLE="$STUBS/gentle-stub" GAME=0
export NOTIFY_USER_QUIET="$ROOT/configs/mako/quiet"
export NOTIFY_USER_BIN="$NY" FULLSCREEN_WATCH="$STUBS/watcher-stub"
unset NOTIFY_HOOK_TTY
: > "$LOG"; : > "$MAKO_MODES"; : > "$PANES"

# ---- stubs ----
cat > "$STUBS/notify-send" <<'S'
#!/usr/bin/env bash
# logs "notify-send<TAB>arg<TAB>arg..."; with -p prints an id (the -r id when replacing, else a counter)
rid="" p=0 args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [[ ${args[i]} == -p ]] && p=1
  [[ ${args[i]} == -r ]] && rid=${args[i+1]}
done
{ printf 'notify-send'; printf '\t%s' "$@"; printf '\n'; } >> "$LOG"
if (( p )); then
  if [[ -n $rid ]]; then echo "$rid"; else n=$(( $(cat "$LOG.id" 2>/dev/null || echo 100) + 1 )); echo $n > "$LOG.id"; echo $n; fi
fi
S
cat > "$STUBS/makoctl" <<'S'
#!/usr/bin/env bash
echo "makoctl $*" >> "$LOG"
[[ -n ${MAKO_FAIL:-} ]] && exit 1
if [[ ${1:-} == mode ]]; then
  case "${2:-}" in
    -a) grep -qx "$3" "$MAKO_MODES" || echo "$3" >> "$MAKO_MODES" ;;
    -t) if grep -qx "$3" "$MAKO_MODES"; then grep -vx "$3" "$MAKO_MODES" > "$MAKO_MODES.n"; mv "$MAKO_MODES.n" "$MAKO_MODES"; else echo "$3" >> "$MAKO_MODES"; fi ;;
    -r) grep -vx "$3" "$MAKO_MODES" > "$MAKO_MODES.n"; mv "$MAKO_MODES.n" "$MAKO_MODES" ;;
    *)  echo default; cat "$MAKO_MODES" ;;
  esac
fi
exit 0
S
cat > "$STUBS/tmux" <<'S'
#!/usr/bin/env bash
echo "tmux $*" >> "$LOG"
case "${1:-}" in
  list-panes) cat "$PANES" ;;
  list-clients) echo "/dev/pts/9 work" ;;
esac
exit 0
S
cat > "$STUBS/hyprctl" <<'S'
#!/usr/bin/env bash
[[ -n ${HYPR_FAIL:-} ]] && exit 1
case "$1" in monitors) cat "$HYPR_MONITORS" ;; clients) cat "$HYPR_CLIENTS" ;; *) exit 1 ;; esac
S
for p in paplay pw-play; do printf '#!/usr/bin/env bash\necho "%s $*" >> "$LOG"\n' "$p" > "$STUBS/$p"; done
printf '#!/usr/bin/env bash\necho "need-attention $#:$1|$2" >> "$LOG"\n' > "$STUBS/need-attention"
printf '#!/usr/bin/env bash\necho "pkill $*" >> "$LOG"\n' > "$STUBS/pkill"
printf '#!/usr/bin/env bash\necho "watcher $*" >> "$LOG"\n' > "$STUBS/watcher-stub"
# gentle --game stand-in: exit 3 when $GAME=1; counts its calls
printf '#!/usr/bin/env bash\necho "gentle $*" >> "$LOG"\n[[ ${GAME:-0} == 1 ]] && exit 3\nexit 0\n' > "$STUBS/gentle-stub"
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"

# ---- tiny harness ----
PASS=0 FAIL=0
ok()   { PASS=$((PASS + 1)); }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }
check() { # check <name> <expected> <actual>
  if [[ $2 == "$3" ]]; then ok; else bad "$1: expected [$2] got [$3]"; fi
}
logcount() { grep -c -e "$1" "$LOG"; }
reset() { rm -rf "$NOTIFY_USER_DIR"; : > "$LOG"; rm -f "$LOG.id"; : > "$MAKO_MODES"; : > "$PANES"; rm -f "$FULLSCREEN_WATCH_PIDFILE"; unset HYPR_FAIL MAKO_FAIL; }
# a live process whose command line says fullscreen-watch, so ensure_watcher sees a healthy daemon
fake_watcher() { bash -c 'exec -a fullscreen-watch-fake sleep 120' & FW=$!; BG+=("$FW"); echo "$FW" > "$FULLSCREEN_WATCH_PIDFILE"; }

# ================= notify-user =================
reset; fake_watcher
"$NY" needs alpha "first" ; "$NY" needs beta "other"
check "needs: two sessions, two notifications" 2 "$(logcount '^notify-send')"
check "needs: count 2" " ● 2 " "$("$NY" --count)"
"$NY" needs alpha "second"
check "needs: repeat reuses the stored id (-r 101)" 1 "$(grep -ce $'^notify-send\t-p\t-a\tclaude-needs\t-u\tnormal\t-r\t101\t' "$LOG")"
check "needs: still two entries after a repeat" " ● 2 " "$("$NY" --count)"
check "needs: lane app name and urgency" 3 "$(grep -ce $'-a\tclaude-needs\t-u\tnormal' "$LOG")"
check "needs: summary names the session" 1 "$(grep -ce $'\talpha needs you\tfirst' "$LOG")"
[[ $(stat -c %a "$NOTIFY_USER_DIR") == 700 ]] && ok || bad "state dir mode is not 700"
check "needs: sound only when the session was not already waiting (alpha, beta; alpha's new text is silent)" 2 "$(logcount '^pw-play')"
"$NY" needs alpha "second"
check "needs: identical repeat is silent" 2 "$(logcount '^pw-play')"
"$NY" --list | grep -q $'alpha\tsecond' && ok || bad "--list misses alpha"
check "--list: two lines" 2 "$("$NY" --list | wc -l)"

# clear
: > "$LOG"
"$NY" clear alpha; rc=$?
check "clear: rc 0" 0 $rc
check "clear: dismisses the stored id" 1 "$(grep -c '^makoctl dismiss -h -n 101$' "$LOG")"
check "clear: entry dropped" " ● 1 " "$("$NY" --count)"
: > "$LOG"
out=$("$NY" clear alpha 2>&1); rc=$?
check "clear: nothing waiting is silent" "" "$out"
check "clear: nothing waiting rc 0" 0 $rc
check "clear: nothing waiting makes no calls" 0 "$(wc -l < "$LOG")"
"$NY" clear beta; "$NY" clear beta
check "count 0 prints nothing" "" "$("$NY" --count)"
check "count with no state dir prints nothing" "" "$(NOTIFY_USER_DIR="$T/none" "$NY" --count)"

# info
reset; fake_watcher
"$NY" info test "just fyi"
check "info: low urgency, own app name" 1 "$(grep -ce $'-a\tclaude-info\t-u\tlow' "$LOG")"
check "info: nothing stored" "" "$("$NY" --count)"
check "info: no sound" 0 "$(logcount '^pw-play')"

# sound is off in fullscreen mode
reset; fake_watcher; echo fullscreen > "$MAKO_MODES"
"$NY" needs gamer "wait"
check "needs in fullscreen: notification still sent" 1 "$(logcount '^notify-send')"
check "needs in fullscreen: no sound" 0 "$(logcount '^pw-play')"

reset; fake_watcher; echo quiet > "$MAKO_MODES"
"$NY" needs gamer "wait"
check "needs in forced quiet: notification still sent" 1 "$(logcount '^notify-send')"
check "needs in forced quiet: no sound" 0 "$(logcount '^pw-play')"

# sanitising
reset; fake_watcher
long=$(printf 'x%.0s' $(seq 1 1000))
"$NY" needs $'esc\e[31m\x07name' $'line1\nline2\e]0;evil\x07 tail\x01'
if grep -q $'\e' "$LOG"; then bad "escape character survived"; else ok; fi
check "control characters stripped from text" 1 "$(grep -c 'line1line2]0;evil tail$' "$LOG")"
check "control characters stripped from name" 1 "$(grep -c 'esc\[31mname needs you' "$LOG")"
"$NY" needs longone "$long"
check "text capped at 240" 240 "$(grep 'longone needs you' "$LOG" | awk -F'\t' '{print length($NF)}')"
"$NY" needs "$long" "long name"
check "name capped at 40" 1 "$(grep -ce $'\t'"$(printf 'x%.0s' $(seq 1 40))"' needs you' "$LOG")"
inj="a\"b'c\$(touch $T/pwned)\`touch $T/pwned2\`;touch $T/pwned3"
"$NY" needs "$inj" "msg \$(touch $T/pwned4) \"q\" 'q'"
"$NY" clear "$inj"
"$NY" info "$inj" "x"
"$NY" urgent "$inj" "x"
sleep 0.2
check "no command injection through name or text" 0 "$(ls "$T" | grep -c '^pwned')"
check "quoted name stored and cleared as one entry" "" "$("$NY" --count | grep -v ' ● [23] ')"
reset; fake_watcher
"$NY" needs mk 'a <b>bold</b> & "q"'
check "pango markup escaped" 1 "$(grep -c 'a &lt;b&gt;bold&lt;/b&gt; &amp; "q"$' "$LOG")"
"$NY" needs 2>/dev/null && bad "missing args should fail" || ok
"$NY" frobnicate a b 2>/dev/null; check "unknown command rc 2" 2 $?

# urgent
reset; fake_watcher
"$NY" urgent prod $'down\e[0m now'
check "urgent hands over to need-attention with cleaned args" 1 "$(grep -c '^need-attention 2:prod|down\[0m now$' "$LOG")"
check "urgent sends no mako notification itself" 0 "$(logcount '^notify-send')"

# watcher health: dead or missing watcher -> --sync before sending
reset
"$NY" info a b
check "no pidfile: runs fullscreen-watch --sync" 1 "$(logcount '^watcher --sync')"
echo 999999 > "$FULLSCREEN_WATCH_PIDFILE"; : > "$LOG"
"$NY" needs a b
check "stale pidfile: runs --sync" 1 "$(logcount '^watcher --sync')"
fake_watcher; : > "$LOG"
"$NY" needs a c
check "live watcher: no --sync" 0 "$(logcount '^watcher --sync')"

# --open / --show
reset; fake_watcher
NOTIFY_USER_TARGET=work:3 "$NY" needs alpha "q?"
: > "$LOG"
"$NY" --open /dev/pts/9; sleep 0.3
check "--open: popup on the asked client" 1 "$(grep -c '^tmux display-popup -c /dev/pts/9 ' "$LOG")"
: > "$LOG"
printf '1' | "$NY" --show /dev/pts/9 >/dev/null 2>&1
check "--show: a number switches the client to the window" 1 "$(grep -c '^tmux switch-client -c /dev/pts/9 -t work:3$' "$LOG")"
: > "$LOG"
printf 'q' | "$NY" --show /dev/pts/9 >/dev/null 2>&1
check "--show: q switches nothing" 0 "$(logcount '^tmux switch-client')"

# ================= notify-hook.sh =================
ev() { # ev <event> <cwd> [message]
  jq -nc --arg e "$1" --arg c "$2" --arg m "${3:-}" '{session_id:"s1",cwd:$c,hook_event_name:$e} + (if $m != "" then {message:$m} else {} end)'
}
reset; fake_watcher
printf '/dev/pts/77\tmain:4\tmy-window\n/dev/pts/78\trcterm-x:1\ttwin\n' > "$PANES"
export NOTIFY_HOOK_TTY=/dev/pts/77
out=$(ev Notification /w/proj "Claude needs your permission" | "$HOOK" 2>&1); rc=$?
check "hook: no output" "" "$out"
check "hook: exit 0" 0 $rc
check "hook Notification -> needs <window name> <message>" 1 "$(grep -ce $'\tmy-window needs you\tClaude needs your permission$' "$LOG")"
check "hook Notification: target stored for --open" 1 "$(grep -ce $'\tmain:4\t' "$NOTIFY_USER_DIR"/needs/s-my-window)"
: > "$LOG"
ev UserPromptSubmit /w/proj | "$HOOK"
check "hook UserPromptSubmit -> clear (dismiss)" 1 "$(grep -c '^makoctl dismiss -h -n 101$' "$LOG")"
check "hook: entry gone" "" "$("$NY" --count)"
NOTIFY_HOOK_TTY=/dev/pts/77 ev Notification /w/proj "again" | "$HOOK"
: > "$LOG"
ev PostToolUse /w/proj | "$HOOK"
check "hook PostToolUse -> clear (dismiss)" 1 "$(grep -c '^makoctl dismiss' "$LOG")"
# fallback to the basename of cwd when no pane matches
export NOTIFY_HOOK_TTY=/dev/pts/12345
: > "$LOG"
ev Notification /w/some-project "hi" | "$HOOK"
check "hook: no pane match -> cwd basename" 1 "$(grep -ce $'\tsome-project needs you\thi$' "$LOG")"
ev PostToolUse /w/some-project | "$HOOK"
check "hook: clear by the same cwd name" "" "$("$NY" --count)"
export NOTIFY_HOOK_TTY=/dev/pts/78   # a rcterm twin is never used as a name
: > "$LOG"; ev Notification /w/fallback "x" | "$HOOK"
check "hook: rcterm-* panes are skipped" 1 "$(grep -ce $'\tfallback needs you\t' "$LOG")"
"$NY" clear fallback
export NOTIFY_HOOK_TTY=/dev/pts/77
# the fast path: nothing waiting -> the sender is not even started, and the hook is quick
reset; mkdir -p "$T/empty"
export NOTIFY_USER_BIN="$STUBS/watcher-stub"   # any call to the sender would show up as 'watcher ...'
: > "$LOG"
ev PostToolUse /w/x | "$HOOK"; ev UserPromptSubmit /w/x | "$HOOK"
check "hook: clear with nothing waiting does nothing" 0 "$(wc -l < "$LOG")"
start=$(date +%s%N); for _ in $(seq 1 20); do ev PostToolUse /w/x | "$HOOK"; done; ms=$(( ($(date +%s%N) - start) / 20000000 ))
# 'ev' itself costs a jq per round; the bound is loose on purpose
(( ms < 60 )) && ok || bad "hook fast path too slow: ${ms} ms per call"
export NOTIFY_USER_BIN="$NY"
for junk in '' 'not json' '{"hook_event_name":"Stop"}' '{"hook_event_name":"Notification"}'; do
  out=$(printf '%s' "$junk" | "$HOOK" 2>&1); rc=$?
  check "hook: junk input [$junk] quiet and exit 0" "0:" "$rc:$out"
done
NOTIFY_USER_BIN=/nonexistent/bin; export NOTIFY_USER_BIN
out=$(ev Notification /w/x hi | "$HOOK" 2>&1); check "hook: missing sender still exit 0, quiet" ":0" "$out:$?"
export NOTIFY_USER_BIN="$NY"

# ================= fullscreen-watch --sync =================
mon() { printf '[{"name":"HDMI-A-1","activeWorkspace":{"id":%s,"name":"%s"},"specialWorkspace":{"id":%s,"name":""}}]' "$1" "$1" "${2:-0}" > "$HYPR_MONITORS"; }
# cl <class:fullscreen:workspace-id>...  -> the hyprctl clients -j stub
cl() { local a c f w; for a in "$@"; do IFS=: read -r c f w <<<"$a"; printf '{"class":"%s","fullscreen":%s,"workspace":{"id":%s},"mapped":true}\n' "$c" "$f" "$w"; done | jq -s . > "$HYPR_CLIENTS"; }
sync_() { python3 -I "$WATCH" --sync; }
mode() { cat "$MAKO_MODES"; }
printf '# everyday\n\n^chromium$\n' > "$T/ignore"
reset; GAME=0
mon 1
cl kitty:0:1 chromium:2:1
sync_
check "sync: Chromium fullscreen on the visible workspace -> off" "" "$(mode)"
cl kitty:0:1 chromium:2:1 mpv:2:1
sync_
check "sync: another class fullscreen -> on" "fullscreen" "$(mode)"
: > "$LOG"; sync_
check "sync: no change -> makoctl mode not called to change" 0 "$(logcount 'makoctl mode -[ar]')"
cl kitty:0:1 Chromium:2:1
sync_
check "sync: fullscreen ended (class match is case-insensitive) -> off" "" "$(mode)"
: > "$LOG"; sync_
check "sync: still off -> no change call" 0 "$(logcount 'makoctl mode -[ar]')"
cl kitty:0:1 mpv:2:2
sync_
check "sync: fullscreen on a hidden workspace does not count" "" "$(mode)"
mon 1 -98; cl kitty:0:1 mpv:2:-98
sync_
check "sync: fullscreen on a shown special workspace counts" "fullscreen" "$(mode)"
mon 1; cl kitty:1:1 ; : > "$MAKO_MODES"
# a pinned float (quake terminal) has fullscreen 0
cl kitty:0:1; sync_
check "sync: pinned float is not fullscreen" "" "$(mode)"
# the game rule
GAME=1 cl chromium:2:1; GAME=1 sync_
check "sync: Chromium fullscreen plus a game -> on" "fullscreen" "$(mode)"
cl kitty:0:1; : > "$MAKO_MODES"; GAME=1 sync_
check "sync: no fullscreen plus a game -> on" "fullscreen" "$(mode)"
GAME=0 sync_
check "sync: game gone -> off" "" "$(mode)"
check "sync: gentle --game is what is asked" 1 "$(grep -c '^gentle --game$' "$LOG" | awk '{print ($1>0)}')"
# the ignore file
cl chromium:2:1
: > "$T/ignore"; sync_
check "sync: empty ignore file, Chromium fullscreen -> on" "fullscreen" "$(mode)"
: > "$MAKO_MODES"; rm -f "$T/ignore"; sync_
check "sync: missing ignore file, Chromium fullscreen -> on" "fullscreen" "$(mode)"
printf '([unclosed\n*bad\n^chromium$\n' > "$T/ignore"
: > "$MAKO_MODES"; sync_; rc=$?
check "sync: bad regex lines do not crash (rc 0)" 0 $rc
check "sync: bad regex lines skipped, good line still applies -> off" "" "$(mode)"
printf '([unclosed\n' > "$T/ignore"; echo fullscreen > "$MAKO_MODES"; cl kitty:0:1; sync_
check "sync: only a bad regex, nothing fullscreen -> mode not stuck" "" "$(mode)"
printf '^chromium$\n' > "$T/ignore"
# maximize (state 1) is not fullscreen
: > "$MAKO_MODES"; cl mpv:1:1; sync_
check "sync: maximized non-ignored window -> off" "" "$(mode)"
cl mpv:3:1; sync_
check "sync: fullscreen state 3 -> on" "fullscreen" "$(mode)"
# the forced quiet mode is none of the watcher's business
printf 'quiet\n' > "$MAKO_MODES"; : > "$LOG"; cl kitty:0:1; sync_
check "sync: quiet stays on when nothing is fullscreen" "quiet" "$(mode)"
cl mpv:2:1; sync_
check "sync: quiet stays, fullscreen added" "quiet:fullscreen" "$(mode | paste -sd:)"
cl kitty:0:1; sync_
check "sync: fullscreen removed, quiet stays" "quiet" "$(mode)"
check "sync: watcher never addressed quiet" 0 "$(logcount 'makoctl mode -[art] quiet')"
echo quiet > "$MAKO_MODES"; HYPR_FAIL=1 sync_
check "sync: hyprctl failing leaves quiet alone" "quiet" "$(mode)"
: > "$MAKO_MODES"
# gentle missing = no game
FULLSCREEN_WATCH_GENTLE=/nonexistent/gentle python3 -I "$WATCH" --sync; rc=$?
check "sync: gentle missing -> rc 0, no game, mode off" "0:" "$rc:$(mode)"
# the 10 s cache: one gentle call for a burst
: > "$LOG"
python3 -I - "$WATCH" <<'PY'
import importlib.machinery, importlib.util, sys
sys.dont_write_bytecode = True
l = importlib.machinery.SourceFileLoader("fw", sys.argv[1]); sp = importlib.util.spec_from_loader("fw", l)
m = importlib.util.module_from_spec(sp); l.exec_module(m)
for _ in range(5): m.game_running()
PY
check "game check cached: 5 asks, 1 gentle call" 1 "$(logcount '^gentle --game')"
# never stuck on
echo fullscreen > "$MAKO_MODES"; HYPR_FAIL=1 GAME=1 sync_
check "sync: hyprctl fails -> stuck mode removed (even with a game)" "" "$(mode)"
echo fullscreen > "$MAKO_MODES"; echo 'garbage' > "$HYPR_MONITORS"; sync_
check "sync: unparsable hyprctl output -> mode removed" "" "$(mode)"
mon 1; echo fullscreen > "$MAKO_MODES"; echo 'garbage' > "$HYPR_CLIENTS"; sync_
check "sync: unparsable clients output -> mode removed" "" "$(mode)"
mon 1; echo fullscreen > "$MAKO_MODES"
mkdir -p "$T/nohypr"; ln -sf "$STUBS/makoctl" "$T/nohypr/makoctl"
for c in bash env grep cat mv; do ln -sf "$(command -v $c)" "$T/nohypr/$c"; done
PATH="$T/nohypr" /usr/bin/python3 -I "$WATCH" --sync
check "sync: hyprctl missing -> mode removed" "" "$(mode)"

# ================= quiet (waybar bell) =================
Q="$ROOT/configs/mako/quiet"
reset
"$Q" toggle; check "quiet toggle: mode -t quiet" 1 "$(logcount '^makoctl mode -t quiet$')"
check "quiet toggle: waybar signalled (RTMIN+10)" 1 "$(logcount '^pkill -RTMIN+10 -x waybar$')"
check "quiet toggle: on" "quiet" "$(mode)"
"$Q" toggle; check "quiet toggle again: off" "" "$(mode)"
"$Q" on; check "quiet on: mode -a quiet" 1 "$(logcount '^makoctl mode -a quiet$')"
"$Q" off; check "quiet off: mode -r quiet" 1 "$(logcount '^makoctl mode -r quiet$')"
check "quiet on/off/toggle: no notification sent" 0 "$(logcount '^notify-send')"
check "quiet never touches fullscreen" 0 "$(logcount 'mode -[art] fullscreen')"
check "quiet: one signal per toggle, on, off" 4 "$(logcount '^pkill')"
st() { "$Q" status | jq -r '.class'; }
: > "$MAKO_MODES";                       check "status: off" off "$(st)"
echo quiet > "$MAKO_MODES";              check "status: forced" forced "$(st)"
echo fullscreen > "$MAKO_MODES";         check "status: auto" auto "$(st)"
printf 'quiet\nfullscreen\n' > "$MAKO_MODES"; check "status: both -> forced" forced "$(st)"
check "status: JSON has text and tooltip" "true" "$("$Q" status | jq '(.text|length>0) and (.tooltip|test("Click: toggle forced quiet"))')"
MAKO_FAIL=1; export MAKO_FAIL
out=$("$Q" status); rc=$?
check "status: makoctl failing -> exit 0, class off, valid JSON" "0:off" "$rc:$(jq -r .class <<<"$out")"
"$Q" toggle; check "toggle: makoctl failing -> exit 0" 0 $?
"$Q" is-quiet; check "is-quiet: makoctl failing -> 1" 1 $?
unset MAKO_FAIL
: > "$MAKO_MODES";                       "$Q" is-quiet; check "is-quiet: none -> 1" 1 $?
echo quiet > "$MAKO_MODES";              "$Q" is-quiet; check "is-quiet: quiet -> 0" 0 $?
echo fullscreen > "$MAKO_MODES";         "$Q" is-quiet; check "is-quiet: fullscreen -> 0" 0 $?
echo other > "$MAKO_MODES";              "$Q" is-quiet; check "is-quiet: unrelated mode -> 1" 1 $?
"$Q" bogus >/dev/null 2>&1;              check "quiet: unknown arg -> rc 2" 2 $?
: > "$MAKO_MODES"

# ================= gentle --game (stubbed process list) =================
GENTLE="$ROOT/configs/claude/bin/gentle"
mkdir -p "$T/psbin"
cat > "$T/psbin/ps" <<'S'
#!/usr/bin/env bash
cat "$PS_LIST"
S
chmod +x "$T/psbin/ps"
export PS_LIST="$T/ps-list"
printf '  100 firefox /usr/lib/firefox/firefox\n  200 bash bash -c sleep 1\n' > "$PS_LIST"
out=$(PATH="$T/psbin:$PATH" "$GENTLE" --game); rc=$?
check "gentle --game: no game -> exit 0, no output" "0:" "$rc:$out"
printf '  100 firefox /usr/lib/firefox/firefox\n  300 GTA5.exe /opt/steam/steamapps/common/GTA5/GTA5.exe\n' > "$PS_LIST"
out=$(PATH="$T/psbin:$PATH" "$GENTLE" --game); rc=$?
check "gentle --game: a game runs -> exit 3, no output" "3:" "$rc:$out"

# ================= fullscreen-watch daemon (fake Hyprland socket) =================
reset
SIGDIR="$XDG_RUNTIME_DIR/hypr/test-sig"; mkdir -p "$SIGDIR"
mon 1; cl kitty:0:1
# fake event socket: one event on connect, another whenever the file "trigger" appears
/usr/bin/python3 -I - "$SIGDIR/.socket2.sock" "$T/trigger" <<'PY' &
import os, socket, sys, time
s = socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen(1)
c, _ = s.accept()
c.send(b"workspace>>1\n")
for _ in range(400):
    if os.path.exists(sys.argv[2]):
        os.unlink(sys.argv[2]); c.send(b"fullscreen>>1\n")
    time.sleep(0.05)
PY
SRV=$!; BG+=("$SRV")
sleep 0.3
python3 -I "$WATCH" & DPID=$!; BG+=("$DPID")
sleep 0.8
check "daemon: pidfile written" "$DPID" "$(cat "$FULLSCREEN_WATCH_PIDFILE" 2>/dev/null)"
check "daemon: not fullscreen at start" "" "$(cat "$MAKO_MODES")"
cl kitty:0:1 mpv:2:1; touch "$T/trigger"; sleep 0.8
check "daemon: fullscreen event -> mode on" "fullscreen" "$(cat "$MAKO_MODES")"
kill "$SRV"; sleep 0.8
check "daemon: event socket gone -> mode off, never stuck" "" "$(cat "$MAKO_MODES")"
echo fullscreen > "$MAKO_MODES"
kill -TERM "$DPID"; sleep 0.5
check "daemon: SIGTERM removes the mode" "" "$(cat "$MAKO_MODES")"
[[ -e $FULLSCREEN_WATCH_PIDFILE ]] && bad "daemon: pidfile left behind" || ok

echo
if (( FAIL == 0 )); then echo "notify-user tests: $PASS passed, 0 failed"; exit 0; fi
echo "notify-user tests: $PASS passed, $FAIL FAILED"; exit 1
