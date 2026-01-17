#!/bin/bash

# Hyprland Grid Setup Script

# 1. Create the main vertical split (40% top row, 60% bottom row)
hyprctl dispatch splitratio 0.4
hyprctl dispatch splitv

# 2. Focus on the top pane to create the columns there
hyprctl dispatch focusmove u

# Create the 30/70 split for the first column
# Formula: 0.3 / (0.3 + 0.7) = 0.3
hyprctl dispatch splitratio 0.3
hyprctl dispatch splith

# Focus on the right pane and create the 40/30 split
# Formula: 0.4 / (0.4 + 0.3) = 0.5714
hyprctl dispatch focusmove r
hyprctl dispatch splitratio 0.5714
hyprctl dispatch splith

# 3. Now focus on the bottom pane to create the columns there
hyprctl dispatch focusmove d

# Create the 30/70 split for the first column
hyprctl dispatch splitratio 0.3
hyprctl dispatch splith

# Focus on the right pane and create the 40/30 split
hyprctl dispatch focusmove r
hyprctl dispatch splitratio 0.5714
hyprctl dispatch splith

# 4. Finally, focus the desired "start" pane (middle bottom)
hyprctl dispatch focusmove l
