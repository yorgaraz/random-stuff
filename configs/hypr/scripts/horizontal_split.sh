#!/usr/bin/env bash

# tile_horizontal.sh
# Arranges all non-floating windows on the active workspace horizontally,
# with each window taking up an equal amount of space.

# --- Get Monitor and Workspace Information ---
# We need to find the dimensions and position of the currently focused monitor
# to correctly place and size the windows.
MONITOR_INFO=$(hyprctl -j monitors | jq '.[] | select(.focused == true)')
if [[ -z "$MONITOR_INFO" ]]; then
    echo "Error: Could not retrieve focused monitor information." >&2
    exit 1
fi

MONITOR_WIDTH=$(echo "$MONITOR_INFO" | jq '.width')
MONITOR_HEIGHT=$(echo "$MONITOR_INFO" | jq '.height')
MONITOR_X=$(echo "$MONITOR_INFO" | jq '.x')
MONITOR_Y=$(echo "$MONITOR_INFO" | jq '.y')
WORKSPACE_ID=$(echo "$MONITOR_INFO" | jq '.activeWorkspace.id')

# --- Get Window Information ---
# Filter clients to get only the non-floating windows on the active workspace.
WINDOWS=$(hyprctl -j clients | jq --argjson WORKSPACE_ID "$WORKSPACE_ID" '[.[] | select(.workspace.id == $WORKSPACE_ID and .floating == false)]')

# Count the number of windows to be tiled.
WINDOW_COUNT=$(echo "$WINDOWS" | jq 'length')

# If there are less than 2 windows, there's no need to tile.
if [[ "$WINDOW_COUNT" -lt 2 ]]; then
    exit 0
fi

# --- Calculate Dimensions and Positions ---
# Calculate the width each window should have. We use integer division.
WINDOW_WIDTH=$((MONITOR_WIDTH / WINDOW_COUNT))

# Initialize the starting X position for the first window.
CURRENT_X=$MONITOR_X

# --- Build Dispatch Commands ---
# We will build a single string of commands to be executed in one batch call
# for maximum efficiency.
DISPATCH_COMMANDS=""

# Loop through each window address and generate the corresponding commands.
for i in $(seq 0 $((WINDOW_COUNT - 1))); do
    WINDOW_ADDRESS=$(echo "$WINDOWS" | jq -r ".[$i].address")

    # To avoid small gaps from integer division, the last window fills the remaining space.
    if [[ $i -eq $((WINDOW_COUNT - 1)) ]]; then
        CURRENT_WIDTH=$((MONITOR_X + MONITOR_WIDTH - CURRENT_X))
    else
        CURRENT_WIDTH=$WINDOW_WIDTH
    fi

    # Append commands for the current window to the batch string.
    # 1. Ensure the window is not in fullscreen mode.
    # 2. Move the window to the calculated X and Y position.
    # 3. Resize the window to the calculated width and full monitor height.
    DISPATCH_COMMANDS+="dispatch fullscreen 0,address:$WINDOW_ADDRESS;"
    DISPATCH_COMMANDS+="dispatch movewindowpixel exact $CURRENT_X $MONITOR_Y,address:$WINDOW_ADDRESS;"
    DISPATCH_COMMANDS+="dispatch resizewindowpixel exact $CURRENT_WIDTH $MONITOR_HEIGHT,address:$WINDOW_ADDRESS;"

    # Update the X position for the next window.
    CURRENT_X=$((CURRENT_X + WINDOW_WIDTH))
done

# --- Execute Commands ---
# Run all the generated commands in a single, efficient batch operation.
hyprctl --batch "$DISPATCH_COMMANDS"

