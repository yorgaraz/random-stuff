#!/bin/sh

TERM_CLASS="Kitty"
TERM_TITLE="quake-term"
LAUNCH_CMD="kitty --config ~/.config/kitty/kitty.conf -o 'font_size=18' --class '$TERM_CLASS' --title '$TERM_TITLE' &"


if ! command -v jq &> /dev/null; then
    notify-send "jq is not installed" "Please install jq to use the toggle-quake script."
    exit 1
fi

if [ -z "$(hyprctl clients -j | jq ".[] | select(.class==\"$TERM_CLASS\" and .title==\"$TERM_TITLE\")")" ]; then
    eval "$LAUNCH_CMD"
    sleep 0.2
fi

WINDOW_INFO=$(hyprctl clients -j | jq ".[] | select(.class==\"$TERM_CLASS\" and .title==\"$TERM_TITLE\")")
WORKSPACE_ID=$(echo "$WINDOW_INFO" | jq '.workspace.id')

if [ "$WORKSPACE_ID" -lt 0 ]; then
    hyprctl dispatch togglespecialworkspace quaketerminal
else
    hyprctl dispatch movetoworkspace special:quaketerminal,title:"^$TERM_TITLE$"
fi
