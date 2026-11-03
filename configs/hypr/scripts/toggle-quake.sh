#!/bin/sh
# F12 drop-down terminal.
#   toggle-quake.sh          show / hide
#   toggle-quake.sh resize   flip drop-down (40%) <-> full screen; Super+F11 calls this
#
# "Full screen" here is a pinned float the size of the monitor, NOT Hyprland fullscreen.
# Real fullscreen is one-per-workspace, so any other window going fullscreen knocked the
# term out of it, and Hyprland then restored it centred on the mouse (off-screen).
# Pinned floats always render on top, fullscreen or not, so nothing can knock this.

TERM_CLASS="Kitty"
TERM_TITLE="quake-term"
SEL="title:^$TERM_TITLE\$"
DROP_PCT=40
LAUNCH_CMD="kitty --config ~/.config/kitty/kitty.conf -o 'font_size=18' --class '$TERM_CLASS' --title '$TERM_TITLE' &"

if ! command -v jq > /dev/null 2>&1; then
    notify-send "jq is not installed" "Please install jq to use the toggle-quake script."
    exit 1
fi

QUAKE_INFO=$(hyprctl clients -j | jq ".[] | select(.class==\"$TERM_CLASS\" and .title==\"$TERM_TITLE\")")

# Focused monitor as "x y w h" (logical px).
mon() {
    hyprctl monitors -j | jq -r '.[] | select(.focused) | "\(.x) \(.y) \(.width/.scale|floor) \(.height/.scale|floor)"'
}

# Current size class of the term: big | small.
size_class() {
    set -- $(mon)
    CUR_H=$(echo "$QUAKE_INFO" | jq '.size[1] // 0')
    if [ "${CUR_H:-0}" -gt $(($4 * 3 / 4)) ]; then echo big; else echo small; fi
}

# place big|small: snap to the focused monitor's top-left at that size, no animation.
place() {
    MODE=$1
    set -- $(mon)
    H=$4
    [ "$MODE" = big ] || H=$(($4 * DROP_PCT / 100))
    hyprctl --batch "keyword animations:enabled false; dispatch resizewindowpixel exact $3 $H,$SEL; dispatch movewindowpixel exact $1 $2,$SEL; keyword animations:enabled true" > /dev/null
}

if [ "$1" = "resize" ]; then
    [ -z "$QUAKE_INFO" ] && exit 0
    if [ "$(size_class)" = big ]; then place small; else place big; fi
    hyprctl dispatch focuswindow "$SEL" > /dev/null
    exit 0
fi

if [ -z "$QUAKE_INFO" ]; then
    eval "$LAUNCH_CMD"
    sleep 0.3
    ACTIVE_WORKSPACE_ID=$(hyprctl activeworkspace -j | jq '.id')
    hyprctl dispatch movetoworkspace "$ACTIVE_WORKSPACE_ID,$SEL"
    hyprctl dispatch pin "$SEL"
    place small
    hyprctl dispatch focuswindow "$SEL"
    exit 0
fi

WORKSPACE_ID=$(echo "$QUAKE_INFO" | jq '.workspace.id')
IS_PINNED=$(echo "$QUAKE_INFO" | jq '.pinned')

if [ "$WORKSPACE_ID" -lt 0 ]; then
    # Hidden: bring it to the active workspace, pin, snap back into place (keeps its size class).
    MODE=$(size_class)
    ACTIVE_WORKSPACE_ID=$(hyprctl activeworkspace -j | jq '.id')
    hyprctl dispatch movetoworkspace "$ACTIVE_WORKSPACE_ID,$SEL"
    if [ "$IS_PINNED" = "false" ]; then
        hyprctl dispatch pin "$SEL"
    fi
    place "$MODE"
    hyprctl dispatch focuswindow "$SEL"
else
    # Visible: unpin first, then hide.
    if [ "$IS_PINNED" = "true" ]; then
        hyprctl dispatch pin "$SEL"
    fi
    hyprctl keyword animations:enabled false
    hyprctl dispatch movetoworkspacesilent "special:quaketerminal,$SEL"
    hyprctl keyword animations:enabled true
fi
