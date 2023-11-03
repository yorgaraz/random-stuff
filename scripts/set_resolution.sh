#!/bin/sh

# Get the primary display
PRIMARY_DISPLAY=$(xrandr | grep ' connected' | grep 'primary' | cut -d ' ' -f1)

# If no primary display is found, just use the first connected one.
if [ -z "$PRIMARY_DISPLAY" ]; then
  PRIMARY_DISPLAY=$(xrandr | grep ' connected' | head -n 1 | cut -d ' ' -f1)
fi

# Check the input parameter and set resolution
if [ "$1" = "4k" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 3840x2160
elif [ "$1" = "1080p" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 1920x1080
else
  echo "Invalid parameter. Use '4k' or '1080p'."
fi
