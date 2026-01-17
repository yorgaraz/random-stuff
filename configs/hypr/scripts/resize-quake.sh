#!/bin/sh

DEFAULT_STEP=5
FAST_STEP=20
STEP=$DEFAULT_STEP

if [ "$2" = "fast" ]; then
    STEP=$FAST_STEP
fi

if [ "$1" = "up" ]; then
    hyprctl dispatch resizewindowpixel "0 -$STEP,title:^quake-term$"
elif [ "$1" = "down" ]; then
    hyprctl dispatch resizewindowpixel "0 $STEP,title:^quake-term$"
fi

