#!/bin/bash
# Exits Sunshine Mode / Restores the default desktop state

# Restore resolution to 4K AND re-apply the custom reserved area in one batch
hyprctl --batch "\
    keyword monitor HDMI-A-1,3840x2160@60.000000Hz,auto,1;\
    keyword monitor HDMI-A-1,addreserved,900,0,600,600"
