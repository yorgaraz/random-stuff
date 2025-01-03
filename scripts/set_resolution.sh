#!/bin/sh

# Get the primary display
PRIMARY_DISPLAY=$(xrandr | grep ' connected' | grep 'primary' | cut -d ' ' -f1)

# If no primary display is found, just use the first connected one.
if [ -z "$PRIMARY_DISPLAY" ]; then
  PRIMARY_DISPLAY=$(xrandr | grep ' connected' | head -n 1 | cut -d ' ' -f1)
fi

# Check the input parameter and set resolution
if [ "$1" = "4k" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 3840x2160 --rate 60
elif [ "$1" = "1080p" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 1920x1080 --rate 60
elif [ "$1" = "1600x900" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 1600x900 --rate 60
elif [ "$1" = "1366x768" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 1366x768 --rate 60
elif [ "$1" = "900p" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 1600x900 --rate 60
elif [ "$1" = "720p" ]; then
  xrandr --output $PRIMARY_DISPLAY --mode 1280x720 --rate 60
else
  echo "Invalid parameter. Use '4k', '1080p' or '720p'."
fi
