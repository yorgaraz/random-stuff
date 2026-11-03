#!/bin/sh
# Super+F11.
# Quake term focused:
#   1. term is drop-down size and an app under it is fullscreen -> take that app out of fullscreen
#   2. otherwise (incl. term already full size)                 -> grow / shrink the term
# Any other window: plain fullscreen toggle.

ACTIVE=$(hyprctl activewindow -j)
TITLE=$(echo "$ACTIVE" | jq -r '.title')

if [ "$TITLE" != "quake-term" ]; then
    hyprctl dispatch fullscreen 0
    exit 0
fi

TERM_H=$(echo "$ACTIVE" | jq '.size[1]')
MON_H=$(hyprctl monitors -j | jq '.[] | select(.focused) | .height/.scale | floor')
if [ "$TERM_H" -gt $((MON_H * 3 / 4)) ]; then
    # Full size covers the app, so F11 means the term itself.
    exec ~/.config/hypr/scripts/toggle-quake.sh resize
fi

WS=$(echo "$ACTIVE" | jq '.workspace.id')
# "address mode" of the fullscreen app on this workspace; client .fullscreen: 1 = maximized, 2 = fullscreen.
UNDER=$(hyprctl clients -j | jq -r --argjson ws "$WS" \
    'first(.[] | select(.workspace.id == $ws and .fullscreen > 0 and .title != "quake-term")) | "\(.address) \(.fullscreen)"')

if [ -n "$UNDER" ]; then
    set -- $UNDER
    MODE=0
    [ "$2" = "1" ] && MODE=1   # the fullscreen dispatcher toggles off when given the window's current mode
    hyprctl --batch "dispatch focuswindow address:$1; dispatch fullscreen $MODE; dispatch focuswindow title:^quake-term\$" > /dev/null
else
    ~/.config/hypr/scripts/toggle-quake.sh resize
fi
