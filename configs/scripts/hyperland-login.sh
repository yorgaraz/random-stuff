#!/bin/bash

LOGFILE=~/hyprland_headless_debug.log
echo "Attempting to start headless Hyprland..."
echo "Log will be saved to $LOGFILE"

# Start Hyprland, redirecting all output to the log file
WLR_BACKENDS=headless Hyprland &> $LOGFILE &
HYPRLAND_PID=$!

# Give it a moment to initialize or fail
sleep 2

# Check if the process crashed immediately
if ! ps -p $HYPRLAND_PID > /dev/null; then
    echo "Error: Hyprland crashed on startup."
    echo "--- Displaying last 20 lines of the log ---"
    tail -n 200 $LOGFILE
    exit 1
fi

# Check if the process is running but failed to create a display
WAYLAND_DISPLAY_VALUE=$(cat /proc/$HYPRLAND_PID/environ | tr '\0' '\n' | grep -m1 '^WAYLAND_DISPLAY=')
if [ -z "$WAYLAND_DISPLAY_VALUE" ]; then
    echo "Error: Hyprland is running but failed to create a Wayland display."
    echo "This indicates a critical initialization error, likely with the GPU."
    echo "--- Displaying last 20 lines of the log ---"
    tail -n 200 $LOGFILE
    kill $HYPRLAND_PID # Clean up the zombie process
    exit 1
fi

echo "✅ Success! Hyprland is running and ready."
# (The rest of the script to start wayvnc would go here)
