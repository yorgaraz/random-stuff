#!/usr/bin/env bash

if hyprctl activewindow -j | jq -e '.tags | index("ghost")' > /dev/null; then
    hyprctl dispatch tagwindow -- -ghost
    notify-send "Ghost Mode: OFF" -t 1000
else
    hyprctl dispatch tagwindow +ghost
    notify-send "Ghost Mode: ON" -t 1000
fi
