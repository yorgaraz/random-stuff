#!/bin/bash
# Exits Sunshine Mode / Restores the default desktop state

# Restore resolution to 4K AND re-apply the custom reserved area in one batch.
# Keep in sync with configs/hypr/conf.d/01_monitors.conf (addreserved = top, bottom, left, right).
hyprctl --batch "\
    keyword monitor HDMI-A-1,3840x2160@60.000000Hz,0x0,1;\
    keyword monitor HDMI-A-1,addreserved,900,0,500,600"
