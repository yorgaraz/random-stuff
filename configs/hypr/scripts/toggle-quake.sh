#!/bin/sh

TERM_CLASS="Kitty"
TERM_TITLE="quake-term"
LAUNCH_CMD="kitty --config ~/.config/kitty/kitty.conf -o 'font_size=18' --class '$TERM_CLASS' --title '$TERM_TITLE' &"

if ! command -v jq > /dev/null 2>&1; then
    notify-send "jq is not installed" "Please install jq to use the toggle-quake script."
    exit 1
fi

CLIENTS=$(hyprctl clients -j)
QUAKE_INFO=$(echo "$CLIENTS" | jq ".[] | select(.class==\"$TERM_CLASS\" and .title==\"$TERM_TITLE\")")

if [ -z "$QUAKE_INFO" ]; then
    eval "$LAUNCH_CMD"
    sleep 0.3
    ACTIVE_WORKSPACE_ID=$(hyprctl activeworkspace -j | jq '.id')
    hyprctl dispatch movetoworkspace "$ACTIVE_WORKSPACE_ID,title:^$TERM_TITLE$"
    hyprctl dispatch pin "title:^$TERM_TITLE$"
    hyprctl dispatch focuswindow "title:^$TERM_TITLE$"
    exit 0
fi

WORKSPACE_ID=$(echo "$QUAKE_INFO" | jq '.workspace.id')
IS_PINNED=$(echo "$QUAKE_INFO" | jq '.pinned')

if [ "$WORKSPACE_ID" -lt 0 ]; then
    # It is hidden. Bring it to the active workspace and pin it.
    ACTIVE_WORKSPACE_ID=$(hyprctl activeworkspace -j | jq '.id')
    hyprctl dispatch movetoworkspace "$ACTIVE_WORKSPACE_ID,title:^$TERM_TITLE$"
    if [ "$IS_PINNED" = "false" ]; then
        hyprctl dispatch pin "title:^$TERM_TITLE$"
    fi
    hyprctl dispatch focuswindow "title:^$TERM_TITLE$"
else
    # It is visible. UNPIN it first, then hide it.
    if [ "$IS_PINNED" = "true" ]; then
        hyprctl dispatch pin "title:^$TERM_TITLE$"
    fi
    hyprctl dispatch movetoworkspacesilent "special:quaketerminal,title:^$TERM_TITLE$"
fi
