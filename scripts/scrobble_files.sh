#!/usr/bin/env bash

# Usage:
#   ./scrobble_files.sh [FIND_OPTIONS...] -- /path/to/directory
#
# Example to exclude __pycache__ directories:
#   ./scrobble_files.sh -not -path "*/__pycache__/*" -- ~/dev/myproject/src/moduleA
#
# Explanation:
#   1. Provide any additional find options before the -- delimiter.
#   2. After --, specify the directory you want to scrobble.
#
# The script then recursively lists all files under the specified directory (minus any exclusions)
# and prints each file’s contents for easy copy/paste reference.

FIND_OPTS=()
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--" ]]; then
    shift
    break
  else
    FIND_OPTS+=("$1")
    shift
  fi
done

if [ -z "$1" ]; then
  echo "Usage: $0 [FIND_OPTIONS...] -- /path/to/directory"
  exit 1
fi

TARGET_DIR="$1"
shift

if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: '$TARGET_DIR' is not a valid directory."
  exit 2
fi

find "$TARGET_DIR" -type f "${FIND_OPTS[@]}" | while read -r file; do
  echo "========================================"
  echo "File: $file"
  echo "========================================"
  cat "$file"
  echo -e "\n"
done

