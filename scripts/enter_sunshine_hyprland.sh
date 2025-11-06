#!/bin/bash
# Enters Sunshine Mode using ONLY hyprctl for reliability

# Wait 1 second for the compositor to be ready
sleep 1

# Set resolution to 1080p AND remove the reserved area in one batch
hyprctl --batch "\
    keyword monitor HDMI-A-1,1920x1080@60.000000Hz,auto,1;\
    keyword monitor HDMI-A-1,addreserved,0,0,0,0"
