#!/bin/bash

# source: https://github.com/milianw/shell-helpers/blob/master/clipboard
# Access your KDE 4 klipper on the command line
# usage:
#  ./clipboard
#    will output current contents of klipper
#  echo "foobar" | ./clipboard
#    will put "foobar" into your clipboard/klipper

if [ ! -z "$(which xclip)" ]; then
  readCmd="xclip -o -selection clipboard"
  writeCmd="xclip -i -selection clipboard"
else
  readCmd="qdbus-qt4 org.kde.klipper /klipper getClipboardContents"
  writeCmd="qdbus-qt4 org.kde.klipper /klipper setClipboardContents"
fi

# check for stdin
if ! test -t 0; then
  # oh, nice - user input! we set that as current clipboard content
  if [ ! -z "$(which xclip)" ]; then
    $writeCmd < /dev/stdin
  else
    $writeCmd "$(</dev/stdin)" > /dev/null
  fi
  exit
fi

# if we reach this point no user input was given and we
# print out the current contents of the clipboard
$readCmd
