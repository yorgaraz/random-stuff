#!/bin/sh

ACTIVE=$(hyprctl activewindow -j)
TITLE=$(echo "$ACTIVE" | jq -r '.title')
IS_FULLSCREEN=$(echo "$ACTIVE" | jq -r '.fullscreen')

if [ "$TITLE" = "quake-term" ]; then
    if [ "$IS_FULLSCREEN" = "0" ] || [ "$IS_FULLSCREEN" = "false" ]; then
        # Entering fullscreen: unpin first, then fullscreen
        hyprctl dispatch pin "title:^quake-term$"
        hyprctl dispatch fullscreen 0
    else
        # Exiting fullscreen: un-fullscreen, then repin
        hyprctl dispatch fullscreen 0
        hyprctl dispatch pin "title:^quake-term$"
    fi
else
    # For all other windows, just toggle fullscreen normally
    hyprctl dispatch fullscreen 0
fi
